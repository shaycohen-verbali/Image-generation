from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any

import requests

from app.core.config import get_settings
from app.services.model_catalog import normalize_stage3_generation_model
from app.services.retry import with_backoff


class ReplicateClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.replicate_cf_base_url:
            raise RuntimeError("REPLICATE_CF_BASE_URL must be configured")

    def _headers(self, *, wait_seconds: int | None = 60) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.replicate_api_token}",
            "Content-Type": "application/json",
        }
        if wait_seconds is not None and int(wait_seconds) > 0:
            headers["Prefer"] = f"wait={int(wait_seconds)}"
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: int = 180,
        wait_seconds: int | None = 60,
    ) -> dict[str, Any]:
        def _call() -> dict[str, Any]:
            response = requests.request(
                method,
                url,
                headers=self._headers(wait_seconds=wait_seconds),
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

    def _create_prediction(
        self,
        model_path: str,
        payload_input: dict[str, Any],
        *,
        wait_seconds: int | None = 60,
        timeout: int = 180,
    ) -> dict[str, Any]:
        url = f"{self.settings.replicate_cf_base_url}/v1/models/{model_path}/predictions"
        return self._request("POST", url, json_body={"input": payload_input}, wait_seconds=wait_seconds, timeout=timeout)

    def get_prediction(self, prediction_id: str) -> dict[str, Any]:
        url = f"{self.settings.replicate_cf_base_url}/v1/predictions/{prediction_id}"
        return self._request("GET", url, timeout=90, wait_seconds=None)

    def _poll_prediction(self, prediction_id: str, max_tries: int = 90, interval: float = 2.0) -> dict[str, Any]:
        url = f"{self.settings.replicate_cf_base_url}/v1/predictions/{prediction_id}"
        for _ in range(max_tries):
            data = self._request("GET", url, timeout=90, wait_seconds=None)
            status = data.get("status")
            if status in {"succeeded", "failed", "canceled"}:
                return data
            time.sleep(interval)
        return {"status": "timeout", "id": prediction_id}

    @staticmethod
    def extract_output_url(pred_json: dict[str, Any]) -> str:
        output = pred_json.get("output")
        if isinstance(output, list) and output:
            return str(output[-1])
        if isinstance(output, str):
            return output
        return ""

    def _run_prediction(self, model_path: str, payload_input: dict[str, Any]) -> dict[str, Any]:
        created = self._create_prediction(model_path, payload_input)
        if created.get("status") in {"succeeded", "failed", "canceled"}:
            return created
        prediction_id = created.get("id")
        if not prediction_id:
            return {"status": "failed", "error": "missing_prediction_id", "raw": created}
        return self._poll_prediction(prediction_id)

    def flux_schnell(self, prompt: str) -> dict[str, Any]:
        return self._run_prediction(
            "black-forest-labs/flux-schnell",
            {"prompt": prompt, "output_format": "jpg"},
        )

    def flux_pro(self, prompt: str) -> dict[str, Any]:
        return self._run_prediction(
            "black-forest-labs/flux-1.1-pro",
            {
                "prompt": prompt,
                "aspect_ratio": "4:3",
                "output_format": "jpg",
                "output_quality": 80,
                "prompt_upsampling": False,
                "safety_tolerance": 2,
                "seed": 10000,
            },
        )

    def imagen_fallback(self, prompt: str) -> dict[str, Any]:
        return self.generate_stage3("imagen-3", prompt)[0]

    def generate_stage3(self, model_choice: str, prompt: str) -> tuple[dict[str, Any], str]:
        model_key = normalize_stage3_generation_model(model_choice)
        model_path, payload = self._stage3_request(model_key, prompt)
        return self._run_prediction(model_path, payload), model_path

    def _stage3_request(self, model_key: str, prompt: str) -> tuple[str, dict[str, Any]]:
        if model_key == "flux-1.1-pro":
            return (
                "black-forest-labs/flux-1.1-pro",
                {
                    "prompt": prompt,
                    "aspect_ratio": "4:3",
                    "output_format": "jpg",
                    "output_quality": 80,
                    "prompt_upsampling": False,
                    "safety_tolerance": 2,
                    "seed": 10000,
                },
            )
        if model_key == "imagen-3":
            return (
                "google/imagen-3-fast",
                {
                    "prompt": prompt,
                    "num_outputs": 1,
                    "aspect_ratio": "4:3",
                    "output_format": "jpg",
                    "output_quality": 80,
                    "prompt_upsampling": True,
                    "safety_tolerance": 2,
                },
            )
        if model_key == "imagen-4":
            return (
                "google/imagen-4",
                {
                    "prompt": prompt,
                    "num_outputs": 1,
                    "aspect_ratio": "4:3",
                    "output_format": "jpg",
                    "output_quality": 80,
                    "prompt_upsampling": True,
                    "safety_tolerance": 2,
                },
            )
        if model_key == "nano-banana":
            return (
                "google/nano-banana",
                {
                    "prompt": prompt,
                    "aspect_ratio": "4:3",
                    "output_format": "jpg",
                },
            )
        if model_key == "nano-banana-2":
            return (
                "google/nano-banana-2",
                {
                    "prompt": prompt,
                    "aspect_ratio": "4:3",
                    "output_format": "jpg",
                },
            )
        if model_key == "nano-banana-pro":
            return (
                "google/nano-banana-pro",
                {
                    "prompt": prompt,
                    "aspect_ratio": "4:3",
                    "output_format": "jpg",
                },
            )

        # Defensive fallback
        return (
            "google/imagen-3-fast",
            {
                "prompt": prompt,
                "num_outputs": 1,
                "aspect_ratio": "4:3",
                "output_format": "jpg",
                "output_quality": 80,
                "prompt_upsampling": True,
                "safety_tolerance": 2,
            },
        )

    @staticmethod
    def _to_data_uri(path: Path) -> str:
        mime, _ = mimetypes.guess_type(path.as_posix())
        if not mime:
            mime = "image/jpeg"
        data = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{data}"

    def nano_banana_white_bg(self, image_path: Path, word: str) -> dict[str, Any]:
        prompt = (
            "remove the background - keep only the important elements of the image and make the background white. "
            f'The image\'s main message is to represent the concept "{word}". '
            "Do not add text in the image."
        )
        image_input = self._to_data_uri(image_path)
        return self._run_prediction(
            "google/nano-banana-2",
            {
                "prompt": prompt,
                "image_input": [image_input],
                "aspect_ratio": "match_input_image",
                "output_format": "jpg",
            },
        )

    def nano_banana_profile_variant(
        self,
        image_path: Path,
        *,
        word: str,
        profile_description: str,
        white_background: bool = False,
    ) -> dict[str, Any]:
        request = self.profile_variant_request_summary(
            image_path,
            word=word,
            profile_description=profile_description,
            white_background=white_background,
        )
        image_input = self._to_data_uri(image_path)
        return self._run_prediction(
            str(request["model_path"]),
            {
                "prompt": str(request["prompt"]),
                "image_input": [image_input],
                "aspect_ratio": str(request["aspect_ratio"]),
                "output_format": str(request["output_format"]),
            },
        )

    def submit_nano_banana_profile_variant(
        self,
        image_path: Path,
        *,
        word: str,
        profile_description: str,
        white_background: bool = False,
    ) -> dict[str, Any]:
        request = self.profile_variant_request_summary(
            image_path,
            word=word,
            profile_description=profile_description,
            white_background=white_background,
        )
        image_input = self._to_data_uri(image_path)
        return self._create_prediction(
            str(request["model_path"]),
            {
                "prompt": str(request["prompt"]),
                "image_input": [image_input],
                "aspect_ratio": str(request["aspect_ratio"]),
                "output_format": str(request["output_format"]),
            },
            wait_seconds=None,
            timeout=30,
        )

    def profile_variant_request_summary(
        self,
        image_path: Path,
        *,
        word: str,
        profile_description: str,
        white_background: bool = False,
    ) -> dict[str, Any]:
        background_instruction = (
            "Keep the background pure solid white and keep the subject cleanly isolated on white."
            if white_background
            else "Keep the same background scene, composition, lighting, and props."
        )
        prompt = (
            "Using the provided image as the base, keep the same AAC concept, visual style, focal action, and concept clarity. "
            f"Change only the main person so the image clearly shows a {profile_description}. "
            "Make the age and gender change visible in the whole body, including height, limb length, torso proportions, and silhouette, not only in the face. "
            f"{background_instruction} Keep exactly one clear central person. "
            f'The image must still clearly represent the concept "{word}" for AAC users. '
            "Do not add text, watermark, or extra people."
        )
        return {
            "model_path": "google/nano-banana-2",
            "prompt": prompt,
            "source_image_path": image_path.as_posix(),
            "white_background": white_background,
            "aspect_ratio": "match_input_image",
            "output_format": "jpg",
        }

    def download_image(self, url: str) -> bytes:
        def _call() -> bytes:
            response = requests.get(url, timeout=180)
            response.raise_for_status()
            return response.content

        return with_backoff(
            _call,
            retries=self.settings.max_api_retries,
            retryable=(requests.RequestException,),
        )
