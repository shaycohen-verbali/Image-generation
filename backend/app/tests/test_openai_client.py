from app.services.openai_client import OpenAIClient


def test_normalize_abstract_rubric_adds_defaults() -> None:
    normalized = OpenAIClient.normalize_abstract_rubric({"score": 88, "failure_tags": "bad"})
    assert normalized["score"] == 88
    assert normalized["contrast_clarity"] == 0
    assert normalized["absence_signal_strength"] == 0
    assert normalized["aac_interpretability"] == 0
    assert normalized["failure_tags"] == []
    assert normalized["explanation"] == ""
