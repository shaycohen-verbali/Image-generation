from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import (
    AssetOut,
    BatchJobReportOut,
    BatchJobSummaryOut,
    DeleteRunsResponse,
    PromptOut,
    RunEventOut,
    RetryRunResponse,
    RunDetailOut,
    RunOut,
    RunsCreateRequest,
    ScoreOut,
    StopRunResponse,
    StageResultOut,
)
from app.services.cost_estimator import summarize_run_costs
from app.services.repository import Repository

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

MAX_TEXT_LEN = 2000


def _json_dict(value: str) -> dict:
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        return {}


def _truncate_text(value: str, *, max_len: int = MAX_TEXT_LEN) -> str:
    text = str(value or "")
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}... [truncated {len(text) - max_len} chars]"


def _sanitize_payload(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if key in {"inlineData", "inline_data"} and isinstance(item, dict):
                data = str(item.get("data") or "").strip()
                sanitized[key] = {
                    **{k: _sanitize_payload(v) for k, v in item.items() if k != "data"},
                    "data": f"<redacted base64; chars={len(data)}>",
                }
                continue
            if key == "data" and isinstance(item, str) and len(item) > 256:
                sanitized[key] = f"<redacted base64; chars={len(item)}>"
                continue
            if key in {"text", "raw_text", "prompt_text", "prompt", "content"} and isinstance(item, str):
                sanitized[key] = _truncate_text(item)
                continue
            sanitized[key] = _sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _truncate_text(value)
    return value


def _run_out(run, entry, *, cost_summary: dict | None = None) -> RunOut:
    cost_summary = cost_summary or {}
    batch = entry.batch if entry else ""
    batch_job = None
    if cost_summary.get("batch_job"):
        batch_job = BatchJobSummaryOut(**cost_summary["batch_job"])
    return RunOut(
        id=run.id,
        entry_id=run.entry_id,
        word=entry.word if entry else "",
        part_of_sentence=entry.part_of_sentence if entry else "",
        category=entry.category if entry else "",
        batch=batch,
        batch_job=batch_job,
        status=run.status,
        current_stage=run.current_stage,
        quality_score=run.quality_score,
        quality_threshold=run.quality_threshold,
        optimization_attempt=run.optimization_attempt,
        max_optimization_attempts=run.max_optimization_attempts,
        technical_retry_count=run.technical_retry_count,
        error_detail=run.error_detail,
        estimated_total_cost_usd=float(cost_summary.get("estimated_total_cost_usd") or 0),
        estimated_cost_per_image_usd=cost_summary.get("estimated_cost_per_image_usd"),
        image_count=int(cost_summary.get("image_count") or 0),
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _profile_label(item: dict) -> str:
    profile = item.get("profile") if isinstance(item, dict) else {}
    if not isinstance(profile, dict):
        profile = {}
    parts = [str(profile.get("gender") or "").strip(), str(profile.get("age") or "").strip(), str(profile.get("skin_color") or "").strip()]
    label = "/".join(part for part in parts if part)
    branch_role = str(item.get("branch_role") or "").strip() if isinstance(item, dict) else ""
    prediction_status = str(item.get("prediction_status") or "").strip() if isinstance(item, dict) else ""
    prediction_id = str(item.get("prediction_id") or "").strip() if isinstance(item, dict) else ""
    suffix_parts: list[str] = []
    if branch_role:
        suffix_parts.append(branch_role)
    if prediction_status:
        suffix_parts.append(prediction_status)
    if prediction_id:
        suffix_parts.append(prediction_id[:10])
    if suffix_parts:
        return f"{label or 'profile'} ({'; '.join(suffix_parts)})"
    return label or "profile"


def _build_legacy_execution_log(run, stages: list, assets: list, scores: list) -> str:
    lines = [
        (
            f"run_id={run.id} status={run.status} current_stage={run.current_stage} "
            f"attempt={run.optimization_attempt} tech_retries={run.technical_retry_count} "
            f"updated_at={run.updated_at.isoformat()}"
        )
    ]
    for stage in stages:
        request_json = _json_dict(stage.request_json)
        response_json = _json_dict(stage.response_json)
        line = f"{stage.created_at.isoformat()} stage={stage.stage_name} attempt={stage.attempt} status={stage.status}"
        if stage.stage_name in {"stage4_variant_generate", "stage5_variant_white_bg"}:
            progress = response_json.get("progress") if isinstance(response_json.get("progress"), dict) else {}
            line += (
                f" completed={int(progress.get('completed_count') or 0)}"
                f" in_flight={int(progress.get('in_flight_count') or 0)}"
                f" remaining={int(progress.get('remaining_count') or 0)}"
                f" failed={int(progress.get('failed_count') or 0)}"
            )
            submitted_profiles = response_json.get("submitted_profiles")
            completed_profiles = response_json.get("completed_profiles")
            failed_profiles = response_json.get("failed_profiles")
            if isinstance(submitted_profiles, list) and submitted_profiles:
                line += f" submitted=[{', '.join(_profile_label(item) for item in submitted_profiles[:8])}]"
            if isinstance(completed_profiles, list) and completed_profiles:
                line += f" completed_profiles=[{', '.join(_profile_label(item) for item in completed_profiles[:8])}]"
            if isinstance(failed_profiles, list) and failed_profiles:
                line += f" failed_profiles=[{', '.join(_profile_label(item) for item in failed_profiles[:8])}]"
        elif stage.stage_name == "quality_gate":
            rubric = response_json.get("rubric") if isinstance(response_json.get("rubric"), dict) else {}
            if "score" in rubric:
                line += f" score={rubric.get('score')}"
        elif stage.stage_name == "stage3_upgrade":
            decision = response_json.get("decision") if isinstance(response_json.get("decision"), dict) else {}
            if decision:
                line += (
                    f" resolved_need_person={decision.get('resolved_need_person', '')}"
                    f" render_style={decision.get('render_style_mode', '')}"
                )
        if stage.error_detail:
            line += f" error={stage.error_detail}"
        lines.append(line)

    for asset in assets:
        lines.append(
            f"{asset.created_at.isoformat()} asset stage={asset.stage_name} attempt={asset.attempt} file={asset.file_name} model={asset.model_name}"
        )

    for score in scores:
        lines.append(
            f"{score.created_at.isoformat()} score stage={score.stage_name} attempt={score.attempt} value={score.score_0_100} pass={score.pass_fail}"
        )
    return "\n".join(lines)


def _compact_event_line(event) -> str:
    payload = _sanitize_payload(_json_dict(event.payload_json))
    line = (
        f"{event.created_at.isoformat()} stage={event.stage_name or '-'} attempt={event.attempt} "
        f"event={event.event_type} status={event.status}"
    )
    profile = payload.get("profile")
    if isinstance(profile, dict):
        line += f" profile={_profile_label({'profile': profile, 'branch_role': payload.get('branch_role'), 'prediction_status': payload.get('prediction_status'), 'prediction_id': payload.get('prediction_id')})}"
    if payload.get("prediction_id"):
        line += f" prediction_id={payload.get('prediction_id')}"
    if payload.get("provider_status"):
        line += f" provider_status={payload.get('provider_status')}"
    if event.message:
        line += f" message={event.message}"
    return line


def _detailed_event_lines(event) -> list[str]:
    payload = _sanitize_payload(_json_dict(event.payload_json))
    lines = [
        (
            f"{event.created_at.isoformat()} stage={event.stage_name or '-'} attempt={event.attempt} "
            f"event={event.event_type} status={event.status}"
        )
    ]
    if event.message:
        lines.append(f"message: {event.message}")
    if payload:
        lines.append("payload:")
        lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
    return lines


def _build_event_logs(run, events: list, stages: list, assets: list, scores: list) -> tuple[str, str]:
    header = (
        f"run_id={run.id} status={run.status} current_stage={run.current_stage} "
        f"attempt={run.optimization_attempt} tech_retries={run.technical_retry_count} "
        f"updated_at={run.updated_at.isoformat()}"
    )
    if not events:
        legacy = _build_legacy_execution_log(run, stages, assets, scores)
        return legacy, legacy

    compact_lines = [header]
    detailed_lines = [header]
    for event in events:
        compact_lines.append(_compact_event_line(event))
        detailed_lines.extend(_detailed_event_lines(event))
        detailed_lines.append("")
    return "\n".join(compact_lines), "\n".join(line for line in detailed_lines if line is not None)


@router.post("", response_model=list[RunOut])
def create_runs(payload: RunsCreateRequest, db: Session = Depends(db_dependency)) -> list[RunOut]:
    repo = Repository(db)
    config = repo.get_runtime_config()

    runs = repo.create_runs(
        payload.entry_ids,
        quality_threshold=payload.quality_threshold or config.quality_threshold,
        max_optimization_attempts=payload.max_optimization_attempts
        if payload.max_optimization_attempts is not None
        else config.max_optimization_loops,
    )

    payload_rows: list[RunOut] = []
    for run in runs:
        entry = repo.get_entry(run.entry_id)
        _, stages, _, assets, _ = repo.run_details(run.id)
        cost_summary = summarize_run_costs(stages, assets)
        if entry and entry.batch:
            cost_summary["batch_job"] = repo.batch_job_summary(entry.batch)
        payload_rows.append(_run_out(run, entry, cost_summary=cost_summary))
    return payload_rows


@router.get("", response_model=list[RunOut])
def list_runs(
    status: str | None = Query(default=None),
    entry_id: str | None = Query(default=None),
    min_score: float | None = Query(default=None),
    max_score: float | None = Query(default=None),
    db: Session = Depends(db_dependency),
) -> list[RunOut]:
    repo = Repository(db)
    runs = repo.list_runs(status=status, entry_id=entry_id, min_score=min_score, max_score=max_score)
    payload_rows: list[RunOut] = []
    for run in runs:
        entry = repo.get_entry(run.entry_id)
        _, stages, assets, _ = repo.run_snapshot(run.id)
        cost_summary = summarize_run_costs(stages, assets)
        if entry and entry.batch:
            cost_summary["batch_job"] = repo.batch_job_summary(entry.batch)
        payload_rows.append(_run_out(run, entry, cost_summary=cost_summary))
    return payload_rows


@router.get("/{run_id}", response_model=RunDetailOut)
def get_run(run_id: str, include_debug: bool = Query(default=False), db: Session = Depends(db_dependency)) -> RunDetailOut:
    repo = Repository(db)
    if include_debug:
        run, stages, prompts, assets, scores = repo.run_details(run_id)
        events = repo.list_run_events(run_id)
    else:
        run, stages, assets, scores = repo.run_snapshot(run_id)
        prompts = []
        events = []
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    entry = repo.get_entry(run.entry_id)
    cost_summary = summarize_run_costs(stages, assets)
    if entry and entry.batch:
        cost_summary["batch_job"] = repo.batch_job_summary(entry.batch)
    run_payload = _run_out(run, entry, cost_summary=cost_summary)
    execution_log, detailed_execution_log = ("", "")
    if include_debug:
        execution_log, detailed_execution_log = _build_event_logs(run, events, stages, assets, scores)

    return RunDetailOut(
        run=run_payload,
        stages=[
            StageResultOut(
                id=stage.id,
                stage_name=stage.stage_name,
                attempt=stage.attempt,
                status=stage.status,
                request_json=_sanitize_payload(_json_dict(stage.request_json)),
                response_json=_sanitize_payload(_json_dict(stage.response_json)),
                error_detail=_truncate_text(stage.error_detail),
                created_at=stage.created_at,
            )
            for stage in stages
        ],
        events=[
            RunEventOut(
                id=event.id,
                stage_name=event.stage_name,
                attempt=event.attempt,
                event_type=event.event_type,
                status=event.status,
                message=_truncate_text(event.message),
                payload_json=_sanitize_payload(_json_dict(event.payload_json)),
                created_at=event.created_at,
            )
            for event in events
        ],
        prompts=[
            PromptOut(
                id=prompt.id,
                stage_name=prompt.stage_name,
                attempt=prompt.attempt,
                prompt_text=_truncate_text(prompt.prompt_text),
                needs_person=prompt.needs_person,
                source=prompt.source,
                raw_response_json=_sanitize_payload(_json_dict(prompt.raw_response_json)),
                created_at=prompt.created_at,
            )
            for prompt in prompts
        ],
        assets=[
            AssetOut(
                id=asset.id,
                run_id=asset.run_id,
                stage_name=asset.stage_name,
                attempt=asset.attempt,
                file_name=asset.file_name,
                abs_path=asset.abs_path,
                mime_type=asset.mime_type,
                sha256=asset.sha256,
                width=asset.width,
                height=asset.height,
                origin_url=asset.origin_url,
                model_name=asset.model_name,
                created_at=asset.created_at,
            )
            for asset in assets
        ],
        scores=[
            ScoreOut(
                id=score.id,
                stage_name=score.stage_name,
                attempt=score.attempt,
                score_0_100=score.score_0_100,
                pass_fail=score.pass_fail,
                rubric_json=_sanitize_payload(_json_dict(score.rubric_json)),
                created_at=score.created_at,
            )
            for score in scores
        ],
        cost_summary=cost_summary,
        execution_log=_truncate_text(execution_log, max_len=20000),
        detailed_execution_log=_truncate_text(detailed_execution_log, max_len=40000),
    )


@router.post("/{run_id}/retry", response_model=RetryRunResponse)
def retry_run(run_id: str, db: Session = Depends(db_dependency)) -> RetryRunResponse:
    repo = Repository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    run = repo.retry_run_from_last_failure(run)
    return RetryRunResponse(run_id=run.id, status=run.status, retry_from_stage=run.retry_from_stage)


@router.post("/{run_id}/stop", response_model=StopRunResponse)
def stop_run(run_id: str, db: Session = Depends(db_dependency)) -> StopRunResponse:
    repo = Repository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    updated = repo.request_stop_run(run)
    message = "Run stop requested"
    if updated.status == "canceled":
        message = "Run canceled"
    repo.add_run_event(
        run_id=updated.id,
        stage_name=updated.current_stage,
        attempt=max(0, int(updated.optimization_attempt or 0)),
        event_type="run_stop_requested" if updated.status == "cancel_requested" else "run_canceled",
        status=updated.status,
        message=message,
        payload_json={"current_stage": updated.current_stage},
    )
    return StopRunResponse(
        run_id=updated.id,
        status=updated.status,
        current_stage=updated.current_stage,
        message=message,
    )


@router.get("/batches/{batch_id}/report", response_model=BatchJobReportOut)
def get_batch_report(batch_id: str, db: Session = Depends(db_dependency)) -> BatchJobReportOut:
    repo = Repository(db)
    report = repo.batch_job_report(batch_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return BatchJobReportOut(**report)


@router.delete("/{run_id}", response_model=DeleteRunsResponse)
def delete_run(run_id: str, db: Session = Depends(db_dependency)) -> DeleteRunsResponse:
    repo = Repository(db)
    deleted = repo.delete_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Run not found")
    return DeleteRunsResponse(deleted_run_count=1, deleted_run_ids=[run_id])


@router.delete("", response_model=DeleteRunsResponse)
def clear_runs(
    terminal_only: bool = Query(default=True),
    batch_id: str | None = Query(default=None),
    db: Session = Depends(db_dependency),
) -> DeleteRunsResponse:
    if not terminal_only:
        raise HTTPException(status_code=400, detail="Only terminal history clearing is supported")
    repo = Repository(db)
    deleted_ids = repo.clear_terminal_runs(batch_id=batch_id)
    return DeleteRunsResponse(deleted_run_count=len(deleted_ids), deleted_run_ids=deleted_ids)
