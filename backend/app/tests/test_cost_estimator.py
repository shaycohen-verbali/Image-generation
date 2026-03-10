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
        "stage3_prompt_engineer",
        "stage3_generate",
    ]
    assert result[2]["estimated_cost_usd"] == 0.039


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
