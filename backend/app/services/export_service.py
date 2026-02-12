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
from app.services.utils import sanitize_filename


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
            "word",
            "part of sentence",
            "category",
            "synonyms",
            "Base_Asset_Slug – your filename key",
            "context",
            "need a person",
            "prompt 1",
            "file name 1",
            "image 1",
            "prompt 2",
            "file name 2",
            "image 2",
            "upgraded prompt",
            "file name upgraded",
            "upgraded image 2",
            "file name without background",
            "image without background",
            "boy or girl",
        ]

        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for run, entry in runs_data:
                _, _stages, prompts, assets, _ = self.repo.run_details(run.id)

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

                by_stage_attempt: dict[str, dict[int, Asset]] = defaultdict(dict)
                for asset in assets:
                    by_stage_attempt[asset.stage_name][asset.attempt] = asset
                stage3_by_attempt = by_stage_attempt.get("stage3_upgraded", {})
                stage4_by_attempt = by_stage_attempt.get("stage4_white_bg", {})
                stage2_by_attempt = by_stage_attempt.get("stage2_draft", {})
                last_stage3_attempt = max(stage3_by_attempt.keys()) if stage3_by_attempt else None
                last_stage4_attempt = max(stage4_by_attempt.keys()) if stage4_by_attempt else None
                last_stage3 = stage3_by_attempt.get(last_stage3_attempt) if last_stage3_attempt is not None else None
                last_stage4 = stage4_by_attempt.get(last_stage4_attempt) if last_stage4_attempt is not None else None
                last_stage2_attempt = max(stage2_by_attempt.keys()) if stage2_by_attempt else None
                last_stage2 = stage2_by_attempt.get(last_stage2_attempt) if last_stage2_attempt is not None else None
                first_stage3 = stage3_by_attempt.get(1)
                base_slug = self._base_asset_slug(entry.word, entry.part_of_sentence, entry.category)

                prompt2 = upgraded_prompts[0]["prompt_text"] if upgraded_prompts else ""
                upgraded_prompt = upgraded_prompts[-1]["prompt_text"] if upgraded_prompts else ""
                need_person = ""
                for prompt in prompts:
                    if prompt.stage_name == "stage1_prompt":
                        need_person = prompt.needs_person
                        break

                file_name_1 = self._unique_export_name(base_slug, run.id, last_stage2) if last_stage2 else ""
                file_name_2 = self._unique_export_name(base_slug, run.id, first_stage3) if first_stage3 else ""
                file_name_upgraded = self._unique_export_name(base_slug, run.id, last_stage3) if last_stage3 else ""
                file_name_without_background = self._unique_export_name(base_slug, run.id, last_stage4) if last_stage4 else ""

                writer.writerow(
                    {
                        "word": entry.word,
                        "part of sentence": entry.part_of_sentence,
                        "category": entry.category,
                        "synonyms": "",
                        "Base_Asset_Slug – your filename key": self._base_asset_slug(entry.word, entry.part_of_sentence, entry.category),
                        "context": entry.context,
                        "need a person": need_person,
                        "prompt 1": first_prompt,
                        "file name 1": file_name_1,
                        "image 1": last_stage2.abs_path if last_stage2 else "",
                        "prompt 2": prompt2,
                        "file name 2": file_name_2,
                        "image 2": first_stage3.abs_path if first_stage3 else "",
                        "upgraded prompt": upgraded_prompt,
                        "file name upgraded": file_name_upgraded,
                        "upgraded image 2": last_stage3.abs_path if last_stage3 else "",
                        "file name without background": file_name_without_background,
                        "image without background": last_stage4.abs_path if last_stage4 else "",
                        "boy or girl": entry.boy_or_girl,
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
                    base_slug = self._base_asset_slug(_entry.word, _entry.part_of_sentence, _entry.category)
                    archive.write(asset_path, arcname=self._unique_export_name(base_slug, run.id, selected))

    @staticmethod
    def _base_asset_slug(word: str, part_of_sentence: str, category: str) -> str:
        parts = [
            (word or "").strip().lower() or "unknown-word",
            (part_of_sentence or "").strip().lower() or "unknown-pos",
            (category or "").strip().lower() or "no-category",
        ]
        merged = "_".join(parts)
        return sanitize_filename(merged.lower())

    @staticmethod
    def _latest_asset_for_stage(assets: list[Asset], stage_name: str) -> Asset | None:
        candidates = [asset for asset in assets if asset.stage_name == stage_name]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.attempt)

    @staticmethod
    def _unique_export_name(base_slug: str, run_id: str, asset: Asset | None) -> str:
        if asset is None:
            return ""
        safe_name = sanitize_filename(asset.file_name)
        return f"{base_slug}__{run_id}__{asset.id}__{safe_name}"

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
