from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.services.pipeline import PipelineRunner
from app.services.openai_client import AssistantRunFailedError
from app.services.repository import Repository


class MockOpenAI:
    def __init__(self, scores: list[int]):
        self._scores = scores
        self._score_idx = 0
        self._upgrade_idx = 0

    def resolve_assistant_id(self, configured_id: str, configured_name: str) -> str:
        return configured_id or "asst_test"

    def generate_first_prompt(self, user_text: str, assistant_id: str, **_kwargs):
        return {"first prompt": "simple concept image", "need a person": "no"}, {"raw_text": "ok"}

    def analyze_image(self, image_path: Path, word: str, part_of_sentence: str, category: str, model: str, **_kwargs):
        return {"challenges": "too generic", "recommendations": "increase clarity"}, {"raw_text": "ok"}

    def generate_upgraded_prompt(self, user_text: str, assistant_id: str, **_kwargs):
        self._upgrade_idx += 1
        return {"upgraded prompt": f"upgraded prompt {self._upgrade_idx}"}, {"raw_text": "ok"}

    def score_image(self, image_path: Path, *, word: str, part_of_sentence: str, category: str, threshold: int, model: str, **_kwargs):
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


class FailingAssistantOpenAI(MockOpenAI):
    def generate_first_prompt(self, user_text: str, assistant_id: str, **_kwargs):
        raise AssistantRunFailedError(
            "Assistant run status: failed; code=server_error; message=assistant crashed",
            request_json={"assistant_input": user_text, "assistant_id": assistant_id},
            response_json={
                "thread_id": "thread_fail",
                "run_id": "run_fail",
                "run_payload": {
                    "status": "failed",
                    "last_error": {"code": "server_error", "message": "assistant crashed"},
                },
                "last_error": {"code": "server_error", "message": "assistant crashed"},
            },
        )


class RecordingPromptEngineerOpenAI(MockOpenAI):
    def __init__(self, scores: list[int]):
        super().__init__(scores)
        self.stage1_kwargs = {}
        self.stage3_kwargs = {}

    def generate_first_prompt(self, user_text: str, assistant_id: str, **kwargs):
        self.stage1_kwargs = kwargs
        return {"first prompt": "responses api first prompt", "need a person": "no"}, {"raw_text": '{"first prompt":"responses api first prompt","need a person":"no"}'}

    def generate_upgraded_prompt(self, user_text: str, assistant_id: str, **kwargs):
        self.stage3_kwargs = kwargs
        return {"upgraded prompt": "responses api upgraded prompt"}, {"raw_text": '{"upgraded prompt":"responses api upgraded prompt"}'}


