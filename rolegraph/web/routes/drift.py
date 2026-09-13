"""Offline upload-and-compare UI. Target state stays in the operator's repository."""

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...config import get_settings
from ...drift.engine import LIMITATIONS, compare
from ...drift.files import merge_targets, parse_json
from ...drift.schema import Observation, TargetState
from ..viewmodels import drift_view
from ._shared import render

router = APIRouter()


@router.get("/drift", response_class=HTMLResponse)
async def drift_page(request: Request):
    return render(request, "drift.html", drift_view())


@router.post("/drift/compare", response_class=HTMLResponse)
async def compare_files(request: Request, targets: list[UploadFile] = File(...), observed: UploadFile = File(...)):
    remaining = get_settings().max_upload_bytes
    try:
        if len(targets) > 100:
            raise ValueError("Upload at most 100 target files at a time.")
        documents = []
        for upload in [*targets, observed]:
            if not (upload.filename or "").lower().endswith(".json"):
                raise ValueError("Only .json files can be compared.")
            payload = await upload.read(remaining + 1)
            remaining -= len(payload)
            if remaining < 0:
                raise ValueError("The combined uploads exceed the configured upload limit.")
            documents.append(parse_json(payload))
        target, metadata = merge_targets([
            (upload.filename, TargetState.model_validate(doc)) for upload, doc in zip(targets, documents[:-1])
        ])
        report = compare(target, Observation.model_validate(documents[-1]))
        report["target"] = metadata
    except ValueError as exc:
        report = {"status": "error", "error": str(exc), "limitations": LIMITATIONS}
    finally:
        for upload in [*targets, observed]:
            await upload.close()
    return render(request, "drift.html", drift_view(report))
