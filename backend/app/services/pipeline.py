from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter, sleep
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import Asset, Entry, Prompt, Run, StageResult
from app.services.google_image_client import GoogleImageClient
from app.services.model_catalog import is_google_image_generation_model
from app.services.openai_client import AssistantRunFailedError, OpenAIClient
from app.services.person_profiles import profile_edit_instruction, profile_key, profile_prompt_fragment, variant_branch_plan
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


class RunCanceledError(RuntimeError):
    def __init__(self, stage_name: str, message: str = "Run stopped by user") -> None:
        super().__init__(message)
        self.stage_name = stage_name
        self.request_json = {}
        self.response_json = {}


class PipelineRunner:
    def __init__(
        self,
        db: Session,
        *,
        openai_client: OpenAIClient | None = None,
        replicate_client: ReplicateClient | None = None,
        google_image_client: GoogleImageClient | None = None,
    ) -> None:
        self.db = db
        self.repo = Repository(db)
        self.openai = openai_client or OpenAIClient()
        self.replicate = replicate_client or ReplicateClient()
        self.google_images = google_image_client or GoogleImageClient()

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

    def _record_event(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        event_type: str,
        status: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.repo.add_run_event(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            event_type=event_type,
            status=status,
            message=message,
            payload_json=payload or {},
        )

    @staticmethod
    def _raise_with_context(message: str, *, request_json: dict[str, Any], response_json: dict[str, Any]) -> None:
        error = RuntimeError(message)
        error.request_json = request_json  # type: ignore[attr-defined]
        error.response_json = response_json  # type: ignore[attr-defined]
        raise error

    @staticmethod
    def _merge_error_context(
        exc: Exception,
        *,
        request_json: dict[str, Any] | None = None,
        response_json: dict[str, Any] | None = None,
    ) -> None:
        existing_request = getattr(exc, "request_json", {})
        existing_response = getattr(exc, "response_json", {})
        merged_request: dict[str, Any] = {}
        merged_response: dict[str, Any] = {}
        if isinstance(request_json, dict):
            merged_request.update(request_json)
        if isinstance(existing_request, dict):
            merged_request.update(existing_request)
        if isinstance(response_json, dict):
            merged_response.update(response_json)
        if isinstance(existing_response, dict):
            merged_response.update(existing_response)
        exc.request_json = merged_request  # type: ignore[attr-defined]
        exc.response_json = merged_response  # type: ignore[attr-defined]

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _download_generated_image(self, url: str) -> bytes:
        if str(url or "").startswith("google-inline://"):
            return self.google_images.download_image(url)
        return self.replicate.download_image(url)

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

    def _get_latest_run(self, run_id: str) -> Run | None:
        return self.repo.get_run(run_id)

    def _raise_if_stop_requested(self, run: Run, stage_name: str) -> None:
        latest = self._get_latest_run(run.id)
        if latest is None:
            raise RunCanceledError(stage_name, "Run missing while stop was being processed")
        if str(latest.status or "").strip().lower() in {"cancel_requested", "canceled"}:
            raise RunCanceledError(stage_name)

    def _set_canceled(self, run: Run, stage_name: str, detail: str = "Stopped by user") -> Run:
        updated = self.repo.update_run(
            run,
            status="canceled",
            current_stage=stage_name,
            retry_from_stage="",
            error_detail=detail,
        )
        self._record_event(
            run_id=updated.id,
            stage_name=stage_name,
            attempt=max(0, int(updated.optimization_attempt or 0)),
            event_type="run_canceled",
            status="canceled",
            message=detail,
            payload={"current_stage": stage_name},
        )
        return updated

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

    def _variant_pool_size(self, variant_count: int, worker_limit: int) -> int:
        return max(1, min(variant_count, worker_limit))

    def _variant_filename(self, stage_name: str, entry: Entry, profile: dict[str, str], winner_attempt: int) -> str:
        profile_suffix = self._variant_suffix(profile)
        if stage_name == "stage4_variant_generate":
            return f"stage4_variant_{self._entry_slug(entry)}_{profile_suffix}_attempt_{winner_attempt}.jpg"
        return f"stage5_variant_white_bg_{self._entry_slug(entry)}_{profile_suffix}_attempt_{winner_attempt}.jpg"

    def _variant_stage_item(
        self,
        *,
        asset: Asset,
        profile: dict[str, str],
        profile_description: str,
        branch_role: str,
        source_profile: dict[str, str] | None = None,
        response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = {
            "profile": profile,
            "profile_description": profile_description,
            "branch_role": branch_role,
            "asset": {
                "id": asset.id,
                "file_name": asset.file_name,
                "abs_path": asset.abs_path,
                "origin_url": asset.origin_url,
            },
            "response": response or {"status": "reused_existing_asset"},
        }
        if source_profile:
            item["source_profile"] = source_profile
        return item

    def _record_variant_stage_progress(
        self,
        *,
        run_id: str,
        stage_name: str,
        winner_attempt: int,
        source_asset: str,
        planned_profiles: list[dict[str, str]],
        branch_plan: dict[str, Any],
        variants: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        submitted_profiles: list[dict[str, Any]],
        completed_profiles: list[dict[str, Any]],
        failed_profiles: list[dict[str, Any]],
        status: str,
        aspect_ratio: str,
        image_size: str,
        active_count: int = 0,
    ) -> None:
        self._record_stage(
            run_id=run_id,
            stage_name=stage_name,
            attempt=winner_attempt,
            status=status,
            request_json={
                "winner_attempt": winner_attempt,
                "source_asset": source_asset,
                "profiles": planned_profiles,
                "planned_variant_count": len(planned_profiles),
                "variant_count": len(variants),
                "branch_plan": branch_plan,
                "image_aspect_ratio": aspect_ratio,
                "image_resolution": image_size,
            },
            response_json={
                "model": "gemini-3.1-flash-image-preview",
                "model_selected": "nano-banana-2",
                "source_asset": source_asset,
                "progress": {
                    "completed_count": len(variants),
                    "failed_count": len(failures),
                    "in_flight_count": active_count,
                    "remaining_count": max(0, len(planned_profiles) - len(variants) - len(failures)),
                },
                "submitted_profiles": submitted_profiles,
                "completed_profiles": completed_profiles,
                "failed_profiles": failed_profiles,
                "variants": variants,
                "failures": failures,
            },
            error_detail="; ".join(failure["error"] for failure in failures) if failures and status == "error" else "",
        )

    def process_run(self, run_id: str) -> Run:
        run = self.repo.get_run(run_id)
        if run is None:
            raise RuntimeError(f"Run not found: {run_id}")

        entry = self.repo.get_entry(run.entry_id)
        if entry is None:
            self._set_failed_technical(run, "stage1_prompt", "Entry missing")
            return run

        config = self.repo.get_runtime_config()
        variant_worker_limit = max(1, min(int(getattr(config, "max_variant_workers", 2)), 8))
        self.openai.settings.max_api_retries = config.max_api_retries
        self.replicate.settings.max_api_retries = config.max_api_retries
        self.google_images.configure_workers(variant_worker_limit)
        assistant_id = ""
        if config.prompt_engineer_mode == "assistant":
            assistant_id = self.openai.resolve_assistant_id(config.openai_assistant_id, config.openai_assistant_name)

        start_stage = run.retry_from_stage or "stage1_prompt"
        run = self.repo.update_run(run, status="running", current_stage=start_stage, retry_from_stage="")
        self._record_event(
            run_id=run.id,
            stage_name=start_stage,
            attempt=max(0, int(run.optimization_attempt or 0)),
            event_type="run_started",
            status="running",
            message="Run processing started",
            payload={
                "entry_id": entry.id,
                "word": entry.word,
                "part_of_sentence": entry.part_of_sentence,
                "category": entry.category,
                "start_stage": start_stage,
            },
        )

        try:
            self._raise_if_stop_requested(run, start_stage)
            if start_stage in {"stage1_prompt", "queued"}:
                run = self._run_stage1(run, entry, assistant_id, config.stage_retry_limit)
                self._raise_if_stop_requested(run, "stage1_prompt")
                run = self._run_stage2(run, entry, config.stage_retry_limit)
                self._raise_if_stop_requested(run, "stage2_draft")
            elif start_stage == "stage2_draft":
                run = self._run_stage2(run, entry, config.stage_retry_limit)
                self._raise_if_stop_requested(run, "stage2_draft")

            if start_stage in {"stage1_prompt", "stage2_draft", "stage3_upgrade", "stage4_background", "quality_gate", "queued"}:
                run = self._run_optimization_loop(run, entry, assistant_id, config.stage_retry_limit)

        except RunCanceledError as exc:
            stage_name = str(getattr(exc, "stage_name", "") or run.current_stage or start_stage)
            return self._set_canceled(run, stage_name, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._set_failed_technical(run, run.current_stage, str(exc))
            request_json = getattr(exc, "request_json", {})
            response_json = getattr(exc, "response_json", {})
            self._record_event(
                run_id=run.id,
                stage_name=run.current_stage,
                attempt=max(1, run.optimization_attempt),
                event_type="stage_failed",
                status="error",
                message=str(exc),
                payload={
                    "request_json": request_json if isinstance(request_json, dict) else {},
                    "response_json": response_json if isinstance(response_json, dict) else {},
                },
            )
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
        finally:
            self.google_images.close()

        return self.repo.get_run(run.id) or run

    def _run_stage1(self, run: Run, entry: Entry, assistant_id: str, retry_limit: int) -> Run:
        run = self.repo.update_run(run, current_stage="stage1_prompt")
        self._record_event(
            run_id=run.id,
            stage_name="stage1_prompt",
            attempt=0,
            event_type="stage_started",
            status="running",
            message="Stage 1 prompt generation started",
            payload={"entry_id": entry.id, "assistant_id": assistant_id},
        )

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
            self._record_event(
                run_id=run.id,
                stage_name="stage1_prompt",
                attempt=0,
                event_type="stage_completed",
                status="ok",
                message="Stage 1 prompt generation completed",
                payload={
                    "prompt_engineer_mode": runtime_config.prompt_engineer_mode,
                    "resolved_need_person": decision.get("resolved_need_person"),
                    "render_style_mode": decision.get("render_style_mode"),
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
        self._record_event(
            run_id=run.id,
            stage_name="stage2_draft",
            attempt=0,
            event_type="stage_started",
            status="running",
            message="Stage 2 draft generation started",
            payload={"source_prompt": first_prompt.prompt_text},
        )

        def _exec():
            start = perf_counter()
            runtime_config = self.repo.get_runtime_config()
            result = self.replicate.flux_schnell(
                first_prompt.prompt_text,
                aspect_ratio=runtime_config.image_aspect_ratio,
            )
            if result.get("status") != "succeeded":
                self._raise_with_context(
                    f"FLUX schnell failed: {result.get('status')}",
                    request_json={
                        "prompt": first_prompt.prompt_text,
                        "model": "black-forest-labs/flux-schnell",
                        "image_aspect_ratio": runtime_config.image_aspect_ratio,
                    },
                    response_json=result if isinstance(result, dict) else {},
                )

            output_url = self.replicate.extract_output_url(result)
            if not output_url:
                self._raise_with_context(
                    "No output URL from FLUX schnell",
                    request_json={
                        "prompt": first_prompt.prompt_text,
                        "model": "black-forest-labs/flux-schnell",
                        "image_aspect_ratio": runtime_config.image_aspect_ratio,
                    },
                    response_json=result if isinstance(result, dict) else {},
                )

            image_bytes = self._download_generated_image(output_url)
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
                request_json={
                    "prompt": first_prompt.prompt_text,
                    "image_aspect_ratio": runtime_config.image_aspect_ratio,
                },
                response_json=result,
            )
            self._record_event(
                run_id=run.id,
                stage_name="stage2_draft",
                attempt=0,
                event_type="stage_completed",
                status="ok",
                message="Stage 2 draft generation completed",
                payload={
                    "model": "black-forest-labs/flux-schnell",
                    "output_url": output_url,
                    "saved_file": filename,
                },
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
            self._raise_if_stop_requested(run, "stage3_upgrade")
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

            self._raise_if_stop_requested(run, "quality_gate")
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

        self._raise_if_stop_requested(run, "stage4_background")
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
        self._raise_if_stop_requested(run, "stage4_variant_generate")
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
        self._record_event(
            run_id=run.id,
            stage_name="stage3_upgrade",
            attempt=attempt,
            event_type="stage_started",
            status="running",
            message="Stage 3 upgrade started",
            payload={
                "source_asset": critique_source_asset.abs_path,
                "critique_model": critique_model,
                "initial_need_person": current_need_person,
                "current_render_style_mode": current_render_style_mode,
            },
        )

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
        stage3_request_json = {
            "selected_model": selected_stage3_model,
            "prompt": enforced_upgraded_prompt,
            "attempt": attempt,
            "word": entry.word,
            "part_of_sentence": entry.part_of_sentence,
            "category": entry.category,
            "image_aspect_ratio": runtime_config.image_aspect_ratio,
            "image_resolution": runtime_config.image_resolution,
        }
        generation_client = "google" if is_google_image_generation_model(selected_stage3_model) else "replicate"
        stage3_request_json["generation_client"] = generation_client
        try:
            if generation_client == "google":
                flux_result, model_name = self.google_images.generate_stage3(
                    selected_stage3_model,
                    enforced_upgraded_prompt,
                    aspect_ratio=runtime_config.image_aspect_ratio,
                    image_size=runtime_config.image_resolution,
                )
            else:
                flux_result, model_name = self.replicate.generate_stage3(
                    selected_stage3_model,
                    enforced_upgraded_prompt,
                    aspect_ratio=runtime_config.image_aspect_ratio,
                )
        except Exception as exc:  # noqa: BLE001
            self._merge_error_context(
                exc,
                request_json={**stage3_request_json, "generation_client": generation_client},
            )
            raise
        if flux_result.get("status") != "succeeded":
            fallback_enabled = runtime_config.flux_imagen_fallback_enabled
            if selected_stage3_model != "flux-1.1-pro" or not fallback_enabled:
                self._raise_with_context(
                    f"Stage3 generation failed with {selected_stage3_model}: {flux_result.get('status')}",
                    request_json=stage3_request_json,
                    response_json={
                        "generation": flux_result if isinstance(flux_result, dict) else {},
                        "generation_model": model_name,
                        "generation_client": generation_client,
                    },
                )
            flux_result, model_name = self.replicate.generate_stage3(
                "imagen-3",
                enforced_upgraded_prompt,
                aspect_ratio=runtime_config.image_aspect_ratio,
            )
            if flux_result.get("status") != "succeeded":
                self._raise_with_context(
                    f"Stage3 fallback failed: {flux_result.get('status')}",
                    request_json={**stage3_request_json, "selected_model": "imagen-3", "fallback_from": selected_stage3_model},
                    response_json={
                        "generation": flux_result if isinstance(flux_result, dict) else {},
                        "generation_model": model_name,
                        "generation_client": "replicate",
                    },
                )
            generation_client = "replicate"

        output_url = self.replicate.extract_output_url(flux_result)
        if not output_url:
            self._raise_with_context(
                "No output URL for stage3 upgraded image",
                request_json=stage3_request_json,
                response_json={
                    "generation": flux_result if isinstance(flux_result, dict) else {},
                    "generation_model": model_name,
                    "generation_client": generation_client,
                },
            )
        try:
            image_bytes = self._download_generated_image(output_url)
        except Exception as exc:  # noqa: BLE001
            self._merge_error_context(
                exc,
                request_json={**stage3_request_json, "generation_model": model_name, "output_url": output_url},
                response_json={
                    "generation": flux_result if isinstance(flux_result, dict) else {},
                    "generation_client": generation_client,
                },
            )
            raise

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
                "generation_client": generation_client,
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
                "generation_client": generation_client,
                "decision": enforced_decision,
                "original_prompt_text": upgraded_prompt,
                "enforced_prompt_text": enforced_upgraded_prompt,
            },
        )
        self._record_event(
            run_id=run.id,
            stage_name="stage3_upgrade",
            attempt=attempt,
            event_type="stage_completed",
            status="ok",
            message="Stage 3 upgrade completed",
            payload={
                "resolved_need_person": enforced_decision["resolved_need_person"],
                "render_style_mode": enforced_decision["render_style_mode"],
                "generation_model": model_name,
                "generation_client": generation_client,
                "saved_file": filename,
                "output_url": output_url,
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
                    "generation_client": generation_client,
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
                "provider": f"openai+{generation_client}",
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

        branch_plan = variant_branch_plan(entry)
        planned_profiles = list(branch_plan.get("planned_profiles") or [])[1:]
        if not planned_profiles:
            return

        upgraded_asset = self._asset_for_attempt(run.id, "stage3_upgraded", winner_attempt)
        if upgraded_asset is None:
            raise RuntimeError(f"Missing stage3 upgraded image for variant generation attempt {winner_attempt}")

        runtime_config = self.repo.get_runtime_config()
        aspect_ratio = runtime_config.image_aspect_ratio
        image_size = runtime_config.image_resolution
        variant_worker_limit = max(1, min(int(getattr(runtime_config, "max_variant_workers", 2)), 8))

        stage_names = ("stage4_variant_generate", "stage5_variant_white_bg")
        variants_by_stage: dict[str, list[dict[str, Any]]] = {stage_name: [] for stage_name in stage_names}
        failures_by_stage: dict[str, list[dict[str, Any]]] = {stage_name: [] for stage_name in stage_names}
        stage_variant_profile_keys: dict[str, set[str]] = {stage_name: set() for stage_name in stage_names}
        submitted_profiles_by_stage: dict[str, dict[str, dict[str, Any]]] = {stage_name: {} for stage_name in stage_names}
        completed_profiles_by_stage: dict[str, dict[str, Any]] = {stage_name: {} for stage_name in stage_names}
        failed_profiles_by_stage: dict[str, dict[str, Any]] = {stage_name: {} for stage_name in stage_names}
        def asset_snapshot(asset: Asset) -> dict[str, Any]:
            return {
                "id": asset.id,
                "file_name": asset.file_name,
                "abs_path": asset.abs_path,
                "origin_url": asset.origin_url,
                "model_name": asset.model_name,
            }

        final_assets_by_profile_key: dict[str, dict[str, Any]] = {
            profile_key(branch_plan["base_profile"]): asset_snapshot(upgraded_asset)
        }
        generated_final_profiles: list[dict[str, Any]] = []

        def stage_source_asset(stage_name: str) -> str:
            if stage_name == "stage4_variant_generate":
                return upgraded_asset.abs_path
            return "derived_from_matching_stage4_variant_asset"

        def log_variant_event(
            *,
            stage_name: str,
            event_type: str,
            status: str,
            message: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            self._record_event(
                run_id=run.id,
                stage_name=stage_name,
                attempt=winner_attempt,
                event_type=event_type,
                status=status,
                message=message,
                payload=payload,
            )

        def profile_state_item(
            *,
            profile: dict[str, str],
            branch_role: str,
            source_profile: dict[str, str] | None = None,
            profile_description: str | None = None,
            error: str = "",
            prediction_id: str = "",
            prediction_status: str = "",
        ) -> dict[str, Any]:
            item = {
                "profile": profile,
                "branch_role": branch_role,
                "profile_description": profile_description or profile_prompt_fragment(profile),
            }
            if source_profile:
                item["source_profile"] = source_profile
            if prediction_id:
                item["prediction_id"] = prediction_id
            if prediction_status:
                item["prediction_status"] = prediction_status
            if error:
                item["error"] = error
            return item

        def stage_variants(stage_name: str) -> list[dict[str, Any]]:
            return variants_by_stage[stage_name]

        def stage_failures(stage_name: str) -> list[dict[str, Any]]:
            return failures_by_stage[stage_name]

        def append_variant_item(stage_name: str, item: dict[str, Any]) -> None:
            variant_key = profile_key(item.get("profile", {}))
            if variant_key and variant_key in stage_variant_profile_keys[stage_name]:
                return
            if variant_key:
                stage_variant_profile_keys[stage_name].add(variant_key)
            stage_variants(stage_name).append(item)

        def mark_submitted(
            stage_name: str,
            *,
            profile: dict[str, str],
            branch_role: str,
            source_profile: dict[str, str] | None = None,
            profile_description: str | None = None,
            prediction_id: str = "",
            prediction_status: str = "",
        ) -> None:
            key = profile_key(profile)
            item = submitted_profiles_by_stage[stage_name].setdefault(
                key,
                profile_state_item(
                    profile=profile,
                    branch_role=branch_role,
                    source_profile=source_profile,
                    profile_description=profile_description,
                    prediction_id=prediction_id,
                    prediction_status=prediction_status,
                ),
            )
            if prediction_id:
                item["prediction_id"] = prediction_id
            if prediction_status:
                item["prediction_status"] = prediction_status

        def mark_completed(
            stage_name: str,
            *,
            profile: dict[str, str],
            branch_role: str,
            source_profile: dict[str, str] | None = None,
            profile_description: str | None = None,
        ) -> None:
            key = profile_key(profile)
            completed_profiles_by_stage[stage_name][key] = profile_state_item(
                profile=profile,
                branch_role=branch_role,
                source_profile=source_profile,
                profile_description=profile_description,
            )
            failed_profiles_by_stage[stage_name].pop(key, None)

        def append_failure(
            stage_name: str,
            *,
            profile: dict[str, str],
            branch_role: str,
            source_profile: dict[str, str] | None = None,
            profile_description: str | None = None,
            error: str,
            request_json: dict[str, Any] | None = None,
            response_json: dict[str, Any] | None = None,
        ) -> None:
            key = profile_key(profile)
            if any(profile_key(failure.get("profile", {})) == key for failure in stage_failures(stage_name)):
                return
            stage_failures(stage_name).append(
                {
                    "profile": profile,
                    "branch_role": branch_role,
                    "error": error,
                    "request_json": request_json or {},
                    "response_json": response_json or {},
                }
            )
            failed_profiles_by_stage[stage_name][key] = profile_state_item(
                profile=profile,
                branch_role=branch_role,
                source_profile=source_profile,
                profile_description=profile_description,
                error=error,
            )

        def existing_variant_asset(stage_name: str, profile: dict[str, str]) -> Asset | None:
            return self.repo.get_asset_by_file_name(
                run_id=run.id,
                stage_name=stage_name,
                attempt=winner_attempt,
                file_name=self._variant_filename(stage_name, entry, profile, winner_attempt),
            )

        def sync_variant_progress(stage_name: str, status: str = "running", active_count: int = 0) -> None:
            self._record_variant_stage_progress(
                run_id=run.id,
                stage_name=stage_name,
                winner_attempt=winner_attempt,
                source_asset=stage_source_asset(stage_name),
                planned_profiles=planned_profiles,
                branch_plan=branch_plan,
                variants=stage_variants(stage_name),
                failures=stage_failures(stage_name),
                submitted_profiles=list(submitted_profiles_by_stage[stage_name].values()),
                completed_profiles=list(completed_profiles_by_stage[stage_name].values()),
                failed_profiles=list(failed_profiles_by_stage[stage_name].values()),
                status=status,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                active_count=active_count,
            )

        def reuse_existing_variant(stage_name: str, profile: dict[str, str], branch_role: str, source_profile: dict[str, str] | None = None) -> Asset | None:
            existing = existing_variant_asset(stage_name, profile)
            if existing is None:
                return None
            log_variant_event(
                stage_name=stage_name,
                event_type="variant_reused",
                status="ok",
                message="Existing variant asset reused",
                payload={
                    "profile": profile,
                    "branch_role": branch_role,
                    "source_profile": source_profile or {},
                    "asset_path": existing.abs_path,
                    "file_name": existing.file_name,
                },
            )
            append_variant_item(
                stage_name,
                self._variant_stage_item(
                    asset=existing,
                    profile=profile,
                    profile_description=profile_prompt_fragment(profile),
                    branch_role=branch_role,
                    source_profile=source_profile,
                ),
            )
            mark_completed(
                stage_name,
                profile=profile,
                branch_role=branch_role,
                source_profile=source_profile,
            )
            if stage_name == "stage4_variant_generate":
                final_assets_by_profile_key[profile_key(profile)] = asset_snapshot(existing)
                generated_final_profiles.append(
                    {"profile": profile, "branch_role": branch_role, "source_profile": source_profile or {}}
                )
            return existing

        def run_variant_remote_step(
            *,
            stage_name: str,
            profile: dict[str, str],
            branch_role: str,
            source_profile: dict[str, str] | None,
            source_asset: dict[str, Any],
            white_background: bool,
        ) -> dict[str, Any]:
            profile_description = profile_prompt_fragment(profile)
            try:
                submitted = self._submit_profile_variant_prediction(
                    Path(str(source_asset["abs_path"])),
                    entry.word,
                    profile,
                    white_background,
                    source_profile=source_profile,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "failure_phase": "submit_failed",
                    "profile": profile,
                    "branch_role": branch_role,
                    "source_profile": source_profile,
                    "source_asset": source_asset,
                    "profile_description": profile_description,
                    "error": str(exc),
                    "request_json": self._json_dict(getattr(exc, "request_json", {})),
                    "response_json": self._json_dict(getattr(exc, "response_json", {})),
                }

            request_summary = self._json_dict(submitted.get("request_summary"))
            created = self._json_dict(submitted.get("created"))
            prediction_id = str(created.get("id") or "")
            prediction_status = str(created.get("status") or "").lower() or "processing"
            status_transitions: list[str] = []

            if not prediction_id:
                return {
                    "ok": False,
                    "failure_phase": "submit_invalid",
                    "profile": profile,
                    "branch_role": branch_role,
                    "source_profile": source_profile,
                    "source_asset": source_asset,
                    "profile_description": profile_description,
                    "request_summary": request_summary,
                    "created": created,
                    "error": "variant prediction submission returned no prediction id",
                    "request_json": request_summary,
                    "response_json": created,
                }

            prediction_result = created
            last_status = prediction_status
            while last_status not in {"succeeded", "failed", "canceled"}:
                sleep(1.0)
                prediction_result = self.google_images.get_prediction(prediction_id)
                current_status = str(prediction_result.get("status") or "").lower()
                if current_status and current_status != last_status:
                    status_transitions.append(current_status)
                last_status = current_status or last_status

            if last_status != "succeeded":
                return {
                    "ok": False,
                    "failure_phase": "prediction_failed",
                    "profile": profile,
                    "branch_role": branch_role,
                    "source_profile": source_profile,
                    "source_asset": source_asset,
                    "profile_description": profile_description,
                    "request_summary": request_summary,
                    "created": created,
                    "prediction_id": prediction_id,
                    "prediction_status": last_status,
                    "status_transitions": status_transitions,
                    "prediction_result": prediction_result,
                    "error": f"variant prediction finished with status={last_status}",
                    "request_json": self._json_dict(prediction_result.get("request_json")),
                    "response_json": self._json_dict(prediction_result.get("response_json")),
                }

            try:
                payload = self._materialize_profile_variant_payload(
                    prediction_result,
                    profile=profile,
                    profile_description=profile_description,
                    white_background=white_background,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "failure_phase": "materialization_failed",
                    "profile": profile,
                    "branch_role": branch_role,
                    "source_profile": source_profile,
                    "source_asset": source_asset,
                    "profile_description": profile_description,
                    "request_summary": request_summary,
                    "created": created,
                    "prediction_id": prediction_id,
                    "prediction_status": last_status,
                    "status_transitions": status_transitions,
                    "prediction_result": prediction_result,
                    "error": str(exc),
                    "request_json": self._json_dict(getattr(exc, "request_json", {})),
                    "response_json": self._json_dict(getattr(exc, "response_json", {})),
                }

            return {
                "ok": True,
                "profile": profile,
                "branch_role": branch_role,
                "source_profile": source_profile,
                "source_asset": source_asset,
                "profile_description": profile_description,
                "request_summary": request_summary,
                "created": created,
                "prediction_id": prediction_id,
                "prediction_status": last_status,
                "status_transitions": status_transitions,
                "prediction_result": prediction_result,
                "payload": payload,
            }

        def consume_variant_result(stage_name: str, result: dict[str, Any]) -> Asset | None:
            profile = result["profile"]
            branch_role = str(result["branch_role"])
            source_profile = result.get("source_profile")
            profile_description = str(result.get("profile_description") or profile_prompt_fragment(profile))
            request_summary = self._json_dict(result.get("request_summary"))
            created = self._json_dict(result.get("created"))
            prediction_id = str(result.get("prediction_id") or created.get("id") or "")
            prediction_status = str(result.get("prediction_status") or created.get("status") or "").lower()

            if request_summary or created:
                mark_submitted(
                    stage_name,
                    profile=profile,
                    branch_role=branch_role,
                    source_profile=source_profile,
                    profile_description=profile_description,
                    prediction_id=prediction_id,
                    prediction_status=prediction_status or "processing",
                )
                log_variant_event(
                    stage_name=stage_name,
                    event_type="variant_submit_finished",
                    status="ok" if prediction_id else "error",
                    message="Variant prediction submission returned",
                    payload={
                        "profile": profile,
                        "branch_role": branch_role,
                        "source_profile": source_profile or {},
                        "request": request_summary,
                        "provider_response": created,
                    },
                )

            for provider_status in result.get("status_transitions", []):
                log_variant_event(
                    stage_name=stage_name,
                    event_type="variant_prediction_polled",
                    status="running" if provider_status not in {"succeeded", "failed", "canceled"} else provider_status,
                    message="Variant prediction status changed",
                    payload={
                        "profile": profile,
                        "branch_role": branch_role,
                        "source_profile": source_profile or {},
                        "prediction_id": prediction_id,
                        "provider_status": provider_status,
                    },
                )

            if not result.get("ok"):
                failure_phase = str(result.get("failure_phase") or "failed")
                error_text = str(result.get("error") or "variant generation failed")
                append_failure(
                    stage_name,
                    profile=profile,
                    branch_role=branch_role,
                    source_profile=source_profile,
                    profile_description=profile_description,
                    error=error_text,
                    request_json=self._json_dict(result.get("request_json")),
                    response_json=self._json_dict(result.get("response_json")),
                )
                event_type = {
                    "submit_failed": "variant_submit_failed",
                    "submit_invalid": "variant_submit_failed",
                    "prediction_failed": "variant_prediction_finished",
                    "materialization_failed": "variant_materialization_failed",
                }.get(failure_phase, "variant_prediction_finished")
                message = {
                    "submit_failed": "Variant prediction submission failed",
                    "submit_invalid": "Variant prediction submission returned no prediction id",
                    "prediction_failed": "Variant prediction finished without a usable image",
                    "materialization_failed": "Variant materialization failed",
                }.get(failure_phase, "Variant generation failed")
                payload: dict[str, Any] = {
                    "profile": profile,
                    "branch_role": branch_role,
                    "source_profile": source_profile or {},
                    "error": error_text,
                    "request_json": self._json_dict(result.get("request_json")),
                    "response_json": self._json_dict(result.get("response_json")),
                }
                if prediction_id:
                    payload["prediction_id"] = prediction_id
                if result.get("prediction_result"):
                    payload["provider_response"] = result["prediction_result"]
                log_variant_event(
                    stage_name=stage_name,
                    event_type=event_type,
                    status="error" if failure_phase != "prediction_failed" else prediction_status or "failed",
                    message=message,
                    payload=payload,
                )
                return None

            payload = result["payload"]
            variant_asset = self._save_asset(
                run_id=run.id,
                stage_name=stage_name,
                attempt=winner_attempt,
                filename=self._variant_filename(stage_name, entry, profile, winner_attempt),
                image_bytes=payload["image_bytes"],
                origin_url=payload["origin_url"],
                model_name=payload["model_name"],
            )
            append_variant_item(
                stage_name,
                self._variant_stage_item(
                    asset=variant_asset,
                    profile=profile,
                    profile_description=payload["profile_description"],
                    branch_role=branch_role,
                    source_profile=source_profile,
                    response=payload["response"],
                ),
            )
            mark_completed(
                stage_name,
                profile=profile,
                branch_role=branch_role,
                source_profile=source_profile,
                profile_description=payload["profile_description"],
            )
            log_variant_event(
                stage_name=stage_name,
                event_type="variant_asset_saved",
                status="ok",
                message="Variant image downloaded and saved",
                payload={
                    "profile": profile,
                    "branch_role": branch_role,
                    "source_profile": source_profile or {},
                    "saved_asset_path": variant_asset.abs_path,
                    "file_name": variant_asset.file_name,
                    "origin_url": payload["origin_url"],
                    "provider_response": payload["response"],
                },
            )
            if stage_name == "stage4_variant_generate":
                final_assets_by_profile_key[profile_key(profile)] = asset_snapshot(variant_asset)
                generated_final_profiles.append(
                    {"profile": profile, "branch_role": branch_role, "source_profile": source_profile or {}}
                )
            return variant_asset

        def run_variant_batch(stage_name: str, jobs: list[dict[str, Any]], *, white_background: bool) -> list[Asset]:
            self._raise_if_stop_requested(run, stage_name)
            reused_assets: list[Asset] = []
            pending_jobs: list[dict[str, Any]] = []
            for job in jobs:
                self._raise_if_stop_requested(run, stage_name)
                existing = reuse_existing_variant(stage_name, job["profile"], str(job["branch_role"]), job.get("source_profile"))
                if existing is not None:
                    reused_assets.append(existing)
                    continue
                profile_description = profile_prompt_fragment(job["profile"])
                mark_submitted(
                    stage_name,
                    profile=job["profile"],
                    branch_role=str(job["branch_role"]),
                    source_profile=job.get("source_profile"),
                    profile_description=profile_description,
                    prediction_status="queued_for_worker",
                )
                log_variant_event(
                    stage_name=stage_name,
                    event_type="variant_submit_started",
                    status="running",
                    message="Submitting variant prediction",
                    payload={
                        "profile": job["profile"],
                        "branch_role": str(job["branch_role"]),
                        "source_profile": job.get("source_profile") or {},
                        "source_asset": str(job["source_asset"]["abs_path"]),
                    },
                )
                pending_jobs.append(job)

            if not pending_jobs:
                sync_variant_progress(stage_name)
                return reused_assets

            worker_count = self._variant_pool_size(len(pending_jobs), variant_worker_limit)
            sync_variant_progress(stage_name, active_count=len(pending_jobs))
            created_assets = list(reused_assets)
            executor = ThreadPoolExecutor(max_workers=worker_count)
            try:
                future_map = {
                    executor.submit(
                        run_variant_remote_step,
                        stage_name=stage_name,
                        profile=job["profile"],
                        branch_role=str(job["branch_role"]),
                        source_profile=job.get("source_profile"),
                        source_asset=job["source_asset"],
                        white_background=white_background,
                    ): job
                    for job in pending_jobs
                }
                remaining = len(future_map)
                for future in as_completed(future_map):
                    result = future.result()
                    asset = consume_variant_result(stage_name, result)
                    if asset is not None:
                        created_assets.append(asset)
                    remaining -= 1
                    sync_variant_progress(stage_name, active_count=remaining)
                    self._raise_if_stop_requested(run, stage_name)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            return created_assets

        self._raise_if_stop_requested(run, "stage4_variant_generate")
        self.repo.update_run(run, current_stage="stage4_variant_generate")
        sync_variant_progress("stage4_variant_generate")
        sync_variant_progress("stage5_variant_white_bg")
        log_variant_event(
            stage_name="stage4_variant_generate",
            event_type="stage_started",
            status="running",
            message="Stage 5 variant final generation started",
            payload={
                "source_asset": upgraded_asset.abs_path,
                "planned_profiles": planned_profiles,
                "branch_plan": branch_plan,
                "image_aspect_ratio": aspect_ratio,
                "image_resolution": image_size,
                "sequence": [
                    "white_male_age_variants_from_stage3",
                    "female_seed_from_stage3",
                    "white_female_age_variants_from_female_seed",
                    "appearance_variants_from_matching_white_gender_age_baseline",
                ],
            },
        )

        male_age_jobs = [
            {
                "profile": profile,
                "branch_role": "male_age_variant",
                "source_profile": branch_plan["base_profile"],
                "source_asset": asset_snapshot(upgraded_asset),
            }
            for profile in branch_plan.get("male_age_variants", [])
        ]

        female_seed_profile = branch_plan.get("female_seed")
        if female_seed_profile:
            male_age_jobs.append(
                {
                    "profile": female_seed_profile,
                    "branch_role": "female_seed",
                    "source_profile": branch_plan["base_profile"],
                    "source_asset": asset_snapshot(upgraded_asset),
                }
            )

        initial_assets = run_variant_batch(
            "stage4_variant_generate",
            male_age_jobs,
            white_background=False,
        )

        female_seed_asset: dict[str, Any] | None = None
        if female_seed_profile:
            female_seed_asset = final_assets_by_profile_key.get(profile_key(female_seed_profile))
            if female_seed_asset is None:
                fallback_asset = next(
                    (asset for asset in initial_assets if asset and asset.file_name == self._variant_filename("stage4_variant_generate", entry, female_seed_profile, winner_attempt)),
                    None,
                )
                if fallback_asset is not None:
                    female_seed_asset = asset_snapshot(fallback_asset)

        female_age_jobs: list[dict[str, Any]] = []
        for profile in branch_plan.get("female_age_variants", []):
            if female_seed_asset is None:
                append_failure(
                    "stage4_variant_generate",
                    profile=profile,
                    branch_role="female_age_variant",
                    source_profile=female_seed_profile,
                    profile_description=profile_prompt_fragment(profile),
                    error="female seed baseline is missing",
                )
                sync_variant_progress("stage4_variant_generate")
                continue
            female_age_jobs.append(
                {
                    "profile": profile,
                    "branch_role": "female_age_variant",
                    "source_profile": female_seed_profile,
                    "source_asset": female_seed_asset,
                }
            )

        run_variant_batch(
            "stage4_variant_generate",
            female_age_jobs,
            white_background=False,
        )

        appearance_jobs: list[dict[str, Any]] = []
        for profile in branch_plan.get("appearance_variants", []):
            source_profile = {
                "gender": profile.get("gender", ""),
                "age": profile.get("age", ""),
                "skin_color": "white",
            }
            source_asset = final_assets_by_profile_key.get(profile_key(source_profile))
            if source_asset is None:
                append_failure(
                    "stage4_variant_generate",
                    profile=profile,
                    branch_role="appearance_variant",
                    source_profile=source_profile,
                    profile_description=profile_prompt_fragment(profile),
                    error="matching white gender-age baseline is missing",
                )
                sync_variant_progress("stage4_variant_generate")
                continue
            appearance_jobs.append(
                {
                    "profile": profile,
                    "branch_role": "appearance_variant",
                    "source_profile": source_profile,
                    "source_asset": source_asset,
                }
            )

        run_variant_batch(
            "stage4_variant_generate",
            appearance_jobs,
            white_background=False,
        )

        final_stage_status = "error" if stage_failures("stage4_variant_generate") else "ok"
        sync_variant_progress("stage4_variant_generate", final_stage_status, active_count=0)
        log_variant_event(
            stage_name="stage4_variant_generate",
            event_type="stage_completed",
            status=final_stage_status,
            message="Stage 5 variant final generation finished",
            payload={
                "completed_count": len(stage_variants("stage4_variant_generate")),
                "failed_count": len(stage_failures("stage4_variant_generate")),
            },
        )

        self._raise_if_stop_requested(run, "stage5_variant_white_bg")
        self.repo.update_run(run, current_stage="stage5_variant_white_bg")
        log_variant_event(
            stage_name="stage5_variant_white_bg",
            event_type="stage_started",
            status="running",
            message="Stage 6 variant white-background generation started",
            payload={
                "source_asset": "derived_from_matching_stage4_variant_asset",
                "planned_profiles": planned_profiles,
                "branch_plan": branch_plan,
                "image_aspect_ratio": aspect_ratio,
                "image_resolution": image_size,
                "sequence": ["white_background_for_all_stage5_to_stage8_outputs"],
            },
        )

        white_bg_jobs: list[dict[str, Any]] = []
        for item in generated_final_profiles:
            source_asset = final_assets_by_profile_key.get(profile_key(item["profile"]))
            if source_asset is None:
                append_failure(
                    "stage5_variant_white_bg",
                    profile=item["profile"],
                    branch_role=str(item["branch_role"]),
                    source_profile=item.get("profile"),
                    profile_description=profile_prompt_fragment(item["profile"]),
                    error="matching final variant asset is missing",
                )
                sync_variant_progress("stage5_variant_white_bg")
                continue
            white_bg_jobs.append(
                {
                    "profile": item["profile"],
                    "branch_role": str(item["branch_role"]),
                    "source_profile": item.get("profile"),
                    "source_asset": source_asset,
                }
            )

        run_variant_batch(
            "stage5_variant_white_bg",
            white_bg_jobs,
            white_background=True,
        )

        white_stage_status = "error" if stage_failures("stage5_variant_white_bg") else "ok"
        sync_variant_progress("stage5_variant_white_bg", white_stage_status, active_count=0)
        log_variant_event(
            stage_name="stage5_variant_white_bg",
            event_type="stage_completed",
            status=white_stage_status,
            message="Stage 6 variant white-background generation finished",
            payload={
                "completed_count": len(stage_variants("stage5_variant_white_bg")),
                "failed_count": len(stage_failures("stage5_variant_white_bg")),
            },
        )
        if stage_failures("stage4_variant_generate") or stage_failures("stage5_variant_white_bg"):
            if stage_failures("stage4_variant_generate"):
                self.repo.update_run(run, current_stage="stage4_variant_generate")
            self._raise_with_context(
                "; ".join(
                    [failure["error"] for failure in stage_failures("stage4_variant_generate")]
                    + [failure["error"] for failure in stage_failures("stage5_variant_white_bg")]
                ),
                request_json={
                    "stage4_variant_generate_failures": stage_failures("stage4_variant_generate"),
                    "stage5_variant_white_bg_failures": stage_failures("stage5_variant_white_bg"),
                },
                response_json={},
            )

    def _submit_profile_variant_prediction(
        self,
        image_path: Path,
        word: str,
        profile: dict[str, str],
        white_background: bool,
        *,
        source_profile: dict[str, str] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> dict[str, Any]:
        profile_description = profile_prompt_fragment(profile)
        edit_instruction = profile_edit_instruction(profile, source_profile)
        request_summary = self.google_images.profile_variant_request_summary(
            image_path,
            word=word,
            profile_description=profile_description,
            white_background=white_background,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            edit_instruction=edit_instruction,
        )
        try:
            created = self.google_images.submit_nano_banana_profile_variant(
                image_path,
                word=word,
                profile_description=profile_description,
                white_background=white_background,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                edit_instruction=edit_instruction,
            )
        except Exception as exc:  # noqa: BLE001
            self._merge_error_context(exc, request_json=request_summary)
            raise
        return {
            "profile_description": profile_description,
            "edit_instruction": edit_instruction,
            "request_summary": request_summary,
            "created": created,
        }

    def _materialize_profile_variant_payload(
        self,
        prediction_result: dict[str, Any],
        *,
        profile: dict[str, str],
        profile_description: str,
        white_background: bool,
    ) -> dict[str, Any]:
        profile_suffix = self._variant_suffix(profile)
        if prediction_result.get("status") != "succeeded":
            stage_label = "white background variant" if white_background else "variant generation"
            self._raise_with_context(
                f"{stage_label} failed for {profile_suffix}: {prediction_result.get('status')}",
                request_json=self._json_dict(prediction_result.get("request_json")),
                response_json=self._json_dict(prediction_result.get("response_json")),
            )
        output_url = self.replicate.extract_output_url(prediction_result)
        if not output_url:
            stage_label = "white background variant" if white_background else "variant generation"
            self._raise_with_context(
                f"No output URL for {stage_label} {profile_suffix}",
                request_json=self._json_dict(prediction_result.get("request_json")),
                response_json=prediction_result,
            )
        image_bytes = self._download_generated_image(output_url)
        return {
            "profile_description": profile_description,
            "response": prediction_result,
            "origin_url": output_url,
            "image_bytes": image_bytes,
            "model_name": str(prediction_result.get("model") or "gemini-3.1-flash-image-preview"),
        }

    def _run_stage4_attempt(self, *, run: Run, entry: Entry, winner_attempt: int, winner_score: float) -> None:
        upgraded_asset = self._asset_for_attempt(run.id, "stage3_upgraded", winner_attempt)
        if upgraded_asset is None:
            raise RuntimeError(f"Missing stage3 upgraded image for winner attempt {winner_attempt}")
        runtime_config = self.repo.get_runtime_config()
        self._record_event(
            run_id=run.id,
            stage_name="stage4_background",
            attempt=winner_attempt,
            event_type="stage_started",
            status="running",
            message="Stage 4 white background generation started",
            payload={
                "source_image_path": upgraded_asset.abs_path,
                "winner_attempt": winner_attempt,
                "winner_score": winner_score,
                "model": "nano-banana-2",
                "provider_model": "gemini-3.1-flash-image-preview",
                "image_aspect_ratio": runtime_config.image_aspect_ratio,
                "image_resolution": runtime_config.image_resolution,
            },
        )

        start = perf_counter()
        try:
            result = self.google_images.nano_banana_white_bg(
                Path(upgraded_asset.abs_path),
                entry.word,
                aspect_ratio=runtime_config.image_aspect_ratio,
                image_size=runtime_config.image_resolution,
            )
        except Exception as exc:  # noqa: BLE001
            self._merge_error_context(
                exc,
                request_json={
                    "input_asset": upgraded_asset.abs_path,
                    "winner_attempt": winner_attempt,
                    "winner_score": winner_score,
                    "model": "nano-banana-2",
                    "provider_model": "gemini-3.1-flash-image-preview",
                    "word": entry.word,
                    "image_aspect_ratio": runtime_config.image_aspect_ratio,
                    "image_resolution": runtime_config.image_resolution,
                },
            )
            raise
        if result.get("status") != "succeeded":
            self._raise_with_context(
                f"Nano banana failed: {result.get('status')}",
                request_json={
                    "input_asset": upgraded_asset.abs_path,
                    "winner_attempt": winner_attempt,
                    "winner_score": winner_score,
                    "model": "nano-banana-2",
                    "provider_model": "gemini-3.1-flash-image-preview",
                    "word": entry.word,
                    "image_aspect_ratio": runtime_config.image_aspect_ratio,
                    "image_resolution": runtime_config.image_resolution,
                },
                response_json={"generation": result if isinstance(result, dict) else {}},
            )

        output_url = self.replicate.extract_output_url(result)
        if not output_url:
            self._raise_with_context(
                "No output URL for stage4",
                request_json={
                    "input_asset": upgraded_asset.abs_path,
                    "winner_attempt": winner_attempt,
                    "winner_score": winner_score,
                    "model": "nano-banana-2",
                    "provider_model": "gemini-3.1-flash-image-preview",
                    "word": entry.word,
                    "image_aspect_ratio": runtime_config.image_aspect_ratio,
                    "image_resolution": runtime_config.image_resolution,
                },
                response_json={"generation": result if isinstance(result, dict) else {}},
            )

        image_bytes = self._download_generated_image(output_url)
        filename = f"stage4_white_bg_{self._entry_slug(entry)}_attempt_{winner_attempt}.jpg"
        self._save_asset(
            run_id=run.id,
            stage_name="stage4_white_bg",
            attempt=winner_attempt,
            filename=filename,
            image_bytes=image_bytes,
            origin_url=output_url,
            model_name="gemini-3.1-flash-image-preview",
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
                "image_aspect_ratio": runtime_config.image_aspect_ratio,
                "image_resolution": runtime_config.image_resolution,
            },
            response_json={**result, "provider": "google"},
        )
        self._record_event(
            run_id=run.id,
            stage_name="stage4_background",
            attempt=winner_attempt,
            event_type="stage_completed",
            status="ok",
            message="Stage 4 white background generation completed",
            payload={
                "source_image_path": upgraded_asset.abs_path,
                "saved_image_path": self._asset_for_attempt(run.id, "stage4_white_bg", winner_attempt).abs_path if self._asset_for_attempt(run.id, "stage4_white_bg", winner_attempt) else "",
                "winner_attempt": winner_attempt,
                "winner_score": winner_score,
                "provider_model": "gemini-3.1-flash-image-preview",
                "response_json": result,
            },
        )

        logger.info(
            "stage completed",
            extra={
                "run_id": run.id,
                "stage_name": "stage4_background",
                "status": "ok",
                "provider": "google",
                "latency_ms": round((perf_counter() - start) * 1000, 2),
            },
        )

    def _run_quality_gate_attempt(self, *, run: Run, entry: Entry, attempt: int) -> tuple[float, bool, dict[str, Any]]:
        final_asset = self._asset_for_attempt(run.id, "stage3_upgraded", attempt)
        if final_asset is None:
            raise RuntimeError(f"Missing stage3 upgraded image for attempt {attempt}")
        self._record_event(
            run_id=run.id,
            stage_name="quality_gate",
            attempt=attempt,
            event_type="stage_started",
            status="running",
            message="Quality gate started",
            payload={"asset_path": final_asset.abs_path},
        )

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
        self._record_event(
            run_id=run.id,
            stage_name="quality_gate",
            attempt=attempt,
            event_type="stage_completed",
            status="ok",
            message="Quality gate completed",
            payload={
                "score": score,
                "passed": passed,
                "threshold": run.quality_threshold,
            },
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