class PersonVariantOpenAI(MockOpenAI):
    def generate_first_prompt(self, user_text: str, assistant_id: str, **_kwargs):
        return {"first prompt": "person-centered action image", "need a person": "yes"}, {"raw_text": "ok"}

    def analyze_image(self, image_path: Path, word: str, part_of_sentence: str, category: str, model: str, **_kwargs):
        return (
            {
                "challenges": "needs stronger action",
                "recommendations": "keep the person and emphasize the action",
                "person_needed_for_clarity": "yes",
                "person_presence_problem": "none",
                "person_decision_reasoning": "The verb needs a visible person for AAC clarity.",
            },
            {"raw_text": "ok"},
        )

    def generate_upgraded_prompt(self, user_text: str, assistant_id: str, **_kwargs):
        return {"upgraded prompt": "upgraded person-centered action image"}, {"raw_text": "ok"}


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

    def flux_schnell(self, prompt: str, *, aspect_ratio: str = "1:1"):
        self.stage2_calls += 1
        if self.stage2_calls <= self.stage2_failures_before_success:
            return {"status": "failed", "id": "pred_s2_failed"}
        return {"status": "succeeded", "id": "pred_s2", "output": "http://mock/stage2.jpg"}

    def generate_stage3(self, model_choice: str, prompt: str, *, aspect_ratio: str = "1:1"):
        if model_choice == "flux-1.1-pro":
            self.stage3_calls += 1
            if self.stage3_calls in self.flux_fail_attempts:
                return {"status": "failed", "id": f"pred_s3_failed_{self.stage3_calls}"}, "black-forest-labs/flux-1.1-pro"
            return {"status": "succeeded", "id": f"pred_s3_{self.stage3_calls}", "output": "http://mock/stage3.jpg"}, "black-forest-labs/flux-1.1-pro"

        if model_choice == "imagen-3":
            self.imagen_calls += 1
            return {"status": "succeeded", "id": f"pred_imagen_{self.imagen_calls}", "output": "http://mock/stage3_fallback.jpg"}, "google/imagen-3-fast"

        self.stage3_calls += 1
        return {"status": "succeeded", "id": f"pred_s3_{self.stage3_calls}", "output": "http://mock/stage3.jpg"}, model_choice

    def nano_banana_white_bg(self, image_path: Path, word: str, *, aspect_ratio: str = "1:1", image_size: str = "1K"):
        self.stage4_calls += 1
        if self.stage4_calls in self.nano_fail_attempts:
            return {"status": "failed", "id": f"pred_s4_failed_{self.stage4_calls}"}
        return {"status": "succeeded", "id": f"pred_s4_{self.stage4_calls}", "output": "http://mock/stage4.jpg"}

    def profile_variant_request_summary(
        self,
        image_path: Path,
        *,
        word: str,
        profile_description: str,
        white_background: bool = False,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        edit_instruction: str = "",
    ) -> dict[str, object]:
        return {
            "model_path": "google/nano-banana-2",
            "prompt": f"{edit_instruction} variant prompt for {profile_description}".strip(),
            "source_image_path": image_path.as_posix(),
            "white_background": white_background,
            "aspect_ratio": aspect_ratio or "match_input_image",
            "image_size": image_size or "1K",
            "output_format": "jpg",
            "word": word,
        }

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


class VariantCapableReplicate(MockReplicate):
    def __init__(self):
        super().__init__()
        self.variant_submissions: list[dict[str, str | bool]] = []
        self._variant_predictions: dict[str, dict[str, object]] = {}
        self._variant_idx = 0

    def submit_nano_banana_profile_variant(
        self,
        image_path: Path,
        *,
        word: str,
        profile_description: str,
        white_background: bool = False,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        edit_instruction: str = "",
    ) -> dict[str, object]:
        self._variant_idx += 1
        prediction_id = f"pred_variant_{self._variant_idx}"
        self.variant_submissions.append(
            {
                "prediction_id": prediction_id,
                "source_path": image_path.as_posix(),
                "profile_description": profile_description,
                "white_background": white_background,
            }
        )
        self._variant_predictions[prediction_id] = {
            "status": "processing",
            "polls_remaining": 1,
            "output": f"http://mock/{prediction_id}.jpg",
        }
        return {"id": prediction_id, "status": "processing"}

    def get_prediction(self, prediction_id: str) -> dict[str, object]:
        state = self._variant_predictions[prediction_id]
        polls_remaining = int(state["polls_remaining"])
        if polls_remaining > 0:
            state["polls_remaining"] = polls_remaining - 1
            return {"id": prediction_id, "status": "processing"}
        return {
            "id": prediction_id,
            "status": "succeeded",
            "output": state["output"],
            "model": "google/nano-banana-2",
        }


