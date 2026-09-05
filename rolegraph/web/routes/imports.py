"""Import screen: upload a dataset, load the demo, review warnings, switch snapshots.

Uploads are validated before anything is stored: size limit, JSON only, schema
check. Nothing is fetched from the network.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from ...config import get_settings
from ...importer import SchemaError, import_json_bytes
from ...storage.repository import (
    activate_snapshot,
    audit_log,
    delete_snapshot,
    list_snapshots,
    save_import,
    snapshot_warnings,
)
from ..state import invalidate
from ._shared import render

router = APIRouter()

ALLOWED_SUFFIXES = (".json",)
ALLOWED_CONTENT_TYPES = ("application/json", "text/json", "application/octet-stream", "text/plain", "")


def _import_context(extra: dict | None = None) -> dict:
    settings = get_settings()
    snapshots = list_snapshots()
    context = {
        "nav": "import",
        "snapshots": snapshots,
        "active": next((s for s in snapshots if s.is_active), None),
        "demo_path": settings.demo_dataset,
        "demo_available": settings.demo_dataset.exists(),
        "max_upload_mb": settings.max_upload_bytes // (1024 * 1024),
        "audit": audit_log(15),
        "result": None,
        "error": None,
    }
    context.update(extra or {})
    return context


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request) -> HTMLResponse:
    return render(request, "import.html", _import_context())


@router.post("/import/demo", response_class=HTMLResponse)
async def load_demo(request: Request) -> HTMLResponse:
    settings = get_settings()
    if not settings.demo_dataset.exists():
        return render(
            request,
            "import.html",
            _import_context({"error": f"Demo dataset not found at {settings.demo_dataset}."}),
        )
    payload = settings.demo_dataset.read_bytes()
    try:
        result = import_json_bytes(payload, settings.demo_dataset.name, settings.max_upload_bytes)
    except SchemaError as exc:
        return render(request, "import.html", _import_context({"error": str(exc)}))
    snapshot_id = save_import(result)
    invalidate()
    return render(
        request,
        "import.html",
        _import_context(
            {
                "result": result,
                "result_snapshot_id": snapshot_id,
                "warnings": snapshot_warnings(snapshot_id),
            }
        ),
    )


@router.post("/import/upload", response_class=HTMLResponse)
async def upload(request: Request, file: UploadFile = File(...)) -> HTMLResponse:
    settings = get_settings()
    filename = file.filename or "upload.json"
    if not filename.lower().endswith(ALLOWED_SUFFIXES):
        return render(
            request,
            "import.html",
            _import_context({"error": "Only .json files can be imported."}),
        )
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        return render(
            request,
            "import.html",
            _import_context(
                {"error": f"Unsupported content type '{file.content_type}'. Upload a JSON file."}
            ),
        )
    payload = await file.read(settings.max_upload_bytes + 1)
    try:
        result = import_json_bytes(payload, filename, settings.max_upload_bytes)
    except SchemaError as exc:
        return render(request, "import.html", _import_context({"error": str(exc)}))
    snapshot_id = save_import(result)
    invalidate()
    return render(
        request,
        "import.html",
        _import_context(
            {
                "result": result,
                "result_snapshot_id": snapshot_id,
                "warnings": snapshot_warnings(snapshot_id),
            }
        ),
    )


@router.post("/import/activate/{snapshot_id}")
async def activate(snapshot_id: int) -> RedirectResponse:
    try:
        activate_snapshot(snapshot_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate()
    return RedirectResponse(url="/import", status_code=303)


@router.post("/import/delete/{snapshot_id}")
async def delete(snapshot_id: int) -> RedirectResponse:
    delete_snapshot(snapshot_id)
    invalidate(snapshot_id)
    return RedirectResponse(url="/import", status_code=303)


@router.get("/snapshots/{snapshot_id}/warnings", response_class=HTMLResponse)
async def warnings(request: Request, snapshot_id: int) -> HTMLResponse:
    return render(
        request,
        "snapshot_warnings.html",
        {
            "nav": "import",
            "snapshot_id": snapshot_id,
            "warnings": snapshot_warnings(snapshot_id),
            "summary": next((s for s in list_snapshots() if s.id == snapshot_id), None),
        },
    )
