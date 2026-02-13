from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from app.services.pipeline import PipelineRunner
from app.services.repository import Repository


class MockOpenAI:
    def __init__(self, scores: list[int]):
        self._scores = scores
        self._score_idx = 0
        self._upgrade_idx = 0

    def resolve_assistant_id(self, configured_id: str, configured_name: str) -> str:
        return configured_id or "asst_test"

    def generate_first_prompt(self, user_text: str, assistant_id: str):
        return {"first prompt": "simple concept image", "need a person": "no"}, {"raw_text": "ok"}

    def analyze_image(self, image_path: Path, word: str, part_of_sentence: str, category: str, model: str):
        return {"challenges": "too generic", "recommendations": "increase clarity"}, {"raw_text": "ok"}

    def generate_upgraded_prompt(self, user_text: str, assistant_id: str):
        self._upgrade_idx += 1
        return {"upgraded prompt": f"upgraded prompt {self._upgrade_idx}"}, {"raw_text": "ok"}

    def score_image(self, image_path: Path, *, word: str, part_of_sentence: str, category: str, threshold: int, model: str):
        score = self._scores[min(self._score_idx, len(self._scores) - 1)]
        self._score_idx += 1
        return (
            {
                "score": score,
                "explanation": f"score {score}",
                "failure_tags": [] if score >= threshold else ["ambiguity"],
            },
            {"raw_text": "ok"},
        )


class MockReplicate:
    def __init__(
        self,
        *,
        stage2_failures_before_success: int = 0,
        flux_fail_attempts: set[int] | None = None,
        nano_fail_attempts: set[int] | None = None,
    ):
        self.stage2_failures_before_success = stage2_failures_before_success
        self.stage2_calls = 0
        self.stage3_calls = 0
        self.stage4_calls = 0
        self.flux_fail_attempts = flux_fail_attempts or set()
        self.nano_fail_attempts = nano_fail_attempts or set()
        self.imagen_calls = 0

    def flux_schnell(self, prompt: str):
        self.stage2_calls += 1
        if self.stage2_calls <= self.stage2_failures_before_success:
            return {"status": "failed", "id": "pred_s2_failed"}
        return {"status": "succeeded", "id": "pred_s2", "output": "http://mock/stage2.jpg"}

    def flux_pro(self, prompt: str):
        self.stage3_calls += 1
        if self.stage3_calls in self.flux_fail_attempts:
            return {"status": "failed", "id": f"pred_s3_failed_{self.stage3_calls}"}
        return {"status": "succeeded", "id": f"pred_s3_{self.stage3_calls}", "output": "http://mock/stage3.jpg"}

    def imagen_fallback(self, prompt: str):
        self.imagen_calls += 1
        return {"status": "succeeded", "id": f"pred_imagen_{self.imagen_calls}", "output": "http://mock/stage3_fallback.jpg"}

    def nano_banana_white_bg(self, image_path: Path, word: str):
        self.stage4_calls += 1
        if self.stage4_calls in self.nano_fail_attempts:
            return {"status": "failed", "id": f"pred_s4_failed_{self.stage4_calls}"}
        return {"status": "succeeded", "id": f"pred_s4_{self.stage4_calls}", "output": "http://mock/stage4.jpg"}

    @staticmethod
    def extract_output_url(pred_json: dict):
        out = pred_json.get("output")
        if isinstance(out, list) and out:
            return out[-1]
        if isinstance(out, str):
            return out
        return ""

    def download_image(self, url: str) -> bytes:
        img = Image.new("RGB", (16, 12), color=(255, 255, 255))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()


def _create_run(db_session, *, max_optimization_attempts: int = 3):
    repo = Repository(db_session)
    entry = repo.create_entry(
        {
            "word": "apple",
            "part_of_sentence": "noun",
            "category": "food",
            "context": "fruit",
            "boy_or_girl": "girl",
            "batch": "1",
        }
    )
    run = repo.create_runs(
        [entry.id],
        quality_threshold=95,
        max_optimization_attempts=max_optimization_attempts,
    )[0]
    return run


def test_happy_path_all_stages(db_session):
    run = _create_run(db_session)
    mock_replicate = MockReplicate()
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[95]), replicate_client=mock_replicate)

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert result.quality_score == 95
    assert mock_replicate.stage4_calls == 1


def test_stage2_retry_then_success(db_session):
    run = _create_run(db_session)
    mock_replicate = MockReplicate(stage2_failures_before_success=1)
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[95]), replicate_client=mock_replicate)

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert mock_replicate.stage2_calls >= 2


def test_stage3_flux_fallback_to_imagen(db_session):
    run = _create_run(db_session)
    mock_replicate = MockReplicate(flux_fail_attempts={1})
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[95]), replicate_client=mock_replicate)

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert mock_replicate.imagen_calls == 1


def test_stage4_failure_exhausts_retries(db_session):
    repo = Repository(db_session)
    config = repo.get_runtime_config()
    config.stage_retry_limit = 2
    db_session.add(config)
    db_session.commit()

    run = _create_run(db_session)
    mock_replicate = MockReplicate(nano_fail_attempts={1, 2, 3, 4, 5})
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[95]), replicate_client=mock_replicate)

    result = runner.process_run(run.id)

    assert result.status == "failed_technical"
    assert result.current_stage == "stage4_background"


def test_quality_loop_passes_on_second_attempt(db_session):
    run = _create_run(db_session, max_optimization_attempts=3)
    mock_replicate = MockReplicate()
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[80, 96]), replicate_client=mock_replicate)

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert result.optimization_attempt == 2
    assert result.quality_score == 96
    assert mock_replicate.stage4_calls == 1


def test_quality_loop_reaches_fail_threshold(db_session):
    run = _create_run(db_session, max_optimization_attempts=1)
    mock_replicate = MockReplicate()
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[70, 75]), replicate_client=mock_replicate)

    result = runner.process_run(run.id)

    assert result.status == "completed_fail_threshold"
    assert result.optimization_attempt == 2
    assert result.quality_score == 75
    assert mock_replicate.stage4_calls == 1


def test_winner_attempt_is_highest_score_and_used_for_stage4(db_session):
    run = _create_run(db_session, max_optimization_attempts=3)
    mock_replicate = MockReplicate()
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[70, 92, 85, 80]), replicate_client=mock_replicate)

    result = runner.process_run(run.id)

    assert result.status == "completed_fail_threshold"
    assert result.optimization_attempt == 2
    assert result.quality_score == 92
    assert mock_replicate.stage4_calls == 1