class MockGoogleImageClient:
    def __init__(self, *, stage4_failures: set[int] | None = None):
        self.stage3_calls = 0
        self.stage4_calls = 0
        self.stage4_failures = stage4_failures or set()
        self.variant_submissions: list[dict[str, str | bool]] = []
        self._variant_predictions: dict[str, dict[str, object]] = {}
        self._variant_idx = 0
        self._inline_assets: dict[str, bytes] = {}

    @staticmethod
    def _image_bytes() -> bytes:
        img = Image.new("RGB", (16, 12), color=(255, 255, 255))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def _inline_url(self, prediction_id: str) -> str:
        url = f"google-inline://{prediction_id}"
        self._inline_assets[url] = self._image_bytes()
        return url

    def generate_stage3(self, model_choice: str, prompt: str, *, aspect_ratio: str = "1:1", image_size: str = "1K"):
        self.stage3_calls += 1
        prediction_id = f"google_stage3_{self.stage3_calls}"
        return {
            "status": "succeeded",
            "id": prediction_id,
            "model": "gemini-3.1-flash-image-preview",
            "output": [self._inline_url(prediction_id)],
            "request_json": {"prompt": prompt, "model_choice": model_choice, "aspect_ratio": aspect_ratio, "image_size": image_size},
            "response_json": {"mock": True},
        }, "gemini-3.1-flash-image-preview"

    def nano_banana_white_bg(self, image_path: Path, word: str, *, aspect_ratio: str = "1:1", image_size: str = "1K"):
        self.stage4_calls += 1
        prediction_id = f"google_stage4_{self.stage4_calls}"
        if self.stage4_calls in self.stage4_failures:
            return {
                "status": "failed",
                "id": prediction_id,
                "model": "gemini-3.1-flash-image-preview",
                "request_json": {"image_path": image_path.as_posix(), "word": word, "aspect_ratio": aspect_ratio, "image_size": image_size},
                "response_json": {"mock": True, "status": "failed"},
            }
        return {
            "status": "succeeded",
            "id": prediction_id,
            "model": "gemini-3.1-flash-image-preview",
            "output": [self._inline_url(prediction_id)],
            "request_json": {"image_path": image_path.as_posix(), "word": word, "aspect_ratio": aspect_ratio, "image_size": image_size},
            "response_json": {"mock": True},
        }

    def profile_variant_request_summary(
        self,
        image_path: Path,
        *,
        word: str,
        profile_description: str,
        white_background: bool = False,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        edit_instruction: str = "",
    ) -> dict[str, object]:
        return {
            "model": "nano-banana-2",
            "provider_model": "gemini-3.1-flash-image-preview",
            "prompt": f"{edit_instruction} variant prompt for {profile_description}".strip(),
            "source_image_path": image_path.as_posix(),
            "white_background": white_background,
            "aspect_ratio": aspect_ratio or "match_input_image",
            "image_size": image_size or "1K",
            "word": word,
        }

    def submit_nano_banana_profile_variant(
        self,
        image_path: Path,
        *,
        word: str,
        profile_description: str,
        white_background: bool = False,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        edit_instruction: str = "",
    ) -> dict[str, object]:
        self._variant_idx += 1
        prediction_id = f"google_variant_{self._variant_idx}"
        self.variant_submissions.append(
            {
                "prediction_id": prediction_id,
                "source_path": image_path.as_posix(),
                "profile_description": profile_description,
                "white_background": white_background,
            }
        )
        self._variant_predictions[prediction_id] = {
            "status": "processing",
            "polls_remaining": 1,
            "output": self._inline_url(prediction_id),
            "request_json": {
                "source_path": image_path.as_posix(),
                "profile_description": profile_description,
                "aspect_ratio": aspect_ratio or "match_input_image",
                "image_size": image_size or "1K",
                "edit_instruction": edit_instruction,
            },
            "response_json": {"mock": True},
        }
        return {"id": prediction_id, "status": "processing", "model": "gemini-3.1-flash-image-preview"}

    def get_prediction(self, prediction_id: str) -> dict[str, object]:
        state = self._variant_predictions[prediction_id]
        polls_remaining = int(state["polls_remaining"])
        if polls_remaining > 0:
            state["polls_remaining"] = polls_remaining - 1
            return {"id": prediction_id, "status": "processing"}
        return {
            "id": prediction_id,
            "status": "succeeded",
            "output": state["output"],
            "model": "gemini-3.1-flash-image-preview",
            "request_json": state["request_json"],
            "response_json": state["response_json"],
        }

    def download_image(self, url: str) -> bytes:
        return self._inline_assets[url]


