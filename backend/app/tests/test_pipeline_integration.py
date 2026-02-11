from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from app.services.pipeline import PipelineRunner
from app.services.repository import Repository


class MockOpenAI:
    def __init__(
        self,
        scores: list[int],
        *,
        abstract_rubrics: list[dict] | None = None,
    ):
        self._scores = scores
        self._score_idx = 0
        self._upgrade_idx = 0
        self._abstract_rubrics = abstract_rubrics or []
        self._abstract_idx = 0
        self.last_stage1_user_text = ""

    def resolve_assistant_id(self, configured_id: str, configured_name: str) -> str:
        return configured_id or "asst_test"

    def generate_first_prompt(self, user_text: str, assistant_id: str):
        self.last_stage1_user_text = user_text
        return {"first prompt": "simple concept image", "need a person": "no"}, {"raw_text": "ok"}

    def analyze_image(self, image_path: Path, word: str, part_of_sentence: str, category: str, model: str):
        return {"challenges": "too generic", "recommendations": "increase clarity"}, {"raw_text": "ok"}

    def generate_upgraded_prompt(self, user_text: str, assistant_id: str):
        self._upgrade_idx += 1
        return {"upgraded prompt": f"upgraded prompt {self._upgrade_idx}"}, {"raw_text": "ok"}

    def score_image(
        self,
        image_path: Path,
        *,
        word: str,
        part_of_sentence: str,
        category: str,
        threshold: int,
        model: str,
        abstract_mode: bool = False,
        contrast_subject: str = "",
    ):
        score = self._scores[min(self._score_idx, len(self._scores) - 1)]
        self._score_idx += 1
        if abstract_mode:
            payload = self._abstract_rubrics[min(self._abstract_idx, len(self._abstract_rubrics) - 1)] if self._abstract_rubrics else {}
            self._abstract_idx += 1
            return (
                {
                    "score": score,
                    "contrast_clarity": payload.get("contrast_clarity", 5),
                    "absence_signal_strength": payload.get("absence_signal_strength", 5),
                    "aac_interpretability": payload.get("aac_interpretability", 5),
                    "explanation": f"abstract score {score}",
                    "failure_tags": payload.get("failure_tags", [] if score >= threshold else ["ambiguity"]),
                },
                {"raw_text": "ok", "contrast_subject": contrast_subject},
            )
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


def _create_run(
    db_session,
    *,
    max_optimization_attempts: int = 3,
    word: str = "apple",
    part_of_sentence: str = "noun",
    category: str = "food",
    context: str = "fruit",
):
    repo = Repository(db_session)
    entry = repo.create_entry(
        {
            "word": word,
            "part_of_sentence": part_of_sentence,
            "category": category,
            "context": context,
            "boy_or_girl": "girl",
            "batch": "1",
        }
    )
    run = repo.create_runs(
        [entry.id],
        quality_threshold=90,
        max_optimization_attempts=max_optimization_attempts,
    )[0]
    return run


def test_happy_path_all_stages(db_session):
    run = _create_run(db_session)
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[95]), replicate_client=MockReplicate())

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert result.quality_score == 95


def test_stage2_retry_then_success(db_session):
    run = _create_run(db_session)
    mock_replicate = MockReplicate(stage2_failures_before_success=1)
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[92]), replicate_client=mock_replicate)

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert mock_replicate.stage2_calls >= 2


def test_stage3_flux_fallback_to_imagen(db_session):
    run = _create_run(db_session)
    mock_replicate = MockReplicate(flux_fail_attempts={1})
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[93]), replicate_client=mock_replicate)

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
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[80, 94]), replicate_client=MockReplicate())

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert result.optimization_attempt == 2
    assert result.quality_score == 94


def test_quality_loop_reaches_fail_threshold(db_session):
    run = _create_run(db_session, max_optimization_attempts=1)
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[70, 75]), replicate_client=MockReplicate())

    result = runner.process_run(run.id)

    assert result.status == "completed_fail_threshold"
    assert result.optimization_attempt == 2
    assert result.quality_score == 75


def test_abstract_word_sets_review_warning_after_three_failed_attempts(db_session):
    run = _create_run(
        db_session,
        max_optimization_attempts=2,
        word="none",
        part_of_sentence="pronoun",
        category="",
        context="there are no apples on the plate",
    )
    mock_openai = MockOpenAI(scores=[70, 75, 78])
    runner = PipelineRunner(db_session, openai_client=mock_openai, replicate_client=MockReplicate())

    result = runner.process_run(run.id)

    assert result.status == "completed_fail_threshold"
    assert result.review_warning is True
    assert "after 3 attempts" in result.review_warning_reason
    assert "single-frame contrast composition" in mock_openai.last_stage1_user_text


def test_concrete_word_never_sets_review_warning(db_session):
    run = _create_run(
        db_session,
        max_optimization_attempts=2,
        word="apple",
        part_of_sentence="noun",
        category="food",
        context="a red apple on a plate",
    )
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[70, 75, 78]), replicate_client=MockReplicate())

    result = runner.process_run(run.id)

    assert result.status == "completed_fail_threshold"
    assert result.review_warning is False
    assert result.review_warning_reason == ""


def test_abstract_requires_clarity_and_interpretability_even_with_high_score(db_session):
    run = _create_run(
        db_session,
        max_optimization_attempts=0,
        word="none",
        part_of_sentence="pronoun",
        category="",
        context="none of the balls are in the box",
    )
    mock_openai = MockOpenAI(
        scores=[95],
        abstract_rubrics=[{"contrast_clarity": 3, "absence_signal_strength": 5, "aac_interpretability": 5, "failure_tags": ["ambiguity"]}],
    )
    runner = PipelineRunner(db_session, openai_client=mock_openai, replicate_client=MockReplicate())

    result = runner.process_run(run.id)

    assert result.status == "completed_fail_threshold"
    assert result.quality_score == 95
