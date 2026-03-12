from __future__ import annotations

import base64
import mimetypes
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

from app.core.config import get_settings
from app.services.model_catalog import google_image_model_name, normalize_stage3_generation_model
from app.services.retry import with_backoff

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GoogleImageAPIError(RuntimeError):
    def __init__(self, message: str, *, request_json: dict[str, Any], response_json: dict[str, Any]) -> None:
        super().__init__(message)
        self.request_json = request_json
        self.response_json = response_json


class GoogleImageClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._prediction_executor = ThreadPoolExecutor(max_workers=max(1, min(int(self.settings.max_parallel_runs or 1), 2)))
        self._prediction_futures: dict[str, Future[dict[str, Any]]] = {}
        self._prediction_models: dict[str, str] = {}
        self._inline_assets: dict[str, bytes] = {}
        self._lock = threading.Lock()

    @classmethod
    def _sanitize_payload(cls, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                if key in {"inlineData", "inline_data"} and isinstance(item, dict):
                    data = str(item.get("data") or "").strip()
                    sanitized[key] = {
                        **{k: v for k, v in item.items() if k != "data"},
                        "data": f"<redacted base64; chars={len(data)}>",
                    }
                    continue
                sanitized[key] = cls._sanitize_payload(item)
            return sanitized
        if isinstance(value, list):
            return [cls._sanitize_payload(item) for item in value]
        return value

    def _request(self, model_name: str, request_json: dict[str, Any], *, timeout: int = 300) -> dict[str, Any]:
        if not self.settings.google_api_key:
            raise GoogleImageAPIError(
                "GOOGLE_API_KEY is required when using Google image models",
                request_json={
                    "model": model_name,
                    "url": f"{GOOGLE_BASE_URL}/models/{model_name}:generateContent",
                    "json_body": self._sanitize_payload(request_json),
                    "timeout": timeout,
                },
                response_json={},
            )

        url = f"{GOOGLE_BASE_URL}/models/{model_name}:generateContent"

        def _call() -> dict[str, Any]:
            response = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                params={"key": self.settings.google_api_key},
                json=request_json,
                timeout=timeout,
            )
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise GoogleImageAPIError(
                    f"Google image API HTTP {response.status_code}: {response.text[:1000]}",
                    request_json={
                        "method": "POST",
                        "url": url,
                        "json_body": self._sanitize_payload(request_json),
                        "timeout": timeout,
                        "model": model_name,
                    },
                    response_json={
                        "status_code": response.status_code,
                        "text": response.text[:4000],
                    },
                ) from exc
            return response.json()

        return with_backoff(
            _call,
            retries=self.settings.max_api_retries,
            retryable=(requests.RequestException,),
        )

    @staticmethod
    def _text_part(text: str) -> dict[str, Any]:
        return {"text": str(text)}

    @staticmethod
    def _inline_part(image_path: Path) -> dict[str, Any]:
        mime_type, _ = mimetypes.guess_type(image_path.as_posix())
        mime_type = mime_type or "image/jpeg"
        data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        return {"inlineData": {"mimeType": mime_type, "data": data}}

    @staticmethod
    def _response_text(response_json: dict[str, Any]) -> str:
        texts: list[str] = []
        for candidate in response_json.get("candidates", []):
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            for part in content.get("parts", []):
                if isinstance(part, dict) and part.get("text"):
                    texts.append(str(part["text"]))
        return "\n".join(texts).strip()

    @staticmethod
    def _response_inline_image(response_json: dict[str, Any]) -> tuple[bytes, str] | None:
        for candidate in response_json.get("candidates", []):
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            for part in content.get("parts", []):
                if not isinstance(part, dict):
                    continue
                inline_data = part.get("inlineData")
                if not isinstance(inline_data, dict):
                    inline_data = part.get("inline_data")
                if not isinstance(inline_data, dict):
                    continue
                data = str(inline_data.get("data") or "").strip()
                if not data:
                    continue
                mime_type = str(inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png")
                return base64.b64decode(data), mime_type
        return None

    @staticmethod
    def _generation_config(*, aspect_ratio: str | None, image_size: str | None) -> dict[str, Any]:
        config: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        image_config: dict[str, Any] = {}
        if aspect_ratio:
            image_config["aspectRatio"] = aspect_ratio
        if image_size:
            image_config["imageSize"] = image_size
        if image_config:
            config["imageConfig"] = image_config
        return config

    def _build_request(
        self,
        *,
        prompt: str,
        image_paths: list[Path] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> dict[str, Any]:
        parts = [self._text_part(prompt)]
        for image_path in image_paths or []:
            parts.append(self._inline_part(image_path))
        return {
            "contents": [{"parts": parts}],
            "generationConfig": self._generation_config(aspect_ratio=aspect_ratio, image_size=image_size),
        }

    def _run_generation(
        self,
        *,
        model_name: str,
        prompt: str,
        image_paths: list[Path] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        timeout: int = 300,
    ) -> dict[str, Any]:
        request_json = self._build_request(
            prompt=prompt,
            image_paths=image_paths,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
        )
        response_json = self._request(model_name, request_json, timeout=timeout)
        image_payload = self._response_inline_image(response_json)
        text_output = self._response_text(response_json)
        sanitized_request_json = self._sanitize_payload(request_json)
        sanitized_response_json = self._sanitize_payload(response_json)
        prediction_id = str(response_json.get("responseId") or response_json.get("response_id") or uuid.uuid4().hex)
        if image_payload is None:
            return {
                "status": "failed",
                "id": prediction_id,
                "model": model_name,
                "provider": "google",
                "error": "no_inline_image_in_response",
                "text_output": text_output,
                "request_json": sanitized_request_json,
                "response_json": sanitized_response_json,
            }

        image_bytes, mime_type = image_payload
        inline_url = f"google-inline://{prediction_id}"
        with self._lock:
            self._inline_assets[inline_url] = image_bytes
        return {
            "status": "succeeded",
            "id": prediction_id,
            "model": model_name,
            "provider": "google",
            "mime_type": mime_type,
            "output": [inline_url],
            "text_output": text_output,
            "request_json": sanitized_request_json,
            "response_json": sanitized_response_json,
        }

    def generate_stage3(
        self,
        model_choice: str,
        prompt: str,
        *,
        aspect_ratio: str | None,
        image_size: str | None,
    ) -> tuple[dict[str, Any], str]:
        selected = normalize_stage3_generation_model(model_choice)
        model_name = google_image_model_name(selected)
        return self._run_generation(
            model_name=model_name,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
        ), model_name

    def nano_banana_white_bg(
        self,
        image_path: Path,
        word: str,
        *,
        aspect_ratio: str | None,
        image_size: str | None,
    ) -> dict[str, Any]:
        prompt = (
            "remove the background and replace it with pure solid white. Keep the exact same character identity, face, hairstyle, clothing, pose, ball position, scale, and camera framing. "
            "Do not redraw the subject, do not change the avatar, and do not add or remove body parts or props. Keep the full body and the full ball entirely inside the frame with clean margin on every side. "
            "There must be exactly one person in the image. "
            "Keep only the important elements of the image and make the background white. "
            f'The image\'s main message is to represent the concept "{word}". '
            "Do not add text in the image."
        )
        return self._run_generation(
            model_name=google_image_model_name("nano-banana-2"),
            prompt=prompt,
            image_paths=[image_path],
            aspect_ratio=aspect_ratio,
            image_size=image_size,
        )

    def profile_variant_request_summary(
        self,
        image_path: Path,
        *,
        word: str,
        profile_description: str,
        white_background: bool = False,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        edit_instruction: str = "",
    ) -> dict[str, Any]:
        background_instruction = (
            "Keep the background pure solid white and keep the subject cleanly isolated on white."
            if white_background
            else "Keep the same background scene, composition, lighting, and props."
        )
        prompt = (
            "Using the provided image as the base, keep the same AAC concept, visual style, focal action, and concept clarity. "
            f"{edit_instruction.strip()} "
            f"Change only the main person so the image clearly shows a {profile_description}. "
            "Make the age and gender change visible in the whole body, including height, limb length, torso proportions, silhouette, and head size, not only in the face. "
            "When the person's age changes, make nearby objects scale appropriately relative to the person's body so the scene still reads naturally. "
            f"{background_instruction} Keep exactly one clear central person. "
            "Keep the same single avatar identity across matching final and white-background outputs; do not invent a different child. "
            "Preserve the same pose, action, clothing color palette, soccer ball position, and overall composition unless a small recentering adjustment is needed to avoid cropping. "
            "Keep the full body and the full soccer ball completely inside the frame with visible margin on all sides; do not crop the right edge, left edge, top, or bottom. "
            "Do not create duplicate people, extra limbs, or multiple similar girls/boys. "
            "If the requested person is female, the output must clearly read as female at a glance; if the requested person is male, the output must clearly read as male at a glance. "
            f'The image must still clearly represent the concept "{word}" for AAC users. '
            "Do not add text, watermark, or extra people."
        )
        return {
            "model": "nano-banana-2",
            "provider_model": google_image_model_name("nano-banana-2"),
            "prompt": prompt,
            "source_image_path": image_path.as_posix(),
            "white_background": white_background,
            "aspect_ratio": aspect_ratio or "",
            "image_size": image_size or "",
        }

    def submit_nano_banana_profile_variant(
        self,
        image_path: Path,
        *,
        word: str,
        profile_description: str,
        white_background: bool = False,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        edit_instruction: str = "",
    ) -> dict[str, Any]:
        prediction_id = f"google_pred_{uuid.uuid4().hex}"
        model_name = google_image_model_name("nano-banana-2")
        future = self._prediction_executor.submit(
            self._run_generation,
            model_name=model_name,
            prompt=str(
                self.profile_variant_request_summary(
                    image_path,
                    word=word,
                    profile_description=profile_description,
                    white_background=white_background,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                    edit_instruction=edit_instruction,
                )["prompt"]
            ),
            image_paths=[image_path],
            aspect_ratio=aspect_ratio,
            image_size=image_size,
        )
        with self._lock:
            self._prediction_futures[prediction_id] = future
            self._prediction_models[prediction_id] = model_name
        return {"id": prediction_id, "status": "processing", "model": model_name, "provider": "google"}

    def get_prediction(self, prediction_id: str) -> dict[str, Any]:
        with self._lock:
            future = self._prediction_futures.get(prediction_id)
            model_name = self._prediction_models.get(prediction_id, google_image_model_name("nano-banana-2"))
        if future is None:
            return {"id": prediction_id, "status": "failed", "error": "unknown_prediction_id", "model": model_name, "provider": "google"}
        if not future.done():
            return {"id": prediction_id, "status": "processing", "model": model_name, "provider": "google"}
        with self._lock:
            self._prediction_futures.pop(prediction_id, None)
            self._prediction_models.pop(prediction_id, None)
        try:
            result = future.result()
        except GoogleImageAPIError as exc:
            return {
                "id": prediction_id,
                "status": "failed",
                "model": model_name,
                "provider": "google",
                "error": str(exc),
                "request_json": exc.request_json,
                "response_json": exc.response_json,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "id": prediction_id,
                "status": "failed",
                "model": model_name,
                "provider": "google",
                "error": str(exc),
            }
        return {**result, "id": prediction_id}

    def download_image(self, url: str) -> bytes:
        with self._lock:
            image_bytes = self._inline_assets.pop(url, None)
        if image_bytes is None:
            raise GoogleImageAPIError(
                f"Missing inline Google image asset for {url}",
                request_json={"url": url},
                response_json={},
            )
        return image_bytes

    def clear_transient_state(self) -> None:
        with self._lock:
            self._prediction_futures.clear()
            self._prediction_models.clear()
            self._inline_assets.clear()

    def close(self) -> None:
        self.clear_transient_state()
        self._prediction_executor.shutdown(wait=False, cancel_futures=True)