class RecordingPipelineRunner(PipelineRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorded_stage_payloads: list[dict[str, object]] = []

    def _record_stage(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        status: str,
        request_json: dict[str, object],
        response_json: dict[str, object],
        error_detail: str = "",
    ) -> None:
        self.recorded_stage_payloads.append(
            {
                "run_id": run_id,
                "stage_name": stage_name,
                "attempt": attempt,
                "status": status,
                "request_json": json.loads(json.dumps(request_json)),
                "response_json": json.loads(json.dumps(response_json)),
                "error_detail": error_detail,
            }
        )
        super()._record_stage(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            status=status,
            request_json=request_json,
            response_json=response_json,
            error_detail=error_detail,
        )


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


def _create_variant_run(db_session):
    repo = Repository(db_session)
    entry = repo.create_entry(
        {
            "word": "soccer",
            "part_of_sentence": "verb",
            "category": "",
            "context": "",
            "boy_or_girl": "male",
            "person_gender_options": ["male", "female"],
            "person_age_options": ["kid", "tween"],
            "person_skin_color_options": ["white", "black"],
            "batch": "1",
        }
    )
    run = repo.create_runs(
        [entry.id],
        quality_threshold=95,
        max_optimization_attempts=3,
    )[0]
    return run


def test_happy_path_all_stages(db_session):
    run = _create_run(db_session)
    mock_replicate = MockReplicate()
    mock_google = MockGoogleImageClient()
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[95]), replicate_client=mock_replicate, google_image_client=mock_google)

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert result.quality_score == 95
    assert mock_google.stage4_calls == 1


def test_stage2_retry_then_success(db_session):
    run = _create_run(db_session)
    mock_replicate = MockReplicate(stage2_failures_before_success=1)
    runner = PipelineRunner(
        db_session,
        openai_client=MockOpenAI(scores=[95]),
        replicate_client=mock_replicate,
        google_image_client=MockGoogleImageClient(),
    )

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert mock_replicate.stage2_calls >= 2


def test_stage3_flux_fallback_to_imagen(db_session):
    repo = Repository(db_session)
    repo.update_runtime_config({"stage3_generate_model": "flux-1.1-pro"})
    run = _create_run(db_session)
    mock_replicate = MockReplicate(flux_fail_attempts={1})
    runner = PipelineRunner(
        db_session,
        openai_client=MockOpenAI(scores=[95]),
        replicate_client=mock_replicate,
        google_image_client=MockGoogleImageClient(),
    )

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
    mock_replicate = MockReplicate()
    mock_google = MockGoogleImageClient(stage4_failures={1, 2, 3, 4, 5})
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[95]), replicate_client=mock_replicate, google_image_client=mock_google)

    result = runner.process_run(run.id)

    assert result.status == "failed_technical"
    assert result.current_stage == "stage4_background"


def test_quality_loop_passes_on_second_attempt(db_session):
    run = _create_run(db_session, max_optimization_attempts=3)
    mock_replicate = MockReplicate()
    mock_google = MockGoogleImageClient()
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[80, 96]), replicate_client=mock_replicate, google_image_client=mock_google)

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert result.optimization_attempt == 2
    assert result.quality_score == 96
    assert mock_google.stage4_calls == 1


def test_quality_loop_reaches_fail_threshold(db_session):
    run = _create_run(db_session, max_optimization_attempts=1)
    mock_replicate = MockReplicate()
    mock_google = MockGoogleImageClient()
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[70, 75]), replicate_client=mock_replicate, google_image_client=mock_google)

    result = runner.process_run(run.id)

    assert result.status == "completed_fail_threshold"
    assert result.optimization_attempt == 2
    assert result.quality_score == 75
    assert mock_google.stage4_calls == 1


def test_winner_attempt_is_highest_score_and_used_for_stage4(db_session):
    run = _create_run(db_session, max_optimization_attempts=3)
    mock_replicate = MockReplicate()
    mock_google = MockGoogleImageClient()
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[70, 92, 85, 80]), replicate_client=mock_replicate, google_image_client=mock_google)

    result = runner.process_run(run.id)

    assert result.status == "completed_fail_threshold"
    assert result.optimization_attempt == 2
    assert result.quality_score == 92
    assert mock_google.stage4_calls == 1


def test_threshold_pass_stops_additional_attempts(db_session):
    run = _create_run(db_session, max_optimization_attempts=3)
    mock_replicate = MockReplicate()
    mock_google = MockGoogleImageClient()
    runner = PipelineRunner(db_session, openai_client=MockOpenAI(scores=[96, 99, 99, 99]), replicate_client=mock_replicate, google_image_client=mock_google)

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert result.optimization_attempt == 1
    assert result.quality_score == 96
    assert mock_google.stage3_calls == 1
    assert mock_google.stage4_calls == 1


