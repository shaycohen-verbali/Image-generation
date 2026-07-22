from app.services.openai_client import OpenAIClient


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
