from app.services.openai_client import OpenAIClient


def test_vision_critique_person_decision_is_independent_and_counterfactual(monkeypatch, tmp_path) -> None:
    client = OpenAIClient()
    captured: dict[str, str] = {}

    def fake_vision_json(*, image_path, prompt, model, temperature):
        captured["prompt"] = prompt
        return ({"person_needed_for_clarity": "no"}, {"raw": "ok"})

    monkeypatch.setattr(client, "_vision_json", fake_vision_json)

    client.analyze_image(
        tmp_path / "abbey.jpg",
        "abbey",
        "noun",
        "religious building",
        "gpt-4o-mini",
        initial_need_person="yes",
        current_render_style_mode="illustration",
    )

    prompt = captured["prompt"]
    assert "if every person were removed" in prompt
    assert "would the intended meaning of the word remain clear" in prompt
    assert "Briefly explain the decision" in prompt
    assert "Concrete subjects normally do not need a person" not in prompt
    assert "quickly and unambiguously" not in prompt
    assert "Current system hypothesis" not in prompt
    assert "Current render style" not in prompt


def test_stage1_gemini_request_can_disable_inner_http_retries(monkeypatch) -> None:
    client = OpenAIClient()
    captured: dict[str, int | None] = {}

    def fake_request(method, url, *, json_body=None, timeout=180, retries=None):
        captured["retries"] = retries
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"first prompt":"a clear bucket","need a person":"no"}'},
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(client, "_request_gemini", fake_request)

    parsed, _ = client.generate_first_prompt(
        "Word: bucket",
        "",
        mode="responses_api",
        responses_model="gemini-3.1-flash-lite",
        request_retries=0,
    )

    assert captured["retries"] == 0
    assert parsed["first prompt"] == "a clear bucket"
