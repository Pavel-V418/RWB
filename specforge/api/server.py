"""HTTP API для локальной консоли (контракт — docs/console-api.yaml).

    POST /runs                       — загрузить ТЗ и запустить конвейер в фоне
    GET  /runs, /runs/{id}           — список и статус запусков
    GET  /runs/{id}/events           — события (SSE; ?after=<id>, ?follow=false — без ожидания)
    GET  /runs/{id}/graph            — узлы, рёбра, статусы, usage для отрисовки графа
    GET  /runs/{id}/artifacts?path=  — список файлов каталога или содержимое файла
    GET  /runs/{id}/report           — отчёт в Markdown
    POST /runs/{id}/approve          — подтвердить остановку (с правками артефактов)

Запуск: `uv run uvicorn specforge.api.server:app`; выгрузка контракта:
`uv run python -m specforge.api.server openapi docs/console-api.yaml`.
"""

import asyncio
import os
import shutil
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractContextManager, ExitStack, asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field, JsonValue
from sse_starlette.sse import EventSourceResponse

from specforge.core.contracts import CaseProfile
from specforge.core.events import EventType, StoredEvent
from specforge.orchestrator.graph import OrchestratorError
from specforge.orchestrator.service import (
    GraphView,
    RunInfo,
    RunOptions,
    RunService,
    new_run_id,
    open_service,
)
from specforge.telemetry.report import render_report
from specforge.tools.docs import SUPPORTED_SUFFIXES

UPLOADS_DIR = ".uploads"
DEFAULT_ORIGINS = "http://localhost:4200,http://127.0.0.1:4200"
SSE_POLL_S = 0.2


class EventOut(BaseModel):
    id: int
    run_id: str | None
    kind: str
    stage: str | None
    agent: str | None
    message: str
    payload: dict[str, JsonValue]
    ts: datetime

    @classmethod
    def of(cls, stored: StoredEvent) -> "EventOut":
        e = stored.event
        return cls(
            id=stored.id,
            run_id=e.run_id,
            kind=e.kind,
            stage=e.stage,
            agent=e.agent,
            message=e.message,
            payload=e.data,
            ts=e.ts,
        )


class RunCreated(BaseModel):
    run_id: str
    status: str


class ApproveRequest(BaseModel):
    edits: dict[str, str] = Field(default_factory=dict)
    """Правки артефактов: {путь относительно artifacts/ (<этап>/<файл>): новое содержимое}."""


class ArtifactEntry(BaseModel):
    path: str
    """Относительно artifacts/."""
    is_dir: bool
    size: int | None = None


class ArtifactListing(BaseModel):
    path: str
    entries: list[ArtifactEntry]


def create_app(service: RunService | None = None) -> FastAPI:
    """Приложение поверх готового RunService (тесты) или созданного по окружению."""
    stack = ExitStack()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is None:
            app.state.service = stack.enter_context(_env_service())
        try:
            yield
        finally:
            stack.close()

    app = FastAPI(
        title="SpecForge Console API",
        version="1.0.0",
        description="API локальной консоли SpecForge: запуски конвейера, события, граф, "
        "артефакты, отчёт и подтверждения.",
        lifespan=lifespan,
    )
    app.state.service = service
    origins = os.environ.get("SPECFORGE_CONSOLE_ORIGINS", DEFAULT_ORIGINS).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


router = APIRouter()


def svc(request: Request) -> RunService:
    current: RunService | None = request.app.state.service
    if current is None:
        raise HTTPException(503, "сервис запусков не инициализирован")
    return current


def known(request: Request, run_id: str) -> tuple[RunService, RunInfo]:
    service = svc(request)
    info = service.info(run_id)
    if info.status == "unknown":
        raise HTTPException(404, f"запуск {run_id!r} не найден")
    return service, info


@router.post("/runs", status_code=202, response_model=RunCreated, tags=["runs"])
def create_run(
    request: Request,
    file: Annotated[UploadFile, File(description="ТЗ: .md, .txt, .pdf, .docx")],
    only: Annotated[str | None, Form(description="Этапы через запятую")] = None,
    approve_after: Annotated[str | None, Form(description="Остановиться после этапа")] = None,
    flags: Annotated[
        str | None, Form(description="Флаги CaseProfile через запятую: has_web,has_mobile")
    ] = None,
) -> RunCreated:
    service = svc(request)
    name = Path(file.filename or "requirements.md").name
    if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise HTTPException(415, f"ожидается {', '.join(SUPPORTED_SUFFIXES)}")
    run_id = new_run_id()
    target = service.config.runs_dir / UPLOADS_DIR / run_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    options = RunOptions(
        profile=_profile(_split(flags)),
        only=_split(only),
        approve_after=approve_after or None,
    )
    try:
        service.start(target, options, run_id=run_id, background=True)
    except OrchestratorError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RunCreated(run_id=run_id, status="running")


