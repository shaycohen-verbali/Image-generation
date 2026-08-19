from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.services.library import LibraryNotConfigured, get_lemma, list_lemmas, list_sense_images, materialize_image

router = APIRouter(prefix="/api/v1/library", tags=["library"])


def _library_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LibraryNotConfigured):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail="Library data could not be loaded")


@router.get("/lemmas")
def search_lemmas(
    q: str = Query(default="", max_length=120),
    pos: str = Query(default="", max_length=40),
    cursor: str = Query(default="", max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    try:
        return list_lemmas(query=q, pos=pos, cursor=cursor, limit=limit)
    except Exception as exc:  # noqa: BLE001 - expose a stable API error
        raise _library_error(exc) from exc


@router.get("/lemmas/{lemma}")
def read_lemma(lemma: str) -> dict:
    try:
        result = get_lemma(lemma)
    except Exception as exc:  # noqa: BLE001 - expose a stable API error
        raise _library_error(exc) from exc
    if not result.get("pos_groups"):
        raise HTTPException(status_code=404, detail="Lemma not found")
    return result


@router.get("/senses/{sense_id}/images")
def read_sense_images(sense_id: str) -> dict:
    try:
        return list_sense_images(sense_id)
    except Exception as exc:  # noqa: BLE001 - expose a stable API error
        raise _library_error(exc) from exc


@router.get("/images/{token}")
def read_library_image(token: str, download: bool = Query(default=False)) -> FileResponse:
    try:
        path = materialize_image(token)
    except Exception as exc:  # noqa: BLE001 - do not reveal storage paths
        raise HTTPException(status_code=404, detail="Image not found or link expired") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        filename=path.name,
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "private, max-age=300"},
    )
