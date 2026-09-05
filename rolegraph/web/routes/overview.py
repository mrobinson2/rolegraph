from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..viewmodels import overview
from ._shared import dataset, render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    data = dataset()
    context = overview(data.graph, data.findings, data.snapshot)
    context["nav"] = "overview"
    return render(request, "overview.html", context)