def test_stage1_assistant_failure_records_last_error_payload(db_session):
    run = _create_run(db_session)
    runner = PipelineRunner(
        db_session,
        openai_client=FailingAssistantOpenAI(scores=[95]),
        replicate_client=MockReplicate(),
        google_image_client=MockGoogleImageClient(),
    )

    result = runner.process_run(run.id)

    assert result.status == "failed_technical"
    assert "server_error" in result.error_detail

    repo = Repository(db_session)
    _, stages, _, _, _ = repo.run_details(run.id)
    stage1_error = next(stage for stage in stages if stage.stage_name == "stage1_prompt")
    response_json = json.loads(stage1_error.response_json)
    assert stage1_error.status == "error"
    assert response_json.get("last_error", {}).get("code") == "server_error"
    assert response_json.get("run_payload", {}).get("status") == "failed"


def test_responses_api_prompt_engineer_mode_is_recorded(db_session):
    repo = Repository(db_session)
    config = repo.update_runtime_config(
        {
            "prompt_engineer_mode": "responses_api",
            "responses_prompt_engineer_model": "gpt-4.1-mini",
            "responses_vector_store_id": "vs_test_123",
            "stage1_prompt_template": "Word: {word}",
            "stage3_prompt_template": "Old prompt: {old_prompt}\nWord: {word}\nChallenges: {challenges}\nRecommendations: {recommendations}",
        }
    )
    assert config.prompt_engineer_mode == "responses_api"

    run = _create_run(db_session)
    mock_openai = RecordingPromptEngineerOpenAI(scores=[95])
    runner = PipelineRunner(
        db_session,
        openai_client=mock_openai,
        replicate_client=MockReplicate(),
        google_image_client=MockGoogleImageClient(),
    )

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert mock_openai.stage1_kwargs["mode"] == "responses_api"
    assert mock_openai.stage1_kwargs["vector_store_id"] == "vs_test_123"
    assert mock_openai.stage3_kwargs["mode"] == "responses_api"

    _, stages, prompts, _, _ = repo.run_details(run.id)
    stage1 = next(stage for stage in stages if stage.stage_name == "stage1_prompt")
    stage1_request = json.loads(stage1.request_json)
    assert stage1_request["prompt_engineer_mode"] == "responses_api"
    assert stage1_request["responses_vector_store_id"] == "vs_test_123"

    stage1_prompt = next(prompt for prompt in prompts if prompt.stage_name == "stage1_prompt")
    assert stage1_prompt.source == "responses_api"


def test_variant_stages_record_progress_and_completed_profiles(db_session):
    run = _create_variant_run(db_session)
    mock_replicate = MockReplicate()
    mock_google = MockGoogleImageClient()
    runner = RecordingPipelineRunner(
        db_session,
        openai_client=PersonVariantOpenAI(scores=[98]),
        replicate_client=mock_replicate,
        google_image_client=mock_google,
    )

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"

    stage4_snapshots = [
        payload for payload in runner.recorded_stage_payloads
        if payload["stage_name"] == "stage4_variant_generate"
    ]
    stage5_snapshots = [
        payload for payload in runner.recorded_stage_payloads
        if payload["stage_name"] == "stage5_variant_white_bg"
    ]
    assert any(
        payload["status"] == "running"
        and int(payload["response_json"].get("progress", {}).get("in_flight_count", 0)) > 0
        for payload in stage4_snapshots
    )
    assert any(
        payload["status"] == "running"
        and int(payload["response_json"].get("progress", {}).get("in_flight_count", 0)) > 0
        for payload in stage5_snapshots
    )

    repo = Repository(db_session)
    _, stages, _, assets, _ = repo.run_details(run.id)
    stage4 = next(stage for stage in stages if stage.stage_name == "stage4_variant_generate")
    stage5 = next(stage for stage in stages if stage.stage_name == "stage5_variant_white_bg")
    stage4_response = json.loads(stage4.response_json)
    stage5_response = json.loads(stage5.response_json)

    assert stage4.status == "ok"
    assert stage5.status == "ok"
    assert stage4_response["progress"]["completed_count"] == len(stage4_response["variants"])
    assert stage5_response["progress"]["completed_count"] == len(stage5_response["variants"])
    assert len(stage4_response["submitted_profiles"]) >= len(stage4_response["completed_profiles"]) > 0
    assert len(stage5_response["submitted_profiles"]) >= len(stage5_response["completed_profiles"]) > 0
    assert any(asset.stage_name == "stage4_variant_generate" for asset in assets)
    assert any(asset.stage_name == "stage5_variant_white_bg" for asset in assets)


