from __future__ import annotations

import csv
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import Asset
from app.services.repository import Repository
from app.services.storage import exports_root


class ExportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = Repository(db)

    def create_export(self, filters: dict[str, Any]):
        record = self.repo.create_export(filters)
        export_dir = exports_root() / record.id
        export_dir.mkdir(parents=True, exist_ok=True)

        try:
            runs = self.repo.list_runs_for_export(filters)
            manifest = self._build_manifest(runs)
            csv_path = export_dir / "export.csv"
            white_bg_zip_path = export_dir / "images_white_bg.zip"
            with_bg_zip_path = export_dir / "images_with_bg_last_attempt.zip"
            manifest_path = export_dir / "manifest.json"

            self._write_csv(csv_path, runs)
            self._write_zip(white_bg_zip_path, runs, stage_name="stage4_white_bg")
            self._write_zip(with_bg_zip_path, runs, stage_name="stage3_upgraded")
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

            self.repo.update_export(
                record,
                status="completed",
                csv_path=csv_path.as_posix(),
                zip_path=white_bg_zip_path.as_posix(),
                manifest_path=manifest_path.as_posix(),
            )
        except Exception as exc:  # noqa: BLE001
            self.repo.update_export(record, status="failed", error_detail=str(exc))

        return self.repo.get_export(record.id)

    def _write_csv(self, path: Path, runs_data: list[tuple]) -> None:
        headers = [
            "run_id",
            "entry_id",
            "word",
            "part_of_sentence",
            "category",
            "context",
            "boy_or_girl",
            "batch",
            "status",
            "quality_score",
            "quality_threshold",
            "optimization_attempt",
            "max_optimization_attempts",
            "first_prompt",
            "upgraded_prompt_count",
            "upgraded_prompts_json",
            "with_background_last_image_name",
            "with_background_last_image_path",
            "without_background_last_image_name",
            "without_background_last_image_path",
            "with_background_images_by_attempt_json",
            "without_background_images_by_attempt_json",
            "all_image_names_json",
            "stage_statuses_json",
            "assets_json",
            "error_detail",
        ]

        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for run, entry in runs_data:
                _, stages, prompts, assets, _ = self.repo.run_details(run.id)

                first_prompt = ""
                upgraded_prompts: list[dict[str, Any]] = []
                for prompt in prompts:
                    payload = {
                        "stage": prompt.stage_name,
                        "attempt": prompt.attempt,
                        "prompt_text": prompt.prompt_text,
                        "needs_person": prompt.needs_person,
                        "source": prompt.source,
                    }
                    if prompt.stage_name == "stage1_prompt" and not first_prompt:
                        first_prompt = prompt.prompt_text
                    if prompt.stage_name == "stage3_upgrade":
                        upgraded_prompts.append(payload)

                stage_statuses = [
                    {
                        "stage_name": stage.stage_name,
                        "attempt": stage.attempt,
                        "status": stage.status,
                        "error_detail": stage.error_detail,
                    }
                    for stage in stages
                ]
                by_stage_attempt: dict[str, dict[int, Asset]] = defaultdict(dict)
                for asset in assets:
                    by_stage_attempt[asset.stage_name][asset.attempt] = asset
                stage3_by_attempt = by_stage_attempt.get("stage3_upgraded", {})
                stage4_by_attempt = by_stage_attempt.get("stage4_white_bg", {})
                last_stage3_attempt = max(stage3_by_attempt.keys()) if stage3_by_attempt else None
                last_stage4_attempt = max(stage4_by_attempt.keys()) if stage4_by_attempt else None
                last_stage3 = stage3_by_attempt.get(last_stage3_attempt) if last_stage3_attempt is not None else None
                last_stage4 = stage4_by_attempt.get(last_stage4_attempt) if last_stage4_attempt is not None else None

                stage3_images_by_attempt = [
                    {
                        "attempt": attempt,
                        "file_name": asset.file_name,
                        "abs_path": asset.abs_path,
                    }
                    for attempt, asset in sorted(stage3_by_attempt.items(), key=lambda item: item[0])
                ]
                stage4_images_by_attempt = [
                    {
                        "attempt": attempt,
                        "file_name": asset.file_name,
                        "abs_path": asset.abs_path,
                    }
                    for attempt, asset in sorted(stage4_by_attempt.items(), key=lambda item: item[0])
                ]
                assets_export = [
                    {
                        "asset_id": asset.id,
                        "stage_name": asset.stage_name,
                        "attempt": asset.attempt,
                        "abs_path": asset.abs_path,
                        "model_name": asset.model_name,
                    }
                    for asset in assets
                ]

                writer.writerow(
                    {
                        "run_id": run.id,
                        "entry_id": entry.id,
                        "word": entry.word,
                        "part_of_sentence": entry.part_of_sentence,
                        "category": entry.category,
                        "context": entry.context,
                        "boy_or_girl": entry.boy_or_girl,
                        "batch": entry.batch,
                        "status": run.status,
                        "quality_score": run.quality_score,
                        "quality_threshold": run.quality_threshold,
                        "optimization_attempt": run.optimization_attempt,
                        "max_optimization_attempts": run.max_optimization_attempts,
                        "first_prompt": first_prompt,
                        "upgraded_prompt_count": len(upgraded_prompts),
                        "upgraded_prompts_json": json.dumps(upgraded_prompts, ensure_ascii=False),
                        "with_background_last_image_name": last_stage3.file_name if last_stage3 else "",
                        "with_background_last_image_path": last_stage3.abs_path if last_stage3 else "",
                        "without_background_last_image_name": last_stage4.file_name if last_stage4 else "",
                        "without_background_last_image_path": last_stage4.abs_path if last_stage4 else "",
                        "with_background_images_by_attempt_json": json.dumps(stage3_images_by_attempt, ensure_ascii=False),
                        "without_background_images_by_attempt_json": json.dumps(stage4_images_by_attempt, ensure_ascii=False),
                        "all_image_names_json": json.dumps([asset.file_name for asset in assets], ensure_ascii=False),
                        "stage_statuses_json": json.dumps(stage_statuses, ensure_ascii=False),
                        "assets_json": json.dumps(assets_export, ensure_ascii=False),
                        "error_detail": run.error_detail,
                    }
                )

    def _write_zip(self, path: Path, runs_data: list[tuple], *, stage_name: str) -> None:
        with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for run, _entry in runs_data:
                _, _, _, assets, _ = self.repo.run_details(run.id)
                selected = self._latest_asset_for_stage(assets, stage_name)
                if selected is None:
                    continue
                asset_path = Path(selected.abs_path)
                if asset_path.exists():
                    archive.write(asset_path, arcname=f"{run.id}/{asset_path.name}")

    @staticmethod
    def _latest_asset_for_stage(assets: list[Asset], stage_name: str) -> Asset | None:
        candidates = [asset for asset in assets if asset.stage_name == stage_name]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.attempt)

    def _build_manifest(self, runs_data: list[tuple]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for run, entry in runs_data:
            _, stages, prompts, assets, scores = self.repo.run_details(run.id)
            rows.append(
                {
                    "run": {
                        "id": run.id,
                        "status": run.status,
                        "quality_score": run.quality_score,
                        "quality_threshold": run.quality_threshold,
                        "optimization_attempt": run.optimization_attempt,
                        "max_optimization_attempts": run.max_optimization_attempts,
                        "error_detail": run.error_detail,
                    },
                    "entry": {
                        "id": entry.id,
                        "word": entry.word,
                        "part_of_sentence": entry.part_of_sentence,
                        "category": entry.category,
                        "context": entry.context,
                        "boy_or_girl": entry.boy_or_girl,
                        "batch": entry.batch,
                    },
                    "stages": [
                        {
                            "stage_name": stage.stage_name,
                            "attempt": stage.attempt,
                            "status": stage.status,
                            "request_json": self.repo.json_field_dict(stage.request_json),
                            "response_json": self.repo.json_field_dict(stage.response_json),
                            "error_detail": stage.error_detail,
                        }
                        for stage in stages
                    ],
                    "prompts": [
                        {
                            "stage_name": prompt.stage_name,
                            "attempt": prompt.attempt,
                            "prompt_text": prompt.prompt_text,
                            "needs_person": prompt.needs_person,
                            "source": prompt.source,
                            "raw_response_json": self.repo.json_field_dict(prompt.raw_response_json),
                        }
                        for prompt in prompts
                    ],
                    "assets": [
                        {
                            "asset_id": asset.id,
                            "stage_name": asset.stage_name,
                            "attempt": asset.attempt,
                            "file_name": asset.file_name,
                            "abs_path": asset.abs_path,
                            "mime_type": asset.mime_type,
                            "sha256": asset.sha256,
                            "width": asset.width,
                            "height": asset.height,
                            "origin_url": asset.origin_url,
                            "model_name": asset.model_name,
                        }
                        for asset in assets
                    ],
                    "scores": [
                        {
                            "stage_name": score.stage_name,
                            "attempt": score.attempt,
                            "score_0_100": score.score_0_100,
                            "pass_fail": score.pass_fail,
                            "rubric_json": self.repo.json_field_dict(score.rubric_json),
                        }
                        for score in scores
                    ],
                }
            )

        return {
            "schema_version": "v1",
            "artifacts": {
                "csv": "export.csv",
                "white_bg_zip": "images_white_bg.zip",
                "with_bg_zip": "images_with_bg_last_attempt.zip",
                "manifest": "manifest.json",
            },
            "records": rows,
        }