@router.get("/runs", response_model=list[RunInfo], tags=["runs"])
def list_runs(request: Request) -> list[RunInfo]:
    return svc(request).list_runs()


@router.get("/runs/{run_id}", response_model=RunInfo, tags=["runs"])
def get_run(request: Request, run_id: str) -> RunInfo:
    return known(request, run_id)[1]


@router.get(
    "/runs/{run_id}/events",
    tags=["runs"],
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "text/event-stream: id — id события, event — kind, "
            "data — EventOut в JSON",
            "content": {"text/event-stream": {}},
        }
    },
)
async def run_events(
    request: Request,
    run_id: str,
    after: Annotated[int, Query(ge=0, description="Только события с id больше")] = 0,
    follow: Annotated[bool, Query(description="Ждать новые до run_finished")] = True,
) -> EventSourceResponse:
    service, _ = known(request, run_id)
    last = request.headers.get("last-event-id")
    start = int(last) if last and last.isdigit() else after

    async def stream() -> AsyncIterator[dict[str, str]]:
        cursor = start
        while True:
            batch = await asyncio.to_thread(service.events, run_id, cursor)
            for stored in batch:
                cursor = stored.id
                yield {
                    "id": str(stored.id),
                    "event": stored.event.kind,
                    "data": EventOut.of(stored).model_dump_json(),
                }
                if stored.event.kind == EventType.run_finished:
                    return
            if not follow or await request.is_disconnected():
                return
            if not batch and service.info(run_id).status in ("failed", "interrupted"):
                return
            await asyncio.sleep(SSE_POLL_S)

    return EventSourceResponse(stream(), ping=15)


@router.get("/runs/{run_id}/graph", response_model=GraphView, tags=["runs"])
def run_graph(request: Request, run_id: str) -> GraphView:
    service, _ = known(request, run_id)
    return service.graph_view(run_id)


@router.get(
    "/runs/{run_id}/artifacts",
    tags=["artifacts"],
    response_model=ArtifactListing,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def run_artifacts(
    request: Request,
    run_id: str,
    path: Annotated[str, Query(description="Путь относительно artifacts/")] = "",
) -> Any:
    service, _ = known(request, run_id)
    root = (service.config.runs_dir / run_id / "artifacts").resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(400, "путь вне artifacts/")
    if target.is_file():
        return FileResponse(target)
    if not target.is_dir():
        raise HTTPException(404, f"нет {path!r}")
    entries = [
        ArtifactEntry(
            path=p.relative_to(root).as_posix(),
            is_dir=p.is_dir(),
            size=p.stat().st_size if p.is_file() else None,
        )
        for p in sorted(target.iterdir())
        if not p.name.startswith(".")
    ]
    return ArtifactListing(path=target.relative_to(root).as_posix().strip("."), entries=entries)


@router.get("/runs/{run_id}/report", response_class=PlainTextResponse, tags=["runs"])
def run_report(request: Request, run_id: str) -> str:
    service, _ = known(request, run_id)
    summary = service.summary(run_id)
    if summary is None:
        raise HTTPException(404, "отчёта ещё нет")
    return render_report(summary, [e.event for e in service.events(run_id)])


@router.post("/runs/{run_id}/approve", status_code=202, response_model=RunCreated, tags=["runs"])
def approve(request: Request, run_id: str, body: ApproveRequest | None = None) -> RunCreated:
    service, info = known(request, run_id)
    if info.status != "waiting_approval":
        raise HTTPException(409, f"запуск в статусе {info.status}, подтверждать нечего")
    try:
        service.approve(run_id, (body or ApproveRequest()).edits, background=True)
    except OrchestratorError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RunCreated(run_id=run_id, status="running")


def _split(value: str | None) -> list[str] | None:
    items = [v.strip() for v in (value or "").split(",") if v.strip()]
    return items or None


def _profile(flags: list[str] | None) -> CaseProfile:
    known_flags = set(CaseProfile.model_fields) - {"extra"}
    chosen = flags or []
    return CaseProfile(
        **{f: True for f in chosen if f in known_flags},
        extra={f: True for f in chosen if f not in known_flags},
    )


def _env_service() -> AbstractContextManager[RunService]:
    from dotenv import find_dotenv, load_dotenv  # noqa: PLC0415

    load_dotenv(find_dotenv(usecwd=True))
    return open_service()


def openapi_yaml(app: FastAPI | None = None) -> str:
    schema = (app or create_app(service=None)).openapi()
    return yaml.safe_dump(schema, allow_unicode=True, sort_keys=False)


def export_openapi(path: Path) -> None:
    path.write_text(openapi_yaml(), encoding="utf-8")


def _main(argv: Iterator[str]) -> None:
    args = list(argv)
    if len(args) == 2 and args[0] == "openapi":
        export_openapi(Path(args[1]))
        return
    raise SystemExit("использование: python -m specforge.api.server openapi <путь.yaml>")


app = create_app()

if __name__ == "__main__":
    _main(iter(sys.argv[1:]))
