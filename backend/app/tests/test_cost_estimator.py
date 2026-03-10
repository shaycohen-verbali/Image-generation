from app.services.cost_estimator import estimate_stage_cost, summarize_run_costs


def test_estimate_stage_cost_from_openai_responses_usage() -> None:
    result = estimate_stage_cost(
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

    assert result["model"] == "gpt-5.4"
    assert result["provider"] == "openai"
    assert result["estimated_cost_usd"] > 0


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
    assert summary["estimated_total_cost_usd"] == 0.043
    assert summary["estimated_cost_per_image_usd"] == 0.0215
    assert len(summary["stage_costs"]) == 2