def test_variant_female_branch_uses_female_seed_assets(db_session):
    run = _create_variant_run(db_session)
    mock_replicate = MockReplicate()
    mock_google = MockGoogleImageClient()
    runner = RecordingPipelineRunner(
        db_session,
        openai_client=PersonVariantOpenAI(scores=[98]),
        replicate_client=mock_replicate,
        google_image_client=mock_google,
    )

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    assert any(
        submission["source_path"].endswith("female_kid_white_attempt_1.jpg")
        and not submission["white_background"]
        for submission in mock_google.variant_submissions
    )
    assert any(
        submission["source_path"].endswith("female_kid_white_attempt_1.jpg")
        and submission["white_background"]
        for submission in mock_google.variant_submissions
    )


def test_rerunning_variant_stage_reuses_existing_assets_without_duplicates(db_session):
    run = _create_variant_run(db_session)
    runner = RecordingPipelineRunner(
        db_session,
        openai_client=PersonVariantOpenAI(scores=[98]),
        replicate_client=MockReplicate(),
        google_image_client=MockGoogleImageClient(),
    )

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    repo = Repository(db_session)
    entry = repo.get_entry(result.entry_id)
    assert entry is not None
    _, _, _, first_assets, _ = repo.run_details(run.id)
    first_variant_assets = [
        asset for asset in first_assets
        if asset.stage_name in {"stage4_variant_generate", "stage5_variant_white_bg"}
    ]

    runner._run_person_variants(run=repo.get_run(run.id), entry=entry, winner_attempt=1)

    _, _, _, second_assets, _ = repo.run_details(run.id)
    second_variant_assets = [
        asset for asset in second_assets
        if asset.stage_name in {"stage4_variant_generate", "stage5_variant_white_bg"}
    ]
    assert len(second_variant_assets) == len(first_variant_assets)


def test_variant_events_capture_request_poll_and_save(db_session):
    run = _create_variant_run(db_session)
    runner = RecordingPipelineRunner(
        db_session,
        openai_client=PersonVariantOpenAI(scores=[98]),
        replicate_client=MockReplicate(),
        google_image_client=MockGoogleImageClient(),
    )

    result = runner.process_run(run.id)

    assert result.status == "completed_pass"
    repo = Repository(db_session)
    events = repo.list_run_events(run.id)
    assert any(event.event_type == "stage_completed" and event.stage_name == "stage4_background" for event in events)
    assert any(event.event_type == "stage_started" and event.stage_name == "stage4_variant_generate" for event in events)
    assert any(event.event_type == "stage_started" and event.stage_name == "stage5_variant_white_bg" for event in events)

    submit_event = next(event for event in events if event.event_type == "variant_submit_finished")
    submit_payload = json.loads(submit_event.payload_json)
    assert submit_payload["request"]["provider_model"] == "gemini-3.1-flash-image-preview"
    assert submit_payload["request"]["source_image_path"]
    assert submit_payload["request"]["prompt"]
    assert submit_payload["provider_response"]["id"].startswith("google_variant_")

    poll_event = next(event for event in events if event.event_type == "variant_prediction_polled")
    poll_payload = json.loads(poll_event.payload_json)
    assert poll_payload["provider_status"] in {"processing", "succeeded"}

    save_event = next(event for event in events if event.event_type == "variant_asset_saved")
    save_payload = json.loads(save_event.payload_json)
    assert save_payload["saved_asset_path"].endswith(".jpg")
    assert save_payload["provider_response"]["status"] == "succeeded"
