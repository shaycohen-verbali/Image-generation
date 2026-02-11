from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Select, desc, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Asset, Entry, Export, Prompt, Run, RuntimeConfig, Score, StageResult
from app.services.utils import deterministic_entry_id, source_row_hash

MIN_QUALITY_THRESHOLD = 95


def _dumps(value: dict[str, Any] | list[Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _loads(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


class Repository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_runtime_config(self) -> RuntimeConfig:
        config = self.db.execute(select(RuntimeConfig).where(RuntimeConfig.id == 1)).scalar_one_or_none()
        if config is None:
            raise RuntimeError("Runtime config not initialized")
        return config

    def update_runtime_config(self, updates: dict[str, Any]) -> RuntimeConfig:
        config = self.get_runtime_config()
        for key, value in updates.items():
            if value is not None and hasattr(config, key):
                setattr(config, key, value)
        config.quality_threshold = max(MIN_QUALITY_THRESHOLD, int(config.quality_threshold))
        self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        return config

    def create_entry(self, payload: dict[str, Any]) -> Entry:
        entry_id = deterministic_entry_id(payload["word"], payload["part_of_sentence"], payload["category"])
        row_hash = source_row_hash(payload)

        existing = self.db.execute(select(Entry).where(Entry.id == entry_id)).scalar_one_or_none()
        if existing:
            return existing

        entry = Entry(
            id=entry_id,
            word=payload["word"].strip(),
            part_of_sentence=payload["part_of_sentence"].strip(),
            category=payload["category"].strip(),
            context=payload.get("context", "").strip(),
            boy_or_girl=payload.get("boy_or_girl", "").strip(),
            batch=str(payload.get("batch", "")).strip(),
            source_row_hash=row_hash,
        )
        self.db.add(entry)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            entry = self.db.execute(select(Entry).where(Entry.id == entry_id)).scalar_one()
            return entry

        self.db.refresh(entry)
        return entry

    def list_entries(
        self,
        *,
        word: str | None = None,
        part_of_sentence: str | None = None,
        category: str | None = None,
        batch: str | None = None,
        status: str | None = None,
        min_score: float | None = None,
        max_score: float | None = None,
    ) -> list[tuple[Entry, Run | None]]:
        stmt: Select[tuple[Entry]] = select(Entry)
        if word:
            stmt = stmt.where(Entry.word.ilike(f"%{word}%"))
        if part_of_sentence:
            stmt = stmt.where(Entry.part_of_sentence == part_of_sentence)
        if category:
            stmt = stmt.where(Entry.category == category)
        if batch:
            stmt = stmt.where(Entry.batch == batch)

        entries = list(self.db.execute(stmt.order_by(Entry.word.asc())).scalars())
        output: list[tuple[Entry, Run | None]] = []
        for entry in entries:
            latest_run = self.db.execute(
                select(Run).where(Run.entry_id == entry.id).order_by(desc(Run.created_at)).limit(1)
            ).scalar_one_or_none()
            if status and (latest_run is None or latest_run.status != status):
                continue
            if min_score is not None and (latest_run is None or latest_run.quality_score is None or latest_run.quality_score < min_score):
                continue
            if max_score is not None and (latest_run is None or latest_run.quality_score is None or latest_run.quality_score > max_score):
                continue
            output.append((entry, latest_run))
        return output

    def create_runs(
        self,
        entry_ids: list[str],
        *,
        quality_threshold: int,
        max_optimization_attempts: int,
    ) -> list[Run]:
        threshold = max(MIN_QUALITY_THRESHOLD, int(quality_threshold))
        runs: list[Run] = []
        for entry_id in entry_ids:
            run = Run(
                entry_id=entry_id,
                status="queued",
                current_stage="queued",
                quality_threshold=threshold,
                max_optimization_attempts=max_optimization_attempts,
            )
            self.db.add(run)
            runs.append(run)
        self.db.commit()
        for run in runs:
            self.db.refresh(run)
        return runs

    def get_run(self, run_id: str) -> Run | None:
        return self.db.execute(select(Run).where(Run.id == run_id)).scalar_one_or_none()

    def list_runs(
        self,
        *,
        status: str | None = None,
        entry_id: str | None = None,
        min_score: float | None = None,
        max_score: float | None = None,
    ) -> list[Run]:
        stmt = select(Run)
        if status:
            stmt = stmt.where(Run.status == status)
        if entry_id:
            stmt = stmt.where(Run.entry_id == entry_id)
        if min_score is not None:
            stmt = stmt.where(Run.quality_score >= min_score)
        if max_score is not None:
            stmt = stmt.where(Run.quality_score <= max_score)
        stmt = stmt.order_by(desc(Run.created_at))
        return list(self.db.execute(stmt).scalars())

    def get_entry(self, entry_id: str) -> Entry | None:
        return self.db.execute(select(Entry).where(Entry.id == entry_id)).scalar_one_or_none()

    def claim_next_queued_run(self) -> Run | None:
        candidate = self.db.execute(
            select(Run).where(Run.status.in_(["queued", "retry_queued"])).order_by(Run.created_at.asc()).limit(1)
        ).scalar_one_or_none()
        if candidate is None:
            return None

        updated = self.db.execute(
            update(Run)
            .where(Run.id == candidate.id)
            .where(Run.status.in_(["queued", "retry_queued"]))
            .values(status="running", current_stage=candidate.retry_from_stage or candidate.current_stage)
        )
        if updated.rowcount == 0:
            self.db.rollback()
            return None

        self.db.commit()
        return self.get_run(candidate.id)

    def update_run(self, run: Run, **updates: Any) -> Run:
        for key, value in updates.items():
            setattr(run, key, value)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def add_stage_result(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        status: str,
        idempotency_key: str,
        request_json: dict[str, Any],
        response_json: dict[str, Any],
        error_detail: str = "",
    ) -> StageResult:
        existing = self.db.execute(
            select(StageResult)
            .where(StageResult.run_id == run_id)
            .where(StageResult.stage_name == stage_name)
            .where(StageResult.attempt == attempt)
        ).scalar_one_or_none()
        if existing is not None:
            existing.status = status
            existing.request_json = _dumps(request_json)
            existing.response_json = _dumps(response_json)
            existing.error_detail = error_detail
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        record = StageResult(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            status=status,
            idempotency_key=idempotency_key,
            request_json=_dumps(request_json),
            response_json=_dumps(response_json),
            error_detail=error_detail,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def add_prompt(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        prompt_text: str,
        needs_person: str,
        source: str,
        raw_response_json: dict[str, Any],
    ) -> Prompt:
        prompt = Prompt(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            prompt_text=prompt_text,
            needs_person=needs_person,
            source=source,
            raw_response_json=_dumps(raw_response_json),
        )
        self.db.add(prompt)
        self.db.commit()
        self.db.refresh(prompt)
        return prompt

    def add_asset(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        file_name: str,
        abs_path: str,
        mime_type: str,
        sha256: str,
        width: int,
        height: int,
        origin_url: str,
        model_name: str,
    ) -> Asset:
        asset = Asset(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            file_name=file_name,
            abs_path=abs_path,
            mime_type=mime_type,
            sha256=sha256,
            width=width,
            height=height,
            origin_url=origin_url,
            model_name=model_name,
        )
        self.db.add(asset)
        self.db.commit()
        self.db.refresh(asset)
        return asset

    def add_score(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        score_0_100: float,
        pass_fail: bool,
        rubric_json: dict[str, Any],
    ) -> Score:
        score = Score(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            score_0_100=score_0_100,
            pass_fail=pass_fail,
            rubric_json=_dumps(rubric_json),
        )
        self.db.add(score)
        self.db.commit()
        self.db.refresh(score)
        return score

    def run_details(self, run_id: str) -> tuple[Run | None, list[StageResult], list[Prompt], list[Asset], list[Score]]:
        run = self.get_run(run_id)
        if run is None:
            return None, [], [], [], []
        stages = list(
            self.db.execute(
                select(StageResult)
                .where(StageResult.run_id == run_id)
                .order_by(StageResult.created_at.asc())
            ).scalars()
        )
        prompts = list(
            self.db.execute(
                select(Prompt)
                .where(Prompt.run_id == run_id)
                .order_by(Prompt.created_at.asc())
            ).scalars()
        )
        assets = list(
            self.db.execute(
                select(Asset)
                .where(Asset.run_id == run_id)
                .order_by(Asset.created_at.asc())
            ).scalars()
        )
        scores = list(
            self.db.execute(
                select(Score)
                .where(Score.run_id == run_id)
                .order_by(Score.created_at.asc())
            ).scalars()
        )
        return run, stages, prompts, assets, scores

    def get_asset(self, asset_id: str) -> Asset | None:
        return self.db.execute(select(Asset).where(Asset.id == asset_id)).scalar_one_or_none()

    def create_export(self, filter_json: dict[str, Any]) -> Export:
        record = Export(filter_json=_dumps(filter_json), status="pending")
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def update_export(self, export: Export, **updates: Any) -> Export:
        for key, value in updates.items():
            setattr(export, key, value)
        self.db.add(export)
        self.db.commit()
        self.db.refresh(export)
        return export

    def get_export(self, export_id: str) -> Export | None:
        return self.db.execute(select(Export).where(Export.id == export_id)).scalar_one_or_none()

    def list_runs_for_export(self, filters: dict[str, Any]) -> list[tuple[Run, Entry]]:
        stmt = select(Run, Entry).join(Entry, Entry.id == Run.entry_id)

        entry_ids = filters.get("entry_ids")
        run_ids = filters.get("run_ids")
        statuses = filters.get("status")
        min_score = filters.get("min_score")
        max_score = filters.get("max_score")

        if entry_ids:
            stmt = stmt.where(Run.entry_id.in_(entry_ids))
        if run_ids:
            stmt = stmt.where(Run.id.in_(run_ids))
        if statuses:
            stmt = stmt.where(Run.status.in_(statuses))
        if min_score is not None:
            stmt = stmt.where(Run.quality_score >= float(min_score))
        if max_score is not None:
            stmt = stmt.where(Run.quality_score <= float(max_score))

        stmt = stmt.order_by(Run.created_at.asc())
        return list(self.db.execute(stmt).all())

    @staticmethod
    def json_field_dict(value: str) -> dict[str, Any]:
        return _loads(value)

    def retry_run_from_last_failure(self, run: Run) -> Run:
        last_failed_stage = self.db.execute(
            select(StageResult)
            .where(StageResult.run_id == run.id)
            .where(StageResult.status.in_(["failed", "error", "timeout"]))
            .order_by(desc(StageResult.created_at))
            .limit(1)
        ).scalar_one_or_none()
        retry_stage = last_failed_stage.stage_name if last_failed_stage else "stage1_prompt"
        run.status = "retry_queued"
        run.retry_from_stage = retry_stage
        run.error_detail = ""
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def count_runs(self) -> int:
        return self.db.execute(select(func.count()).select_from(Run)).scalar_one()
