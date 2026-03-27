from __future__ import annotations

import json
from collections import Counter
from typing import Any


# Official pricing references checked on 2026-03-27:
# - OpenAI: https://openai.com/api/pricing/ and https://platform.openai.com/pricing
# - Gemini API: https://ai.google.dev/gemini-api/docs/pricing
# - Vertex AI Imagen: https://cloud.google.com/vertex-ai/generative-ai/pricing#imagen-models
# Image-generation prices are still estimates because provider invoices are not returned in run payloads.
OPENAI_MODEL_RATES_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.4-mini": (0.25, 2.00),
    "gpt-5.4-nano": (0.05, 0.40),
}

GEMINI_MODEL_RATES_PER_MILLION: dict[str, tuple[float, float]] = {
    "gemini-3-flash": (0.50, 3.00),
    "gemini-3-pro": (2.00, 12.00),
}

REPLICATE_IMAGE_RATES_USD: dict[str, float] = {
    "black-forest-labs/flux-schnell": 0.003,
    "black-forest-labs/flux-1.1-pro": 0.040,
    "google/imagen-3-fast": 0.030,
    "google/imagen-4": 0.040,
    "google/nano-banana": 0.039,
    "google/nano-banana-2": 0.039,
    "google/nano-banana-pro": 0.134,
    "gemini-2.5-flash-image": 0.039,
    "gemini-3.1-flash-image-preview": 0.039,
    "gemini-3-pro-image-preview": 0.134,
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
        if normalized == "gemini-3-pro" and input_tokens > 200_000:
            input_rate, output_rate = 4.00, 18.00
        return (input_tokens / 1_000_000.0) * input_rate + (output_tokens / 1_000_000.0) * output_rate
    return 0.0


def _cost_entry(
    *,
    stage_name: str,
    stage_label: str,
    attempt: int,
    provider: str,
    model: str,
    estimated_cost_usd: float,
    estimate_basis: str,
    unit_count: int = 1,
) -> dict[str, Any]:
    return {
        "stage_name": stage_name,
        "stage_label": stage_label,
        "attempt": int(attempt or 0),
        "provider": provider or "unknown",
        "model": model,
        "estimated_cost_usd": round(float(estimated_cost_usd), 6),
        "estimate_basis": estimate_basis,
        "unit_count": int(unit_count or 1),
    }


def estimate_stage_costs(stage_name: str, request_json: dict[str, Any], response_json: dict[str, Any], attempt: int = 0) -> list[dict[str, Any]]:
    stage_name = str(stage_name or "")
    request_json = _json_dict(request_json)
    response_json = _json_dict(response_json)

    provider = _first_text(
        _nested(response_json, "prompt_engineer", "raw", "provider"),
        _nested(response_json, "analysis_raw", "provider"),
        _nested(response_json, "raw", "provider"),
    )

    if stage_name in {"stage1_prompt", "stage3_prompt_upgrade"}:
        raw = _json_dict(response_json.get("raw"))
        model = _first_text(raw.get("model"), _nested(raw, "run_payload", "model"), _nested(raw, "raw_response", "model"))
        provider = provider or ("google" if str(model).startswith("gemini-") else "openai")
        input_tokens, output_tokens = _extract_gemini_usage(raw) if provider == "google" else _extract_openai_usage({"raw": raw})
        estimated_cost_usd = _token_cost_usd(model, input_tokens, output_tokens)
        label = "Stage 1 Prompt Engineer" if stage_name == "stage1_prompt" else "Stage 3.2 Prompt Engineer"
        return [
            _cost_entry(
                stage_name=stage_name,
                stage_label=label,
                attempt=attempt,
                provider=provider,
                model=model,
                estimated_cost_usd=estimated_cost_usd,
                estimate_basis="official token pricing",
            )
        ]

    if stage_name == "stage3_upgrade":
        analysis_raw = _json_dict(response_json.get("analysis_raw"))
        anatomy_analysis_raw = _json_dict(response_json.get("anatomy_analysis_raw"))
        accessibility_analysis_raw = _json_dict(response_json.get("accessibility_analysis_raw"))
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
        prompt_model = _first_text(
            prompt_engineer_raw.get("model"),
            _nested(prompt_engineer_raw, "run_payload", "model"),
            _nested(prompt_engineer_raw, "raw_response", "model"),
        )
        prompt_provider = _first_text(prompt_engineer_raw.get("provider"), "google" if str(prompt_model).startswith("gemini-") else "openai")
        prompt_input_tokens, prompt_output_tokens = (
            _extract_gemini_usage(prompt_engineer_raw) if prompt_provider == "google" else _extract_openai_usage({"raw": prompt_engineer_raw})
        )
        generation_model = _first_text(response_json.get("generation_model"), generation.get("model"), response_json.get("generation_model_selected"))
        generation_provider = "google" if generation_model.startswith("gemini-") else "replicate"

        entries = [
            _cost_entry(
                stage_name="stage3_critique",
                stage_label="Stage 3.1 Critique",
                attempt=attempt,
                provider=analysis_provider,
                model=analysis_model,
                estimated_cost_usd=_token_cost_usd(analysis_model, analysis_input_tokens, analysis_output_tokens),
                estimate_basis="official token pricing",
            )
        ]

        if anatomy_analysis_raw:
            anatomy_model = _first_text(
                anatomy_analysis_raw.get("model"),
                _nested(anatomy_analysis_raw, "raw_response", "model"),
                request_json.get("anatomy_critique_model_selected"),
            )
            anatomy_provider = _first_text(anatomy_analysis_raw.get("provider"), "google" if str(anatomy_model).startswith("gemini-") else "openai")
            anatomy_input_tokens, anatomy_output_tokens = (
                _extract_gemini_usage(anatomy_analysis_raw)
                if anatomy_provider == "google"
                else _extract_openai_usage(anatomy_analysis_raw)
            )
            entries.append(
                _cost_entry(
                    stage_name="stage3_anatomy_critique",
                    stage_label="Stage 3.15 Anatomy Critique",
                    attempt=attempt,
                    provider=anatomy_provider,
                    model=anatomy_model,
                    estimated_cost_usd=_token_cost_usd(anatomy_model, anatomy_input_tokens, anatomy_output_tokens),
                    estimate_basis="official token pricing",
                )
            )

        accessibility_model = _first_text(
            accessibility_analysis_raw.get("model"),
            _nested(accessibility_analysis_raw, "raw_response", "model"),
            request_json.get("accessibility_critique_model_selected"),
        )
        accessibility_provider = _first_text(
            accessibility_analysis_raw.get("provider"),
            "google" if str(accessibility_model).startswith("gemini-") else "openai",
        )
        accessibility_input_tokens, accessibility_output_tokens = (
            _extract_gemini_usage(accessibility_analysis_raw)
            if accessibility_provider == "google"
            else _extract_openai_usage(accessibility_analysis_raw)
        )
        entries.extend(
            [
                _cost_entry(
                    stage_name="stage3_accessibility_critique",
                    stage_label="Stage 3.16 Accessibility Critique",
                    attempt=attempt,
                    provider=accessibility_provider,
                    model=accessibility_model,
                    estimated_cost_usd=_token_cost_usd(accessibility_model, accessibility_input_tokens, accessibility_output_tokens),
                    estimate_basis="official token pricing",
                ),
                _cost_entry(
                    stage_name="stage3_prompt_engineer",
                    stage_label="Stage 3.2 Prompt Engineer",
                    attempt=attempt,
                    provider=prompt_provider,
                    model=prompt_model,
                    estimated_cost_usd=_token_cost_usd(prompt_model, prompt_input_tokens, prompt_output_tokens),
                    estimate_basis="official token pricing",
                ),
                _cost_entry(
                    stage_name="stage3_generate",
                    stage_label="Stage 3.3 Image Generation",
                    attempt=attempt,
                    provider=generation_provider,
                    model=generation_model,
                    estimated_cost_usd=REPLICATE_IMAGE_RATES_USD.get(generation_model, 0.0),
                    estimate_basis="provider image-price estimate",
                ),
            ]
        )
        return entries

    if stage_name == "quality_gate":
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
        return [
            _cost_entry(
                stage_name=stage_name,
                stage_label="Quality Gate",
                attempt=attempt,
                provider=provider,
                model=model,
                estimated_cost_usd=estimated_cost_usd,
                estimate_basis="official token pricing",
            )
        ]

    if stage_name in {"stage2_draft", "stage4_background"}:
        model = _first_text(response_json.get("model"))
        provider = "google" if model.startswith("gemini-") else "replicate"
        estimated_cost_usd = REPLICATE_IMAGE_RATES_USD.get(model, 0.0)
        label = "Stage 2 Draft Generation" if stage_name == "stage2_draft" else "Stage 4 White Background"
        return [
            _cost_entry(
                stage_name=stage_name,
                stage_label=label,
                attempt=attempt,
                provider=provider,
                model=model,
                estimated_cost_usd=estimated_cost_usd,
                estimate_basis="provider image-price estimate",
            )
        ]

    if stage_name in {"stage4_variant_generate", "stage5_variant_white_bg"}:
        model = _first_text(response_json.get("model"), "gemini-3.1-flash-image-preview")
        provider = "google" if model.startswith("gemini-") else "replicate"
        variants = response_json.get("variants")
        variant_count = int(response_json.get("variant_count") or 0)
        if variant_count <= 0:
            variant_count = len(variants) if isinstance(variants, list) else 0
        if variant_count <= 0:
            return []
        estimated_cost_usd = REPLICATE_IMAGE_RATES_USD.get(model, 0.0) * variant_count
        label = "Character Variant Final Images" if stage_name == "stage4_variant_generate" else "Character Variant White Background"
        return [
            _cost_entry(
                stage_name=stage_name,
                stage_label=label,
                attempt=attempt,
                provider=provider,
                model=model,
                estimated_cost_usd=estimated_cost_usd,
                estimate_basis="provider image-price estimate",
                unit_count=variant_count,
            )
        ]

    if stage_name == "stage4_variant_critique":
        profiles = response_json.get("profiles")
        if not isinstance(profiles, list):
            return []
        total_cost = 0.0
        model_name = _first_text(request_json.get("model_selected"))
        provider_name = "google" if model_name.startswith("gemini-") else "openai"
        reviewed_count = 0
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            critique = profile.get("critique") or {}
            if isinstance(critique, dict) and critique.get("review_skipped"):
                continue
            critique_raw = _json_dict(profile.get("critique_raw"))
            row_model = _first_text(
                critique_raw.get("model"),
                _nested(critique_raw, "raw_response", "model"),
                model_name,
            )
            row_provider = _first_text(
                critique_raw.get("provider"),
                "google" if str(row_model).startswith("gemini-") else "openai",
            )
            input_tokens, output_tokens = (
                _extract_gemini_usage(critique_raw)
                if row_provider == "google"
                else _extract_openai_usage(critique_raw)
            )
            total_cost += _token_cost_usd(row_model, input_tokens, output_tokens)
            if row_model:
                model_name = row_model
            provider_name = row_provider
            reviewed_count += 1
        return [
            _cost_entry(
                stage_name=stage_name,
                stage_label="Step 8.1 Variant Critique",
                attempt=attempt,
                provider=provider_name,
                model=model_name,
                estimated_cost_usd=total_cost,
                estimate_basis="official token pricing",
                unit_count=reviewed_count,
            )
        ] if reviewed_count > 0 else []

    if stage_name == "stage4_variant_correction":
        profiles = response_json.get("profiles")
        corrected_count = 0
        if isinstance(profiles, list):
            corrected_count = len([row for row in profiles if isinstance(row, dict) and not row.get("skipped")])
        if corrected_count <= 0:
            corrected_count = int(request_json.get("corrected_count") or 0)
        model = _first_text(request_json.get("model_selected"), "nano-banana-2")
        if corrected_count <= 0:
            return []
        return [
            _cost_entry(
                stage_name=stage_name,
                stage_label="Step 8.2 Variant Correction",
                attempt=attempt,
                provider="google" if model.startswith("gemini-") else "replicate",
                model=model,
                estimated_cost_usd=REPLICATE_IMAGE_RATES_USD.get(model, 0.0) * corrected_count,
                estimate_basis="provider image-price estimate",
                unit_count=corrected_count,
            )
        ]

    if stage_name == "stage3_generate":
        generation = _json_dict(response_json.get("generation"))
        model = _first_text(response_json.get("generation_model"), generation.get("model"), response_json.get("generation_model_selected"))
        provider = "google" if model.startswith("gemini-") else "replicate"
        estimated_cost_usd = REPLICATE_IMAGE_RATES_USD.get(model, 0.0)
        return [
            _cost_entry(
                stage_name=stage_name,
                stage_label="Stage 3.3 Image Generation",
                attempt=attempt,
                provider=provider,
                model=model,
                estimated_cost_usd=estimated_cost_usd,
                estimate_basis="provider image-price estimate",
            )
        ]

    return []


def summarize_run_costs(stages: list[Any], assets: list[Any]) -> dict[str, Any]:
    stage_costs: list[dict[str, Any]] = []
    total = 0.0
    counted_variant_units: Counter[tuple[str, int]] = Counter()

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

        entries = estimate_stage_costs(stage_name, request_json, response_json, attempt)
        stage_costs.extend(entries)
        total += sum(float(entry["estimated_cost_usd"]) for entry in entries)
        if stage_name in {"stage4_variant_generate", "stage5_variant_white_bg"}:
            variants = response_json.get("variants")
            variant_count = int(response_json.get("variant_count") or 0)
            if variant_count <= 0:
                variant_count = len(variants) if isinstance(variants, list) else 0
            counted_variant_units[(stage_name, attempt)] += variant_count

    asset_list = list(assets or [])
    variant_assets_by_stage_attempt: dict[tuple[str, int], list[Any]] = {}
    for asset in asset_list:
        if isinstance(asset, dict):
            stage_name = str(asset.get("stage_name", ""))
            attempt = int(asset.get("attempt") or 0)
            model_name = str(asset.get("model_name") or "")
        else:
            stage_name = str(getattr(asset, "stage_name", "") or "")
            attempt = int(getattr(asset, "attempt", 0) or 0)
            model_name = str(getattr(asset, "model_name", "") or "")
        if stage_name not in {"stage4_variant_generate", "stage5_variant_white_bg"}:
            continue
        variant_assets_by_stage_attempt.setdefault((stage_name, attempt), []).append(asset)

    for key, stage_assets in variant_assets_by_stage_attempt.items():
        stage_name, attempt = key
        actual_count = len(stage_assets)
        missing_count = actual_count - counted_variant_units[key]
        if missing_count <= 0:
            continue
        first_asset = stage_assets[0]
        if isinstance(first_asset, dict):
            model_name = str(first_asset.get("model_name") or "gemini-3.1-flash-image-preview")
        else:
            model_name = str(getattr(first_asset, "model_name", "") or "gemini-3.1-flash-image-preview")
        unit_price = REPLICATE_IMAGE_RATES_USD.get(model_name, 0.0)
        estimated_cost_usd = unit_price * missing_count
        label = "Character Variant Final Images" if stage_name == "stage4_variant_generate" else "Character Variant White Background"
        stage_costs.append(
            {
                "stage_name": stage_name,
                "stage_label": label,
                "attempt": attempt,
                "provider": "google" if model_name.startswith("gemini-") else "replicate",
                "model": model_name,
                "estimated_cost_usd": round(float(estimated_cost_usd), 6),
                "estimate_basis": "provider image-price estimate from saved variant assets",
                "unit_count": missing_count,
            }
        )
        total += estimated_cost_usd

    image_count = len(asset_list)
    avg = total / image_count if image_count > 0 else None
    return {
        "estimated_total_cost_usd": round(total, 6),
        "estimated_cost_per_image_usd": round(avg, 6) if avg is not None else None,
        "image_count": image_count,
        "stage_costs": stage_costs,
        "estimate_note": "Estimated from official OpenAI, Gemini, and Google Imagen pricing checked on 2026-03-09. Replicate-wrapped image steps are mapped to the closest published provider pricing, not invoice totals.",
    }
