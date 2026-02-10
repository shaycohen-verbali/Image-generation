from app.services.utils import parse_json_relaxed, sanitize_filename


def test_parse_json_relaxed_handles_fenced_json() -> None:
    text = """```json
    {"first prompt":"A red apple", "need a person":"no"}
    ```"""
    parsed = parse_json_relaxed(text)
    assert parsed["first prompt"] == "A red apple"
    assert parsed["need a person"] == "no"


def test_sanitize_filename_replaces_invalid_chars() -> None:
    value = sanitize_filename('a/b\\c:d*e?f"g<h>i|j')
    assert "/" not in value
    assert "\\" not in value
    assert value
