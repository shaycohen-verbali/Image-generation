from __future__ import annotations

import json
from typing import Any


OPENAI_MODEL_RATES_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-5.4": (2.00, 8.00),
}

GEMINI_MODEL_RATES_PER_MILLION: dict[str, tuple[float, float]] = {
    "gemini-3-flash": (0.35, 1.05),
    "gemini-3-pro": (3.50, 10.50),
}

REPLICATE_IMAGE_RATES_USD: dict[str, float] = {
    "black-forest-labs/flux-schnell": 0.003,
    "black-forest-labs/flux-1.1-pro": 0.040,
    "google/imagen-3-fast": 0.030,
    "google/imagen-4": 0.060,
    "google/nano-banana": 0.030,
    "google/nano-banana-2": 0.040,
    "google/nano-banana-pro": 0.060,
}


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _nested(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_openai_usage(response_json: dict[str, Any]) -> tuple[int, int]:
    usage = _nested(response_json, "raw", "raw_response", "usage")
    if not isinstance(usage, dict):
        usage = _nested(response_json, "raw_response", "usage")
    if not isinstance(usage, dict):
        usage = _nested(response_json, "raw", "run_payload", "usage")
    if not isinstance(usage, dict):
        usage = _nested(response_json, "run_payload", "usage")
    if not isinstance(usage, dict):
        return 0, 0

    input_tokens = usage.get("input_tokens")
    if input_tokens is None:
        input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("output_tokens")
    if output_tokens is None:
        output_tokens = usage.get("completion_tokens")
    return int(input_tokens or 0), int(output_tokens or 0)


def _extract_gemini_usage(response_json: dict[str, Any]) -> tuple[int, int]:
    usage = _nested(response_json, "raw", "raw_response", "usageMetadata")
    if not isinstance(usage, dict):
        usage = _nested(response_json, "raw_response", "usageMetadata")
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = usage.get("promptTokenCount") or usage.get("prompt_token_count") or 0
    output_tokens = usage.get("candidatesTokenCount") or usage.get("candidates_token_count") or 0
    return int(input_tokens or 0), int(output_tokens or 0)


def _token_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    normalized = str(model or "").strip().lower()
    if normalized in OPENAI_MODEL_RATES_PER_MILLION:
        input_rate, output_rate = OPENAI_MODEL_RATES_PER_MILLION[normalized]
        return (input_tokens / 1_000_000.0) * input_rate + (output_tokens / 1_000_000.0) * output_rate
    if normalized in GEMINI_MODEL_RATES_PER_MILLION:
        input_rate, output_rate = GEMINI_MODEL_RATES_PER_MILLION[normalized]
        return (input_tokens / 1_000_000.0) * input_rate + (output_tokens / 1_000_000.0) * output_rate
    return 0.0


def estimate_stage_cost(stage_name: str, request_json: dict[str, Any], response_json: dict[str, Any], attempt: int = 0) -> dict[str, Any]:
    stage_name = str(stage_name or "")
    request_json = _json_dict(request_json)
    response_json = _json_dict(response_json)

    provider = _first_text(
        _nested(response_json, "prompt_engineer", "raw", "provider"),
        _nested(response_json, "analysis_raw", "provider"),
        _nested(response_json, "raw", "provider"),
    )
    model = ""
    estimated_cost_usd = 0.0

    if stage_name in {"stage1_prompt", "stage3_prompt_upgrade"}:
        raw = _json_dict(response_json.get("raw"))
        model = _first_text(raw.get("model"), _nested(raw, "run_payload", "model"), _nested(raw, "raw_response", "model"))
        provider = provider or ("google" if str(model).startswith("gemini-") else "openai")
        input_tokens, output_tokens = _extract_gemini_usage(raw) if provider == "google" else _extract_openai_usage({"raw": raw})
        estimated_cost_usd = _token_cost_usd(model, input_tokens, output_tokens)
    elif stage_name == "stage3_upgrade":
        analysis_raw = _json_dict(response_json.get("analysis_raw"))
        prompt_engineer = _json_dict(response_json.get("prompt_engineer"))
        prompt_engineer_raw = _json_dict(prompt_engineer.get("raw"))
        generation = _json_dict(response_json.get("generation"))

        analysis_model = _first_text(
            analysis_raw.get("model"),
            _nested(analysis_raw, "raw_response", "model"),
            request_json.get("critique_model_selected"),
        )
        analysis_provider = _first_text(analysis_raw.get("provider"), "google" if str(analysis_model).startswith("gemini-") else "openai")
        analysis_input_tokens, analysis_output_tokens = (
            _extract_gemini_usage(analysis_raw) if analysis_provider == "google" else _extract_openai_usage(analysis_raw)
        )
        estimated_cost_usd += _token_cost_usd(analysis_model, analysis_input_tokens, analysis_output_tokens)

        prompt_model = _first_text(
            prompt_engineer_raw.get("model"),
            _nested(prompt_engineer_raw, "run_payload", "model"),
            _nested(prompt_engineer_raw, "raw_response", "model"),
        )
        prompt_provider = _first_text(prompt_engineer_raw.get("provider"), "google" if str(prompt_model).startswith("gemini-") else "openai")
        prompt_input_tokens, prompt_output_tokens = (
            _extract_gemini_usage(prompt_engineer_raw) if prompt_provider == "google" else _extract_openai_usage({"raw": prompt_engineer_raw})
        )
        estimated_cost_usd += _token_cost_usd(prompt_model, prompt_input_tokens, prompt_output_tokens)

        model = _first_text(response_json.get("generation_model"), generation.get("model"), response_json.get("generation_model_selected"))
        provider = "replicate"
        estimated_cost_usd += REPLICATE_IMAGE_RATES_USD.get(model, 0.0)

    elif stage_name == "quality_gate":
        raw = _json_dict(response_json.get("analysis_raw") if stage_name == "stage3_upgrade" else response_json.get("raw"))
        model = _first_text(
            raw.get("model"),
            _nested(raw, "raw_response", "model"),
            request_json.get("critique_model_selected"),
            request_json.get("quality_model_selected"),
        )
        provider = provider or _first_text(raw.get("provider"), "google" if str(model).startswith("gemini-") else "openai")
        input_tokens, output_tokens = _extract_gemini_usage(raw) if provider == "google" else _extract_openai_usage(raw)
        estimated_cost_usd = _token_cost_usd(model, input_tokens, output_tokens)
    elif stage_name in {"stage2_draft", "stage4_background"}:
        model = _first_text(response_json.get("model"))
        provider = "replicate"
        estimated_cost_usd = REPLICATE_IMAGE_RATES_USD.get(model, 0.0)
    elif stage_name == "stage3_generate":
        generation = _json_dict(response_json.get("generation"))
        model = _first_text(response_json.get("generation_model"), generation.get("model"), response_json.get("generation_model_selected"))
        provider = "replicate"
        estimated_cost_usd = REPLICATE_IMAGE_RATES_USD.get(model, 0.0)

    return {
        "stage_name": stage_name,
        "attempt": int(attempt or 0),
        "provider": provider or "unknown",
        "model": model,
        "estimated_cost_usd": round(float(estimated_cost_usd), 6),
    }


def summarize_run_costs(stages: list[Any], assets: list[Any]) -> dict[str, Any]:
    stage_costs: list[dict[str, Any]] = []
    total = 0.0

    for stage in stages:
        if isinstance(stage, dict):
            stage_name = stage.get("stage_name", "")
            attempt = int(stage.get("attempt") or 0)
            request_json = _json_dict(stage.get("request_json"))
            response_json = _json_dict(stage.get("response_json"))
        else:
            stage_name = getattr(stage, "stage_name", "")
            attempt = int(getattr(stage, "attempt", 0) or 0)
            request_json = _json_dict(getattr(stage, "request_json", "{}"))
            response_json = _json_dict(getattr(stage, "response_json", "{}"))

        entry = estimate_stage_cost(stage_name, request_json, response_json, attempt)
        stage_costs.append(entry)
        total += float(entry["estimated_cost_usd"])

    image_count = len(assets or [])
    avg = total / image_count if image_count > 0 else None
    return {
        "estimated_total_cost_usd": round(total, 6),
        "estimated_cost_per_image_usd": round(avg, 6) if avg is not None else None,
        "image_count": image_count,
        "stage_costs": stage_costs,
    }
