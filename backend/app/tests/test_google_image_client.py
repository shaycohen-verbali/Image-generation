from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.core.config import get_settings
from app.services.google_image_client import GoogleImageClient


def _png_bytes() -> bytes:
    image = Image.new("RGBA", (8, 8), color=(255, 0, 0, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class InlineResponseGoogleImageClient(GoogleImageClient):
    def __init__(self, response_json: dict):
        self._response_json = response_json
        super().__init__()

    def _request(self, model_name: str, request_json: dict, *, timeout: int = 300) -> dict:
        return self._response_json


def test_google_generation_spills_inline_asset_to_temp_and_deletes_after_download(tmp_path: Path):
    settings = get_settings()
    settings.runtime_data_root = tmp_path / "runtime_data"
    settings.runtime_data_root.mkdir(parents=True, exist_ok=True)

    payload = _png_bytes()
    response_json = {
        "responseId": "resp_test",
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64.b64encode(payload).decode("utf-8"),
                            }
                        }
                    ]
                }
            }
        ]
    }
    client = InlineResponseGoogleImageClient(response_json)

    result, _model_name = client.generate_stage3(
        "nano-banana-2",
        "prompt",
        run_id="run_test",
        aspect_ratio="1:1",
        image_size="1K",
    )

    inline_url = result["output"][0]
    stored = client._inline_assets[inline_url]
    temp_path = Path(stored["path"])
    assert temp_path.exists()

    image_bytes = client.download_image(inline_url)

    assert image_bytes == payload
    assert not temp_path.exists()
    assert inline_url not in client._inline_assets
    client.close()


def test_google_client_close_removes_orphaned_temp_assets(tmp_path: Path):
    settings = get_settings()
    settings.runtime_data_root = tmp_path / "runtime_data"
    settings.runtime_data_root.mkdir(parents=True, exist_ok=True)

    payload = _png_bytes()
    response_json = {
        "responseId": "resp_orphan",
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64.b64encode(payload).decode("utf-8"),
                            }
                        }
                    ]
                }
            }
        ]
    }
    client = InlineResponseGoogleImageClient(response_json)

    result, _model_name = client.generate_stage3(
        "nano-banana-2",
        "prompt",
        run_id="run_test_close",
        aspect_ratio="1:1",
        image_size="1K",
    )

    inline_url = result["output"][0]
    temp_path = Path(client._inline_assets[inline_url]["path"])
    assert temp_path.exists()

    client.close()

    assert not temp_path.exists()
    assert not client._inline_assets


def test_profile_variant_request_summary_for_teenager_switches_to_photorealistic(tmp_path: Path):
    client = InlineResponseGoogleImageClient({})
    request = client.profile_variant_request_summary(
        tmp_path / "source.jpg",
        word="balance",
        profile_description="older-teen to young-adult woman with White skin",
        target_age="teenager",
        white_background=False,
        aspect_ratio="4:3",
        image_size="1K",
        edit_instruction="Keep the same scene.",
    )

    prompt = request["prompt"]
    assert "photorealistic" in prompt
    assert "must not remain in a cartoon, watercolor, storybook, or illustrated style" in prompt
    assert "keep the same AAC concept, focal action, and concept clarity" in prompt


def test_profile_variant_request_summary_for_kid_keeps_visual_style(tmp_path: Path):
    client = InlineResponseGoogleImageClient({})
    request = client.profile_variant_request_summary(
        tmp_path / "source.jpg",
        word="balance",
        profile_description="school-age girl with White skin",
        target_age="kid",
        white_background=False,
        aspect_ratio="4:3",
        image_size="1K",
        edit_instruction="Keep the same scene.",
    )

    prompt = request["prompt"]
    assert "keep the same AAC concept, visual style, focal action, and concept clarity" in prompt
    assert "must not remain in a cartoon, watercolor, storybook, or illustrated style" not in prompt
