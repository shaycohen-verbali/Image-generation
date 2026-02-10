from __future__ import annotations

import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import Asset, Entry, Prompt, Run
from app.services.openai_client import OpenAIClient
from app.services.prompt_templates import build_stage1_prompt, build_stage3_prompt
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
            self._record_stage(
                run_id=run.id,
                stage_name=run.current_stage,
                attempt=max(1, run.optimization_attempt),
                status="error",
                request_json={},
                response_json={},
                error_detail=str(exc),
            )
            return self.repo.get_run(run.id) or run

        return self.repo.get_run(run.id) or run

    def _run_stage1(self, run: Run, entry: Entry, assistant_id: str, retry_limit: int) -> Run:
        run = self.repo.update_run(run, current_stage="stage1_prompt")

        def _exec():
            start = perf_counter()
            prompt_payload = build_stage1_prompt(entry)
            parsed, raw = self.openai.generate_first_prompt(prompt_payload, assistant_id)
            first_prompt = parsed.get("first prompt") or parsed.get("prompt") or parsed.get("first_prompt")
            if not first_prompt:
                raise RuntimeError("Missing 'first prompt' in assistant response")
            need_person = str(parsed.get("need a person", parsed.get("need_person", "no"))).strip().lower()
            if need_person not in {"yes", "no"}:
                need_person = "no"
            self.repo.add_prompt(
                run_id=run.id,
                stage_name="stage1_prompt",
                attempt=0,
                prompt_text=first_prompt,
                needs_person=need_person,
                source="assistant",
                raw_response_json={"parsed": parsed, "raw": raw},
            )
            self._record_stage(
                run_id=run.id,
                stage_name="stage1_prompt",
                attempt=0,
                status="ok",
                request_json={"prompt": prompt_payload},
                response_json={"parsed": parsed, "raw": raw},
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
            filename = f"stage2_draft_{sanitize_filename(entry.word)}.jpg"
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

            run = self.repo.update_run(run, current_stage="stage4_background", optimization_attempt=current_attempt)
            self._execute_with_stage_retry(
                retry_limit,
                lambda: self._run_stage4_attempt(
                    run=run,
                    entry=entry,
                    attempt=current_attempt,
                ),
            )

            run = self.repo.update_run(run, current_stage="quality_gate", optimization_attempt=current_attempt)
            score, passed, rubric = self._execute_with_stage_retry(
                retry_limit,
                lambda: self._run_quality_gate_attempt(
                    run=run,
                    entry=entry,
                    attempt=current_attempt,
                ),
            )

            if passed:
                run = self.repo.update_run(
                    run,
                    status="completed_pass",
                    current_stage="completed",
                    quality_score=score,
                    error_detail="",
                )
                return run

            previous_score_explanation = str(rubric.get("explanation", ""))
            if current_attempt >= total_attempt_budget:
                run = self.repo.update_run(
                    run,
                    status="completed_fail_threshold",
                    current_stage="completed",
                    quality_score=score,
                    error_detail=f"Final score {score} below threshold {run.quality_threshold}",
                )
                return run

            current_attempt += 1

        run = self.repo.update_run(
            run,
            status="completed_fail_threshold",
            current_stage="completed",
            error_detail="attempt budget exhausted",
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
        critique_source_asset = self._latest_asset(run.id, "stage4_white_bg") or self._latest_asset(run.id, "stage2_draft")
        if critique_source_asset is None:
            raise RuntimeError("No source asset available for stage 3")
        critique_path = Path(critique_source_asset.abs_path)

        start = perf_counter()
        analysis, analysis_raw = self.openai.analyze_image(
            critique_path,
            entry.word,
            entry.part_of_sentence,
            entry.category,
            model=self.repo.get_runtime_config().openai_model_vision,
        )

        previous_prompt = self._latest_prompt(run.id, "stage3_upgrade") or self._latest_prompt(run.id, "stage1_prompt")
        if previous_prompt is None:
            raise RuntimeError("No prior prompt to upgrade")

        recommendations = str(analysis.get("recommendations", ""))
        if previous_score_explanation:
            recommendations = f"{recommendations}\nPrevious score feedback: {previous_score_explanation}"

        upgrade_request = build_stage3_prompt(
            entry,
            old_prompt=previous_prompt.prompt_text,
            challenges=str(analysis.get("challenges", "")),
            recommendations=recommendations,
        )

        parsed, raw = self.openai.generate_upgraded_prompt(upgrade_request, assistant_id)
        upgraded_prompt = parsed.get("upgraded prompt") or parsed.get("prompt")
        if not upgraded_prompt:
            raise RuntimeError("Missing upgraded prompt")

        self.repo.add_prompt(
            run_id=run.id,
            stage_name="stage3_upgrade",
            attempt=attempt,
            prompt_text=upgraded_prompt,
            needs_person="",
            source="assistant",
            raw_response_json={"parsed": parsed, "raw": raw, "analysis": analysis, "analysis_raw": analysis_raw},
        )

        flux_result = self.replicate.flux_pro(upgraded_prompt)
        model_name = "black-forest-labs/flux-1.1-pro"
        if flux_result.get("status") != "succeeded":
            fallback_enabled = self.repo.get_runtime_config().flux_imagen_fallback_enabled
            if not fallback_enabled:
                raise RuntimeError(f"FLUX Pro failed and fallback disabled: {flux_result.get('status')}")
            flux_result = self.replicate.imagen_fallback(upgraded_prompt)
            model_name = "google/imagen-3-fast"
            if flux_result.get("status") != "succeeded":
                raise RuntimeError(f"Fallback failed: {flux_result.get('status')}")

        output_url = self.replicate.extract_output_url(flux_result)
        if not output_url:
            raise RuntimeError("No output URL for stage3 upgraded image")
        image_bytes = self.replicate.download_image(output_url)

        filename = f"stage3_upgraded_attempt_{attempt}.jpg"
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
            request_json={"upgrade_prompt_request": upgrade_request},
            response_json={
                "analysis": analysis,
                "assistant": {"parsed": parsed, "raw": raw},
                "generation": flux_result,
                "generation_model": model_name,
            },
        )

        write_metadata(
            run.id,
            attempt,
            {
                "attempt": attempt,
                "stage3": {
                    "analysis": analysis,
                    "assistant": {"parsed": parsed, "raw": raw},
                    "generation": flux_result,
                    "generation_model": model_name,
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

    def _run_stage4_attempt(self, *, run: Run, entry: Entry, attempt: int) -> None:
        upgraded_asset = self._latest_asset(run.id, "stage3_upgraded")
        if upgraded_asset is None:
            raise RuntimeError("Missing stage3 upgraded image")

        start = perf_counter()
        result = self.replicate.nano_banana_white_bg(Path(upgraded_asset.abs_path), entry.word)
        if result.get("status") != "succeeded":
            raise RuntimeError(f"Nano banana failed: {result.get('status')}")

        output_url = self.replicate.extract_output_url(result)
        if not output_url:
            raise RuntimeError("No output URL for stage4")

        image_bytes = self.replicate.download_image(output_url)
        filename = f"stage4_white_bg_attempt_{attempt}.jpg"
        self._save_asset(
            run_id=run.id,
            stage_name="stage4_white_bg",
            attempt=attempt,
            filename=filename,
            image_bytes=image_bytes,
            origin_url=output_url,
            model_name="google/nano-banana",
        )

        self._record_stage(
            run_id=run.id,
            stage_name="stage4_background",
            attempt=attempt,
            status="ok",
            request_json={"input_asset": upgraded_asset.abs_path},
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
        final_asset = self._latest_asset(run.id, "stage4_white_bg")
        if final_asset is None:
            raise RuntimeError("Missing stage4 white background image")

        start = perf_counter()
        config = self.repo.get_runtime_config()
        rubric, raw = self.openai.score_image(
            Path(final_asset.abs_path),
            word=entry.word,
            part_of_sentence=entry.part_of_sentence,
            category=entry.category,
            threshold=run.quality_threshold,
            model=config.openai_model_vision,
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
            request_json={"asset": final_asset.abs_path, "threshold": run.quality_threshold},
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
