from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any

import requests

from app.core.config import get_settings
from app.services.retry import with_backoff
from app.services.utils import parse_json_relaxed

OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAIClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _headers(self, assistants_v2: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        if assistants_v2:
            headers["OpenAI-Beta"] = "assistants=v2"
        return headers

    def _request(self, method: str, url: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None, assistants_v2: bool = False, timeout: int = 180) -> dict[str, Any]:
        def _call() -> dict[str, Any]:
            response = requests.request(
                method,
                url,
                headers=self._headers(assistants_v2=assistants_v2),
                params=params,
                json=json_body,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()

        return with_backoff(
            _call,
            retries=self.settings.max_api_retries,
            retryable=(requests.RequestException,),
        )

    def resolve_assistant_id(self, configured_id: str, configured_name: str) -> str:
        if configured_id:
            return configured_id

        after: str | None = None
        while True:
            params = {"limit": 50}
            if after:
                params["after"] = after
            payload = self._request("GET", f"{OPENAI_BASE_URL}/assistants", params=params, assistants_v2=True)
            for item in payload.get("data", []):
                if item.get("name", "").strip().lower() == configured_name.strip().lower():
                    return item["id"]
            after = payload.get("last_id")
            if not after:
                break
        raise RuntimeError(f"Assistant named '{configured_name}' was not found")

    def _create_thread(self, message: str) -> str:
        payload = {"messages": [{"role": "user", "content": message}]}
        data = self._request("POST", f"{OPENAI_BASE_URL}/threads", json_body=payload, assistants_v2=True)
        return data["id"]

    def _create_run(self, thread_id: str, assistant_id: str) -> str:
        payload = {"assistant_id": assistant_id}
        data = self._request(
            "POST",
            f"{OPENAI_BASE_URL}/threads/{thread_id}/runs",
            json_body=payload,
            assistants_v2=True,
        )
        return data["id"]

    def _poll_run(self, thread_id: str, run_id: str, max_wait_seconds: int = 300) -> dict[str, Any]:
        start = time.time()
        while True:
            run = self._request(
                "GET",
                f"{OPENAI_BASE_URL}/threads/{thread_id}/runs/{run_id}",
                assistants_v2=True,
            )
            status = run.get("status")
            if status in {"completed", "failed", "cancelled", "expired"}:
                return run
            if time.time() - start > max_wait_seconds:
                return {"status": "timeout"}
            time.sleep(2)

    def _latest_assistant_text(self, thread_id: str) -> str:
        payload = self._request(
            "GET",
            f"{OPENAI_BASE_URL}/threads/{thread_id}/messages",
            params={"limit": 1, "order": "desc", "role": "assistant"},
            assistants_v2=True,
        )
        items = payload.get("data", [])
        if not items:
            return ""
        texts: list[str] = []
        for part in items[0].get("content", []):
            if part.get("type") == "text":
                texts.append(part["text"]["value"])
        return "\n".join(texts).strip()

    def _assistant_json(self, user_text: str, assistant_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        thread_id = self._create_thread(user_text)
        run_id = self._create_run(thread_id, assistant_id)
        run = self._poll_run(thread_id, run_id)
        if run.get("status") != "completed":
            raise RuntimeError(f"Assistant run status: {run.get('status')}")
        raw_text = self._latest_assistant_text(thread_id)
        parsed = parse_json_relaxed(raw_text)
        return parsed, {"thread_id": thread_id, "run_id": run_id, "raw_text": raw_text}

    def generate_first_prompt(self, user_text: str, assistant_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._assistant_json(user_text=user_text, assistant_id=assistant_id)

    def generate_upgraded_prompt(self, user_text: str, assistant_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._assistant_json(user_text=user_text, assistant_id=assistant_id)

    @staticmethod
    def _to_data_uri(path: Path) -> str:
        mime, _ = mimetypes.guess_type(path.as_posix())
        if not mime:
            mime = "image/jpeg"
        payload = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{payload}"

    def analyze_image(self, image_path: Path, word: str, part_of_sentence: str, category: str, model: str) -> tuple[dict[str, Any], dict[str, Any]]:
        image_data_uri = self._to_data_uri(image_path)
        prompt = (
            "You are an expert AAC visual designer for children. "
            "Analyze the image for concept clarity. Return STRICT JSON with keys "
            '{"challenges":"...", "recommendations":"..."}. '
            f"Concept word: {word}. Part of sentence: {part_of_sentence}. Category: {category}."
        )
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_uri}},
                    ],
                }
            ],
            "temperature": 0.2,
        }
        response = self._request("POST", f"{OPENAI_BASE_URL}/chat/completions", json_body=payload)
        content = response["choices"][0]["message"]["content"]
        return parse_json_relaxed(content), {"raw_response": response, "raw_text": content}

    def score_image(
        self,
        image_path: Path,
        *,
        word: str,
        part_of_sentence: str,
        category: str,
        threshold: int,
        model: str,
        abstract_mode: bool = False,
        contrast_subject: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        image_data_uri = self._to_data_uri(image_path)
        if abstract_mode:
            prompt = (
                "Score this AAC image for an abstract/ambiguous concept. Return STRICT JSON with fields: "
                '{"score":0-100, "contrast_clarity":0-5, "absence_signal_strength":0-5, "aac_interpretability":0-5, '
                '"explanation":"...", "failure_tags":["ambiguity","clutter","wrong_concept","text_in_image","distracting_details"]}. '
                f"Word: {word}. Part of sentence: {part_of_sentence}. Category: {category}. "
                f"Contrast subject: {contrast_subject}. "
                f"Pass threshold is {threshold}."
            )
        else:
            prompt = (
                "Score the AAC concept image quality for a child user. Return STRICT JSON with fields: "
                '{"score":0-100, "explanation":"...", "failure_tags":["ambiguity","clutter","wrong_concept","text_in_image","distracting_details"]}. '
                f"Word: {word}. Part of sentence: {part_of_sentence}. Category: {category}. "
                f"Pass threshold is {threshold}."
            )
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_uri}},
                    ],
                }
            ],
            "temperature": 0.1,
        }
        response = self._request("POST", f"{OPENAI_BASE_URL}/chat/completions", json_body=payload)
        content = response["choices"][0]["message"]["content"]
        parsed = parse_json_relaxed(content)
        if abstract_mode:
            parsed = self.normalize_abstract_rubric(parsed)
        elif "score" not in parsed:
            parsed["score"] = 0
        return parsed, {"raw_response": response, "raw_text": content}

    @staticmethod
    def normalize_abstract_rubric(parsed: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(parsed)
        normalized["score"] = float(normalized.get("score", 0) or 0)
        normalized["contrast_clarity"] = float(normalized.get("contrast_clarity", 0) or 0)
        normalized["absence_signal_strength"] = float(normalized.get("absence_signal_strength", 0) or 0)
        normalized["aac_interpretability"] = float(normalized.get("aac_interpretability", 0) or 0)
        if not isinstance(normalized.get("failure_tags"), list):
            normalized["failure_tags"] = []
        if "explanation" not in normalized:
            normalized["explanation"] = ""
        return normalized
