from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import Asset, Entry, Prompt, Run, StageResult
from app.services.openai_client import AssistantRunFailedError, OpenAIClient
from app.services.person_profiles import additional_variant_profiles, profile_prompt_fragment
from app.services.prompt_templates import (
    apply_render_decision_to_prompt,
    build_stage1_prompt,
    build_stage3_prompt,
    default_person_profile_for_prompt,
    normalize_need_person,
    resolve_person_decision,
)
from app.services.replicate_client import ReplicateClient
from app.services.repository import Repository
from app.services.storage import image_dimensions, sha256_bytes, write_image, write_metadata
from app.services.utils import sanitize_filename

logger = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(
        self,
        db: Session,
        *,
        openai_client: OpenAIClient | None = None,
        replicate_client: ReplicateClient | None = None,
    ) -> None:
        self.db = db
        self.repo = Repository(db)
        self.openai = openai_client or OpenAIClient()
        self.replicate = replicate_client or ReplicateClient()

    def _record_stage(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        status: str,
        request_json: dict[str, Any],
        response_json: dict[str, Any],
        error_detail: str = "",
    ) -> None:
        self.repo.add_stage_result(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            status=status,
            idempotency_key=f"{run_id}:{stage_name}:{attempt}",
            request_json=request_json,
            response_json=response_json,
            error_detail=error_detail,
        )

    def _latest_prompt(self, run_id: str, stage_name: str) -> Prompt | None:
        return self.db.execute(
            select(Prompt)
            .where(Prompt.run_id == run_id)
            .where(Prompt.stage_name == stage_name)
            .order_by(desc(Prompt.created_at))
            .limit(1)
        ).scalar_one_or_none()

    def _latest_asset(self, run_id: str, stage_name: str) -> Asset | None:
        return self.db.execute(
            select(Asset)
            .where(Asset.run_id == run_id)
            .where(Asset.stage_name == stage_name)
            .order_by(desc(Asset.created_at))
            .limit(1)
        ).scalar_one_or_none()

    def _latest_stage_result(self, run_id: str, stage_name: str) -> StageResult | None:
        return self.db.execute(
            select(StageResult)
            .where(StageResult.run_id == run_id)
            .where(StageResult.stage_name == stage_name)
            .order_by(desc(StageResult.created_at))
            .limit(1)
        ).scalar_one_or_none()

    def _save_asset(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        filename: str,
        image_bytes: bytes,
        origin_url: str,
        model_name: str,
    ) -> Asset:
        path = write_image(run_id, filename, image_bytes)
        width, height = image_dimensions(path)
        return self.repo.add_asset(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            file_name=path.name,
            abs_path=path.as_posix(),
            mime_type="image/jpeg",
            sha256=sha256_bytes(image_bytes),
            width=width,
            height=height,
            origin_url=origin_url,
            model_name=model_name,
        )

    def _set_failed_technical(self, run: Run, stage_name: str, detail: str) -> None:
        self.repo.update_run(
            run,
            status="failed_technical",
            current_stage=stage_name,
            error_detail=detail,
            technical_retry_count=run.technical_retry_count + 1,
        )

    @staticmethod
    def _entry_slug(entry: Entry) -> str:
        parts = [
            (entry.word or "").strip().lower() or "unknown-word",
            (entry.part_of_sentence or "").strip().lower() or "unknown-pos",
            (entry.category or "").strip().lower() or "no-category",
            (entry.boy_or_girl or "").strip().lower() or "unspecified-person",
        ]
        return sanitize_filename("_".join(parts))

    def _asset_for_attempt(self, run_id: str, stage_name: str, attempt: int) -> Asset | None:
        return self.db.execute(
            select(Asset)
            .where(Asset.run_id == run_id)
            .where(Asset.stage_name == stage_name)
            .where(Asset.attempt == attempt)
            .order_by(desc(Asset.created_at))
            .limit(1)
        ).scalar_one_or_none()

    def _execute_with_stage_retry(self, limit: int, fn):
        error: Exception | None = None
        for _ in range(limit):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                error = exc
        if error is None:
            raise RuntimeError("unknown stage execution error")
        raise error

    @staticmethod
    def _variant_suffix(profile: dict[str, str]) -> str:
        return sanitize_filename(
            f"{profile.get('gender', 'person')}_{profile.get('age', 'age')}_{profile.get('skin_color', 'skin')}"
        )

    def _variant_pool_size(self, variant_count: int) -> int:
        runtime_config = self.repo.get_runtime_config()
        configured = int(getattr(runtime_config, "max_parallel_runs", 1) or 1)
        return max(1, min(variant_count, configured))

    def process_run(self, run_id: str) -> Run:
        run = self.repo.get_run(run_id)
        if run is None:
            raise RuntimeError(f"Run not found: {run_id}")

        entry = self.repo.get_entry(run.entry_id)
        if entry is None:
            self._set_failed_technical(run, "stage1_prompt", "Entry missing")
            return run

        config = self.repo.get_runtime_config()
        self.openai.settings.max_api_retries = config.max_api_retries
        self.replicate.settings.max_api_retries = config.max_api_retries
        assistant_id = ""
        if config.prompt_engineer_mode == "assistant":
            assistant_id = self.openai.resolve_assistant_id(config.openai_assistant_id, config.openai_assistant_name)

        start_stage = run.retry_from_stage or "stage1_prompt"
        run = self.repo.update_run(run, status="running", current_stage=start_stage, retry_from_stage="")

        try:
            if start_stage in {"stage1_prompt", "queued"}:
                run = self._run_stage1(run, entry, assistant_id, config.stage_retry_limit)
                run = self._run_stage2(run, entry, config.stage_retry_limit)
            elif start_stage == "stage2_draft":
                run = self._run_stage2(run, entry, config.stage_retry_limit)

            if start_stage in {"stage1_prompt", "stage2_draft", "stage3_upgrade", "stage4_background", "quality_gate", "queued"}:
                run = self._run_optimization_loop(run, entry, assistant_id, config.stage_retry_limit)

        except Exception as exc:  # noqa: BLE001
            self._set_failed_technical(run, run.current_stage, str(exc))
            request_json = getattr(exc, "request_json", {})
            response_json = getattr(exc, "response_json", {})
            self._record_stage(
                run_id=run.id,
                stage_name=run.current_stage,
                attempt=max(1, run.optimization_attempt),
                status="error",
                request_json=request_json if isinstance(request_json, dict) else {},
                response_json=response_json if isinstance(response_json, dict) else {},
                error_detail=str(exc),
            )
            return self.repo.get_run(run.id) or run

        return self.repo.get_run(run.id) or run

    def _run_stage1(self, run: Run, entry: Entry, assistant_id: str, retry_limit: int) -> Run:
        run = self.repo.update_run(run, current_stage="stage1_prompt")

        def _exec():
            start = perf_counter()
            runtime_config = self.repo.get_runtime_config()
            prompt_payload = build_stage1_prompt(
                entry,
                runtime_config.stage1_prompt_template,
                visual_style_id=runtime_config.visual_style_id,
                visual_style_name=runtime_config.visual_style_name,
                visual_style_block=runtime_config.visual_style_prompt_block,
            )
            try:
                parsed, raw = self.openai.generate_first_prompt(
                    prompt_payload,
                    assistant_id,
                    mode=runtime_config.prompt_engineer_mode,
                    responses_model=runtime_config.responses_prompt_engineer_model,
                    vector_store_id=runtime_config.responses_vector_store_id,
                )
            except AssistantRunFailedError as exc:
                exc.request_json = {"prompt": prompt_payload, **(exc.request_json or {})}
                raise
            first_prompt = parsed.get("first prompt") or parsed.get("prompt") or parsed.get("first_prompt")
            if not first_prompt:
                raise RuntimeError("Missing 'first prompt' in assistant response")
            need_person = str(parsed.get("need a person", parsed.get("need_person", "no"))).strip().lower()
            need_person = normalize_need_person(need_person)
            enforced_first_prompt, decision = apply_render_decision_to_prompt(
                first_prompt,
                resolved_need_person=need_person,
                resolved_need_person_reasoning="kept_stage1_person_decision",
                word=entry.word,
                part_of_sentence=entry.part_of_sentence,
                category=entry.category,
                context=entry.context,
                person_profile=default_person_profile_for_prompt(entry),
                illustration_style_id=runtime_config.visual_style_id,
                illustration_style_name=runtime_config.visual_style_name,
                illustration_style_block=runtime_config.visual_style_prompt_block,
            )
            self.repo.add_prompt(
                run_id=run.id,
                stage_name="stage1_prompt",
                attempt=0,
                prompt_text=enforced_first_prompt,
                needs_person=need_person,
                source=runtime_config.prompt_engineer_mode,
                raw_response_json={
                    "prompt_engineer_mode": runtime_config.prompt_engineer_mode,
                    "parsed": parsed,
                    "raw": raw,
                    "decision": decision,
                    "original_prompt_text": first_prompt,
                    "enforced_prompt_text": enforced_first_prompt,
                },
            )
            self._record_stage(
                run_id=run.id,
                stage_name="stage1_prompt",
                attempt=0,
                status="ok",
                request_json={
                    "prompt": prompt_payload,
                    "prompt_engineer_mode": runtime_config.prompt_engineer_mode,
                    "responses_model": runtime_config.responses_prompt_engineer_model if runtime_config.prompt_engineer_mode == "responses_api" else "",
                    "responses_vector_store_id": runtime_config.responses_vector_store_id if runtime_config.prompt_engineer_mode == "responses_api" else "",
                    "visual_style_id": runtime_config.visual_style_id,
                    "visual_style_name": runtime_config.visual_style_name,
                    "visual_style_prompt_block": runtime_config.visual_style_prompt_block,
                },
                response_json={
                    "prompt_engineer_mode": runtime_config.prompt_engineer_mode,
                    "parsed": parsed,
                    "raw": raw,
                    "decision": decision,
                    "original_prompt_text": first_prompt,
                    "enforced_prompt_text": enforced_first_prompt,
                },
            )
            logger.info(
                "stage completed",
                extra={
                    "run_id": run.id,
                    "stage_name": "stage1_prompt",
                    "status": "ok",
                    "provider": "openai_assistant",
                    "latency_ms": round((perf_counter() - start) * 1000, 2),
                },
            )

        self._execute_with_stage_retry(retry_limit, _exec)
        return self.repo.get_run(run.id) or run

    def _run_stage2(self, run: Run, entry: Entry, retry_limit: int) -> Run:
        run = self.repo.update_run(run, current_stage="stage2_draft")
        first_prompt = self._latest_prompt(run.id, "stage1_prompt")
        if first_prompt is None:
            raise RuntimeError("Stage 1 prompt missing for stage 2")

        def _exec():
            start = perf_counter()
            result = self.replicate.flux_schnell(first_prompt.prompt_text)
            if result.get("status") != "succeeded":
                raise RuntimeError(f"FLUX schnell failed: {result.get('status')}")

            output_url = self.replicate.extract_output_url(result)
            if not output_url:
                raise RuntimeError("No output URL from FLUX schnell")

            image_bytes = self.replicate.download_image(output_url)
            filename = f"stage2_draft_{self._entry_slug(entry)}.jpg"
            self._save_asset(
                run_id=run.id,
                stage_name="stage2_draft",
                attempt=0,
                filename=filename,
                image_bytes=image_bytes,
                origin_url=output_url,
                model_name="black-forest-labs/flux-schnell",
            )
            self._record_stage(
                run_id=run.id,
                stage_name="stage2_draft",
                attempt=0,
                status="ok",
                request_json={"prompt": first_prompt.prompt_text},
                response_json=result,
            )
            logger.info(
                "stage completed",
                extra={
                    "run_id": run.id,
                    "stage_name": "stage2_draft",
                    "status": "ok",
                    "provider": "replicate",
                    "latency_ms": round((perf_counter() - start) * 1000, 2),
                },
            )

        self._execute_with_stage_retry(retry_limit, _exec)
        return self.repo.get_run(run.id) or run

    def _run_optimization_loop(self, run: Run, entry: Entry, assistant_id: str, retry_limit: int) -> Run:
        total_attempt_budget = run.max_optimization_attempts + 1
        current_attempt = max(run.optimization_attempt, 0) + 1
        previous_score_explanation = ""
        best_attempt: int | None = None
        best_score: float | None = None
        best_rubric: dict[str, Any] = {}

        while current_attempt <= total_attempt_budget:
            run = self.repo.update_run(run, current_stage="stage3_upgrade", optimization_attempt=current_attempt)

            self._execute_with_stage_retry(
                retry_limit,
                lambda: self._run_stage3_attempt(
                    run=run,
                    entry=entry,
                    assistant_id=assistant_id,
                    attempt=current_attempt,
                    previous_score_explanation=previous_score_explanation,
                ),
            )

            run = self.repo.update_run(run, current_stage="quality_gate", optimization_attempt=current_attempt)
            score, _passed, rubric = self._execute_with_stage_retry(
                retry_limit,
                lambda: self._run_quality_gate_attempt(
                    run=run,
                    entry=entry,
                    attempt=current_attempt,
                ),
            )
            if best_score is None or score > best_score:
                best_score = score
                best_attempt = current_attempt
                best_rubric = rubric

            # Stop early once threshold is reached; no need to generate N+1 attempts.
            if score >= run.quality_threshold:
                break

            previous_score_explanation = str(rubric.get("explanation", ""))
            if current_attempt >= total_attempt_budget:
                break

            current_attempt += 1

        if best_attempt is None or best_score is None:
            raise RuntimeError("No scored attempt available to select winner")

        run = self.repo.update_run(run, current_stage="stage4_background", optimization_attempt=best_attempt, quality_score=best_score)
        self._execute_with_stage_retry(
            retry_limit,
            lambda: self._run_stage4_attempt(
                run=run,
                entry=entry,
                winner_attempt=best_attempt,
                winner_score=best_score,
            ),
        )
        self._execute_with_stage_retry(
            retry_limit,
            lambda: self._run_person_variants(
                run=run,
                entry=entry,
                winner_attempt=best_attempt,
            ),
        )

        if best_score >= run.quality_threshold:
            status = "completed_pass"
            error_detail = ""
        else:
            status = "completed_fail_threshold"
            error_detail = (
                f"Best score {best_score} below threshold {run.quality_threshold} "
                f"(winner attempt {best_attempt}; explanation: {str(best_rubric.get('explanation', ''))})"
            )

        run = self.repo.update_run(
            run,
            status=status,
            current_stage="completed",
            quality_score=best_score,
            optimization_attempt=best_attempt,
            error_detail=error_detail,
        )
        return run

    def _run_stage3_attempt(
        self,
        *,
        run: Run,
        entry: Entry,
        assistant_id: str,
        attempt: int,
        previous_score_explanation: str,
    ) -> None:
        critique_source_asset = self._latest_asset(run.id, "stage3_upgraded") or self._latest_asset(run.id, "stage2_draft")
        if critique_source_asset is None:
            raise RuntimeError("No source asset available for stage 3")
        critique_path = Path(critique_source_asset.abs_path)
        runtime_config = self.repo.get_runtime_config()
        critique_model = runtime_config.stage3_critique_model
        stage1_prompt = self._latest_prompt(run.id, "stage1_prompt")
        latest_stage3 = self._latest_stage_result(run.id, "stage3_upgrade")
        latest_stage3_response = json.loads(latest_stage3.response_json) if latest_stage3 and latest_stage3.response_json else {}
        latest_decision = latest_stage3_response.get("decision", {}) if isinstance(latest_stage3_response, dict) else {}
        current_need_person = latest_decision.get("resolved_need_person") or (stage1_prompt.needs_person if stage1_prompt else "no")
        current_render_style_mode = latest_decision.get("render_style_mode") or ("illustration" if normalize_need_person(current_need_person) == "yes" else "photorealistic")

        start = perf_counter()
        analysis, analysis_raw = self.openai.analyze_image(
            critique_path,
            entry.word,
            entry.part_of_sentence,
            entry.category,
            model=critique_model,
            initial_need_person=current_need_person,
            current_render_style_mode=current_render_style_mode,
        )

        previous_prompt = self._latest_prompt(run.id, "stage3_upgrade") or self._latest_prompt(run.id, "stage1_prompt")
        if previous_prompt is None:
            raise RuntimeError("No prior prompt to upgrade")
        initial_need_person = latest_decision.get("resolved_need_person") or (stage1_prompt.needs_person if stage1_prompt else "no")
        resolved_decision = resolve_person_decision(
            initial_need_person=initial_need_person,
            person_needed_for_clarity=str(analysis.get("person_needed_for_clarity", "")),
            person_presence_problem=str(analysis.get("person_presence_problem", "none")),
            person_profile=default_person_profile_for_prompt(entry),
            illustration_style_id=runtime_config.visual_style_id,
            illustration_style_name=runtime_config.visual_style_name,
            illustration_style_block=runtime_config.visual_style_prompt_block,
        )

        recommendations = str(analysis.get("recommendations", ""))
        if previous_score_explanation:
            recommendations = f"{recommendations}\nPrevious score feedback: {previous_score_explanation}"

        runtime_config = self.repo.get_runtime_config()
        upgrade_request = build_stage3_prompt(
            entry,
            old_prompt=previous_prompt.prompt_text,
            challenges=str(analysis.get("challenges", "")),
            recommendations=recommendations,
            template_text=runtime_config.stage3_prompt_template,
            visual_style_id=runtime_config.visual_style_id,
            visual_style_name=runtime_config.visual_style_name,
            visual_style_block=runtime_config.visual_style_prompt_block,
            resolved_need_person=resolved_decision["resolved_need_person"],
            resolved_need_person_reasoning=resolved_decision["resolved_need_person_reasoning"],
            render_style_mode=resolved_decision["render_style_mode"],
            person_decision_instruction=resolved_decision["person_decision_instruction"],
        )

        try:
            parsed, raw = self.openai.generate_upgraded_prompt(
                upgrade_request,
                assistant_id,
                mode=runtime_config.prompt_engineer_mode,
                responses_model=runtime_config.responses_prompt_engineer_model,
                vector_store_id=runtime_config.responses_vector_store_id,
            )
        except AssistantRunFailedError as exc:
            exc.request_json = {
                "upgrade_prompt_request": upgrade_request,
                "critique_model_selected": critique_model,
                **(exc.request_json or {}),
            }
            exc.response_json = {
                "analysis": analysis,
                "analysis_raw": analysis_raw,
                **(exc.response_json or {}),
            }
            raise
        upgraded_prompt = parsed.get("upgraded prompt") or parsed.get("prompt")
        if not upgraded_prompt:
            raise RuntimeError("Missing upgraded prompt")
        enforced_upgraded_prompt, enforced_decision = apply_render_decision_to_prompt(
            upgraded_prompt,
            resolved_need_person=resolved_decision["resolved_need_person"],
            resolved_need_person_reasoning=resolved_decision["resolved_need_person_reasoning"],
            word=entry.word,
            part_of_sentence=entry.part_of_sentence,
            category=entry.category,
            context=entry.context,
            person_profile=default_person_profile_for_prompt(entry),
            illustration_style_id=runtime_config.visual_style_id,
            illustration_style_name=runtime_config.visual_style_name,
            illustration_style_block=runtime_config.visual_style_prompt_block,
        )
        enforced_decision = {
            **resolved_decision,
            **enforced_decision,
            "initial_need_person": resolved_decision["initial_need_person"],
            "person_needed_for_clarity": resolved_decision["person_needed_for_clarity"],
            "person_presence_problem": resolved_decision["person_presence_problem"],
            "resolved_need_person_reasoning": resolved_decision["resolved_need_person_reasoning"],
        }

        self.repo.add_prompt(
            run_id=run.id,
            stage_name="stage3_upgrade",
            attempt=attempt,
            prompt_text=enforced_upgraded_prompt,
            needs_person=enforced_decision["resolved_need_person"],
            source=runtime_config.prompt_engineer_mode,
            raw_response_json={
                "prompt_engineer_mode": runtime_config.prompt_engineer_mode,
                "parsed": parsed,
                "raw": raw,
                "analysis": analysis,
                "analysis_raw": analysis_raw,
                "decision": enforced_decision,
                "original_prompt_text": upgraded_prompt,
                "enforced_prompt_text": enforced_upgraded_prompt,
            },
        )

        selected_stage3_model = runtime_config.stage3_generate_model
        flux_result, model_name = self.replicate.generate_stage3(selected_stage3_model, enforced_upgraded_prompt)
        if flux_result.get("status") != "succeeded":
            fallback_enabled = runtime_config.flux_imagen_fallback_enabled
            if selected_stage3_model != "flux-1.1-pro" or not fallback_enabled:
                raise RuntimeError(f"Stage3 generation failed with {selected_stage3_model}: {flux_result.get('status')}")
            flux_result, model_name = self.replicate.generate_stage3("imagen-3", enforced_upgraded_prompt)
            if flux_result.get("status") != "succeeded":
                raise RuntimeError(f"Stage3 fallback failed: {flux_result.get('status')}")

        output_url = self.replicate.extract_output_url(flux_result)
        if not output_url:
            raise RuntimeError("No output URL for stage3 upgraded image")
        image_bytes = self.replicate.download_image(output_url)

        filename = f"stage3_upgraded_{self._entry_slug(entry)}_attempt_{attempt}.jpg"
        self._save_asset(
            run_id=run.id,
            stage_name="stage3_upgraded",
            attempt=attempt,
            filename=filename,
            image_bytes=image_bytes,
            origin_url=output_url,
            model_name=model_name,
        )

        self._record_stage(
            run_id=run.id,
            stage_name="stage3_upgrade",
            attempt=attempt,
            status="ok",
            request_json={
                "upgrade_prompt_request": upgrade_request,
                "prompt_engineer_mode": runtime_config.prompt_engineer_mode,
                "responses_model": runtime_config.responses_prompt_engineer_model if runtime_config.prompt_engineer_mode == "responses_api" else "",
                "responses_vector_store_id": runtime_config.responses_vector_store_id if runtime_config.prompt_engineer_mode == "responses_api" else "",
                "visual_style_id": runtime_config.visual_style_id,
                "visual_style_name": runtime_config.visual_style_name,
                "visual_style_prompt_block": runtime_config.visual_style_prompt_block,
                "critique_model_selected": critique_model,
                "generation_model_selected": selected_stage3_model,
                "initial_need_person": current_need_person,
                "current_render_style_mode": current_render_style_mode,
                "resolved_need_person": enforced_decision["resolved_need_person"],
                "resolved_need_person_reasoning": enforced_decision["resolved_need_person_reasoning"],
                "render_style_mode": enforced_decision["render_style_mode"],
                "person_decision_instruction": enforced_decision["person_decision_instruction"],
            },
            response_json={
                "analysis": analysis,
                "analysis_raw": analysis_raw,
                "prompt_engineer": {"parsed": parsed, "raw": raw, "mode": runtime_config.prompt_engineer_mode},
                "generation": flux_result,
                "generation_model": model_name,
                "generation_model_selected": selected_stage3_model,
                "decision": enforced_decision,
                "original_prompt_text": upgraded_prompt,
                "enforced_prompt_text": enforced_upgraded_prompt,
            },
        )

        write_metadata(
            run.id,
            attempt,
            {
                "attempt": attempt,
                "stage3": {
                    "analysis": analysis,
                    "prompt_engineer": {"parsed": parsed, "raw": raw, "mode": runtime_config.prompt_engineer_mode},
                    "generation": flux_result,
                    "generation_model": model_name,
                    "decision": enforced_decision,
                },
            },
        )

        logger.info(
            "stage completed",
            extra={
                "run_id": run.id,
                "stage_name": "stage3_upgrade",
                "status": "ok",
                "provider": "openai+replicate",
                "latency_ms": round((perf_counter() - start) * 1000, 2),
            },
        )

    def _run_person_variants(self, *, run: Run, entry: Entry, winner_attempt: int) -> None:
        stage3_result = self.db.execute(
            select(StageResult)
            .where(StageResult.run_id == run.id)
            .where(StageResult.stage_name == "stage3_upgrade")
            .where(StageResult.attempt == winner_attempt)
            .limit(1)
        ).scalar_one_or_none()
        if stage3_result is None:
            return

        stage3_response = json.loads(stage3_result.response_json) if stage3_result.response_json else {}
        decision = stage3_response.get("decision", {}) if isinstance(stage3_response, dict) else {}
        if normalize_need_person(str(decision.get("resolved_need_person", "no"))) != "yes":
            return

        profiles = additional_variant_profiles(entry)
        if not profiles:
            return

        upgraded_asset = self._asset_for_attempt(run.id, "stage3_upgraded", winner_attempt)
        if upgraded_asset is None:
            raise RuntimeError(f"Missing stage3 upgraded image for variant generation attempt {winner_attempt}")
        white_bg_asset = self._asset_for_attempt(run.id, "stage4_white_bg", winner_attempt)
        if white_bg_asset is None:
            raise RuntimeError(f"Missing white background winner image for variant generation attempt {winner_attempt}")

        with_background_variants: list[dict[str, Any]] = []
        white_bg_variants: list[dict[str, Any]] = []
        self.repo.update_run(run, current_stage="stage4_variant_generate")
        self._record_stage(
            run_id=run.id,
            stage_name="stage4_variant_generate",
            attempt=winner_attempt,
            status="running",
            request_json={
                "winner_attempt": winner_attempt,
                "source_asset": upgraded_asset.abs_path,
                "profiles": profiles,
                "variant_count": 0,
                "planned_variant_count": len(profiles),
            },
            response_json={
                "model": "google/nano-banana-2",
                "source_asset": upgraded_asset.abs_path,
                "variants": [],
            },
        )
        self._record_stage(
            run_id=run.id,
            stage_name="stage5_variant_white_bg",
            attempt=winner_attempt,
            status="running",
            request_json={
                "winner_attempt": winner_attempt,
                "source_asset": white_bg_asset.abs_path,
                "variant_count": 0,
                "planned_variant_count": len(profiles),
            },
            response_json={
                "model": "google/nano-banana-2",
                "source_asset": white_bg_asset.abs_path,
                "variants": [],
            },
        )
        tasks: list[tuple[str, dict[str, str], Path, bool]] = []
        for profile in profiles:
            tasks.append(("stage4_variant_generate", profile, Path(upgraded_asset.abs_path), False))
            tasks.append(("stage5_variant_white_bg", profile, Path(white_bg_asset.abs_path), True))

        with ThreadPoolExecutor(max_workers=self._variant_pool_size(len(tasks))) as executor:
            futures = {
                executor.submit(
                    self._generate_profile_variant_payload,
                    source_path,
                    entry.word,
                    profile,
                    white_background,
                ): (stage_name, profile, white_background)
                for stage_name, profile, source_path, white_background in tasks
            }
            for future in as_completed(futures):
                stage_name, profile, white_background = futures[future]
                profile_suffix = self._variant_suffix(profile)
                payload = future.result()
                if stage_name == "stage4_variant_generate":
                    variant_filename = f"stage4_variant_{self._entry_slug(entry)}_{profile_suffix}_attempt_{winner_attempt}.jpg"
                else:
                    variant_filename = f"stage5_variant_white_bg_{self._entry_slug(entry)}_{profile_suffix}_attempt_{winner_attempt}.jpg"
                variant_asset = self._save_asset(
                    run_id=run.id,
                    stage_name=stage_name,
                    attempt=winner_attempt,
                    filename=variant_filename,
                    image_bytes=payload["image_bytes"],
                    origin_url=payload["origin_url"],
                    model_name=payload["model_name"],
                )
                item = {
                    "profile": profile,
                    "profile_description": payload["profile_description"],
                    "asset": {
                        "id": variant_asset.id,
                        "file_name": variant_asset.file_name,
                        "abs_path": variant_asset.abs_path,
                        "origin_url": variant_asset.origin_url,
                    },
                    "response": payload["response"],
                }
                if stage_name == "stage4_variant_generate":
                    with_background_variants.append(item)
                    self._record_stage(
                        run_id=run.id,
                        stage_name="stage4_variant_generate",
                        attempt=winner_attempt,
                        status="running",
                        request_json={
                            "winner_attempt": winner_attempt,
                            "source_asset": upgraded_asset.abs_path,
                            "profiles": profiles,
                            "variant_count": len(with_background_variants),
                            "planned_variant_count": len(profiles),
                        },
                        response_json={
                            "model": "google/nano-banana-2",
                            "source_asset": upgraded_asset.abs_path,
                            "variants": with_background_variants,
                        },
                    )
                else:
                    white_bg_variants.append(item)
                    self._record_stage(
                        run_id=run.id,
                        stage_name="stage5_variant_white_bg",
                        attempt=winner_attempt,
                        status="running",
                        request_json={
                            "winner_attempt": winner_attempt,
                            "source_asset": white_bg_asset.abs_path,
                            "variant_count": len(white_bg_variants),
                            "planned_variant_count": len(profiles),
                        },
                        response_json={
                            "model": "google/nano-banana-2",
                            "source_asset": white_bg_asset.abs_path,
                            "variants": white_bg_variants,
                        },
                    )

        self._record_stage(
            run_id=run.id,
            stage_name="stage4_variant_generate",
            attempt=winner_attempt,
            status="ok",
            request_json={
                "winner_attempt": winner_attempt,
                "source_asset": upgraded_asset.abs_path,
                "profiles": profiles,
                "variant_count": len(with_background_variants),
                "planned_variant_count": len(profiles),
            },
            response_json={
                "model": "google/nano-banana-2",
                "source_asset": upgraded_asset.abs_path,
                "variants": with_background_variants,
            },
        )

        self.repo.update_run(run, current_stage="stage5_variant_white_bg")
        self._record_stage(
            run_id=run.id,
            stage_name="stage5_variant_white_bg",
            attempt=winner_attempt,
            status="ok",
            request_json={
                "winner_attempt": winner_attempt,
                "variant_count": len(white_bg_variants),
                "planned_variant_count": len(profiles),
                "source_asset": white_bg_asset.abs_path,
            },
            response_json={
                "model": "google/nano-banana-2",
                "source_asset": white_bg_asset.abs_path,
                "variants": white_bg_variants,
            },
        )

    def _generate_profile_variant_payload(
        self,
        image_path: Path,
        word: str,
        profile: dict[str, str],
        white_background: bool,
    ) -> dict[str, Any]:
        profile_description = profile_prompt_fragment(profile)
        result = self.replicate.nano_banana_profile_variant(
            image_path,
            word=word,
            profile_description=profile_description,
            white_background=white_background,
        )
        profile_suffix = self._variant_suffix(profile)
        if result.get("status") != "succeeded":
            stage_label = "white background variant" if white_background else "variant generation"
            raise RuntimeError(f"{stage_label} failed for {profile_suffix}: {result.get('status')}")
        output_url = self.replicate.extract_output_url(result)
        if not output_url:
            stage_label = "white background variant" if white_background else "variant generation"
            raise RuntimeError(f"No output URL for {stage_label} {profile_suffix}")
        image_bytes = self.replicate.download_image(output_url)
        return {
            "profile_description": profile_description,
            "response": result,
            "origin_url": output_url,
            "image_bytes": image_bytes,
            "model_name": "google/nano-banana-2",
        }

    def _run_stage4_attempt(self, *, run: Run, entry: Entry, winner_attempt: int, winner_score: float) -> None:
        upgraded_asset = self._asset_for_attempt(run.id, "stage3_upgraded", winner_attempt)
        if upgraded_asset is None:
            raise RuntimeError(f"Missing stage3 upgraded image for winner attempt {winner_attempt}")

        start = perf_counter()
        result = self.replicate.nano_banana_white_bg(Path(upgraded_asset.abs_path), entry.word)
        if result.get("status") != "succeeded":
            raise RuntimeError(f"Nano banana failed: {result.get('status')}")

        output_url = self.replicate.extract_output_url(result)
        if not output_url:
            raise RuntimeError("No output URL for stage4")

        image_bytes = self.replicate.download_image(output_url)
        filename = f"stage4_white_bg_{self._entry_slug(entry)}_attempt_{winner_attempt}.jpg"
        self._save_asset(
            run_id=run.id,
            stage_name="stage4_white_bg",
            attempt=winner_attempt,
            filename=filename,
            image_bytes=image_bytes,
            origin_url=output_url,
            model_name="google/nano-banana-2",
        )

        self._record_stage(
            run_id=run.id,
            stage_name="stage4_background",
            attempt=winner_attempt,
            status="ok",
            request_json={
                "input_asset": upgraded_asset.abs_path,
                "winner_attempt": winner_attempt,
                "winner_score": winner_score,
                "background_model_selected": "nano-banana-2",
            },
            response_json=result,
        )

        logger.info(
            "stage completed",
            extra={
                "run_id": run.id,
                "stage_name": "stage4_background",
                "status": "ok",
                "provider": "replicate",
                "latency_ms": round((perf_counter() - start) * 1000, 2),
            },
        )

    def _run_quality_gate_attempt(self, *, run: Run, entry: Entry, attempt: int) -> tuple[float, bool, dict[str, Any]]:
        final_asset = self._asset_for_attempt(run.id, "stage3_upgraded", attempt)
        if final_asset is None:
            raise RuntimeError(f"Missing stage3 upgraded image for attempt {attempt}")

        start = perf_counter()
        config = self.repo.get_runtime_config()
        stage3_result = self.db.execute(
            select(StageResult)
            .where(StageResult.run_id == run.id)
            .where(StageResult.stage_name == "stage3_upgrade")
            .where(StageResult.attempt == attempt)
            .limit(1)
        ).scalar_one_or_none()
        stage3_response = json.loads(stage3_result.response_json) if stage3_result and stage3_result.response_json else {}
        decision = stage3_response.get("decision", {}) if isinstance(stage3_response, dict) else {}
        rubric, raw = self.openai.score_image(
            Path(final_asset.abs_path),
            word=entry.word,
            part_of_sentence=entry.part_of_sentence,
            category=entry.category,
            threshold=run.quality_threshold,
            model=config.quality_gate_model,
            expected_render_style_mode=str(decision.get("render_style_mode", "")),
        )
        score = float(rubric.get("score", 0))
        passed = score >= run.quality_threshold

        self.repo.add_score(
            run_id=run.id,
            stage_name="quality_gate",
            attempt=attempt,
            score_0_100=score,
            pass_fail=passed,
            rubric_json={"rubric": rubric, "raw": raw},
        )

        self._record_stage(
            run_id=run.id,
            stage_name="quality_gate",
            attempt=attempt,
            status="ok",
            request_json={
                "asset": final_asset.abs_path,
                "threshold": run.quality_threshold,
                "quality_model_selected": config.quality_gate_model,
                "expected_render_style_mode": str(decision.get("render_style_mode", "")),
                "resolved_need_person": str(decision.get("resolved_need_person", "")),
            },
            response_json={"rubric": rubric, "raw": raw},
        )

        run_dir_file = Path(final_asset.abs_path).parent / f"metadata_attempt_{attempt}.json"
        metadata: dict[str, Any] = {}
        if run_dir_file.exists():
            metadata = json.loads(run_dir_file.read_text(encoding="utf-8"))
        metadata["quality_gate"] = {"score": score, "passed": passed, "rubric": rubric}
        run_dir_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(
            "stage completed",
            extra={
                "run_id": run.id,
                "stage_name": "quality_gate",
                "status": "ok",
                "provider": "openai",
                "latency_ms": round((perf_counter() - start) * 1000, 2),
            },
        )

        return score, passed, rubric
