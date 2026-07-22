from app.services.cost_estimator import estimate_stage_costs, summarize_run_costs


def test_estimate_stage_cost_from_openai_responses_usage() -> None:
    result = estimate_stage_costs(
        "stage1_prompt",
        {},
        {
            "raw": {
                "model": "gpt-5.4",
                "raw_response": {
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 250,
                    }
                },
            }
        },
        attempt=0,
    )

    assert len(result) == 1
    assert result[0]["model"] == "gpt-5.4"
    assert result[0]["provider"] == "openai"
    assert result[0]["estimated_cost_usd"] > 0


def test_estimate_stage_cost_uses_current_rates_and_cached_input_discount() -> None:
    result = estimate_stage_costs(
        "stage1_prompt",
        {},
        {
            "raw": {
                "model": "gpt-5.4-mini",
                "raw_response": {
                    "usage": {
                        "input_tokens": 1_000_000,
                        "output_tokens": 1_000_000,
                        "input_tokens_details": {"cached_tokens": 400_000},
                    }
                },
            }
        },
    )

    # $0.75/M uncached input, $0.075/M cached input, and $4.50/M output.
    assert result[0]["estimated_cost_usd"] == 4.98


def test_estimate_stage_cost_uses_current_gemini_flash_lite_rate() -> None:
    result = estimate_stage_costs(
        "quality_gate",
        {},
        {
            "raw": {
                "model": "gemini-3.1-flash-lite",
                "provider": "google",
                "raw_response": {
                    "usageMetadata": {
                        "promptTokenCount": 1_000_000,
                        "candidatesTokenCount": 1_000_000,
                        "cachedContentTokenCount": 200_000,
                    }
                },
            }
        },
    )

    # $0.25/M uncached input, $0.025/M cached input, and $1.50/M output.
    assert result[0]["estimated_cost_usd"] == 1.705


def test_estimate_stage_cost_uses_gemini_flash_lite_image_price() -> None:
    result = estimate_stage_costs(
        "stage4_background",
        {},
        {"model": "gemini-3.1-flash-lite-image"},
    )

    assert result[0]["provider"] == "google"
    assert result[0]["estimated_cost_usd"] == 0.0336


def test_estimate_stage3_upgrade_cost_breakdown() -> None:
    result = estimate_stage_costs(
        "stage3_upgrade",
        {"critique_model_selected": "gpt-4o-mini"},
        {
            "analysis_raw": {
                "model": "gpt-4o-mini",
                "provider": "openai",
                "raw_response": {
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 100,
                    }
                },
            },
            "prompt_engineer": {
                "raw": {
                    "model": "gpt-5.4",
                    "raw_response": {
                        "usage": {
                            "input_tokens": 2000,
                            "output_tokens": 200,
                        }
                    },
                }
            },
            "generation_model": "google/nano-banana-2",
        },
        attempt=1,
    )

    assert [entry["stage_name"] for entry in result] == [
        "stage3_critique",
        "stage3_accessibility_critique",
        "stage3_prompt_engineer",
        "stage3_generate",
    ]
    assert result[3]["estimated_cost_usd"] == 0.039


def test_summarize_run_costs_uses_assets_for_average_per_image() -> None:
    summary = summarize_run_costs(
        [
            {
                "stage_name": "stage2_draft",
                "attempt": 0,
                "request_json": {},
                "response_json": {"model": "black-forest-labs/flux-schnell"},
            },
            {
                "stage_name": "stage4_background",
                "attempt": 1,
                "request_json": {},
                "response_json": {"model": "google/nano-banana-2"},
            },
        ],
        assets=[{"id": "a1"}, {"id": "a2"}],
    )

    assert summary["image_count"] == 2
    assert summary["estimated_total_cost_usd"] == 0.042
    assert summary["estimated_cost_per_image_usd"] == 0.021
    assert len(summary["stage_costs"]) == 2
    assert "estimate_note" in summary


def test_summarize_run_costs_counts_saved_variant_assets_before_stage_summary_exists() -> None:
    summary = summarize_run_costs(
        [
            {
                "stage_name": "stage2_draft",
                "attempt": 0,
                "request_json": {},
                "response_json": {"model": "black-forest-labs/flux-schnell"},
            }
        ],
        assets=[
            {"id": "a1", "stage_name": "stage2_draft", "attempt": 0, "model_name": "black-forest-labs/flux-schnell"},
            {"id": "a2", "stage_name": "stage4_variant_generate", "attempt": 1, "model_name": "google/nano-banana-2"},
            {"id": "a3", "stage_name": "stage4_variant_generate", "attempt": 1, "model_name": "google/nano-banana-2"},
            {"id": "a4", "stage_name": "stage5_variant_white_bg", "attempt": 1, "model_name": "google/nano-banana-2"},
        ],
    )

    assert summary["image_count"] == 4
    assert summary["estimated_total_cost_usd"] == 0.12
    assert summary["estimated_cost_per_image_usd"] == 0.03
    assert any(
        row["stage_name"] == "stage4_variant_generate"
        and row["estimated_cost_usd"] == 0.078
        and row["unit_count"] == 2
        for row in summary["stage_costs"]
    )
    assert any(
        row["stage_name"] == "stage5_variant_white_bg"
        and row["estimated_cost_usd"] == 0.039
        and row["unit_count"] == 1
        for row in summary["stage_costs"]
    )
