"""Local-only FastAPI surface and SSE replay."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .orchestrator import CaseManager
from .provenance import LITE_ROOT, REPO_ROOT


def validate_bind_host(host: str) -> str:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Pi Swimlane Lite 只允许绑定 loopback")
    return host


class CaseInput(BaseModel):
    reference_lyrics: str = Field(min_length=1)
    golden_line: str = Field(min_length=1)
    style: str = "山歌民歌"
    requirements: str = ""
    forbidden_words: str = ""
    max_repairs: int = 3


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_bind_host(os.environ.get("PI_SWIMLANE_HOST", "127.0.0.1"))
    app.state.manager = CaseManager(os.environ.get("PI_SWIMLANE_DATA_DIR"))
    yield


app = FastAPI(title="Pi Song Swimlane Lite", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=LITE_ROOT / "static"), name="static")


def manager(request: Request) -> CaseManager:
    return request.app.state.manager


@app.get("/")
async def index():
    return FileResponse(LITE_ROOT / "static" / "index.html")


@app.get("/api/health")
async def health(request: Request):
    mgr = manager(request)
    return {"ok": True, "pi_version": mgr.provenance["pi_version"], "git_commit": mgr.provenance["git_commit"]}


@app.get("/api/cases")
async def recent_cases(request: Request):
    return manager(request).recent()


@app.post("/api/cases")
async def create_case(payload: CaseInput, request: Request):
    try:
        case = await manager(request).create_case(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return case.public_state()


@app.get("/api/cases/{case_id}")
async def get_case(case_id: str, request: Request):
    case = manager(request).cases.get(case_id)
    if not case:
        raise HTTPException(404, "Case 不存在")
    return case.public_state()


@app.get("/api/cases/{case_id}/journal")
async def get_journal(case_id: str, request: Request):
    case = manager(request).cases.get(case_id)
    if not case:
        raise HTTPException(404, "Case 不存在")
    return case.journal.events


@app.get("/api/cases/{case_id}/events")
async def events(
    case_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after: int = 0,
):
    case = manager(request).cases.get(case_id)
    if not case:
        raise HTTPException(404, "Case 不存在")
    cursor = int(last_event_id or after or 0)

    async def stream():
        queue = case.journal.subscribe()
        try:
            for event in case.journal.replay_after(cursor):
                yield _sse(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield _sse(event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                if await request.is_disconnected():
                    break
        finally:
            case.journal.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _sse(event: dict) -> str:
    return f"id: {event['event_id']}\nevent: journal\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/cases/{case_id}/stop")
async def stop(case_id: str, request: Request):
    try:
        stopped = await manager(request).stop_current(case_id)
    except KeyError as exc:
        raise HTTPException(404, "Case 不存在") from exc
    return {"stopped": stopped}


@app.post("/api/cases/{case_id}/cancel")
async def cancel(case_id: str, request: Request):
    try:
        await manager(request).cancel_case(case_id)
    except KeyError as exc:
        raise HTTPException(404, "Case 不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"cancelled": True}


@app.get("/api/ph-cases")
async def ph_cases():
    root = REPO_ROOT / "shan-song-skill-iteration" / "experiments" / "2026-07-16_v1.0.0_substyle_showcase" / "inputs"
    result = []
    for case_id in ("PH-009", "PH-046", "PH-094", "PH-168"):
        raw = json.loads((root / f"{case_id}.json").read_text(encoding="utf-8"))
        result.append(
            {
                "id": case_id,
                "reference_lyrics": raw["reference_lyrics"],
                "golden_line": raw.get("explicit_golden_line") or raw["target_title_or_golden_line"],
                "style": raw.get("lyric_style", "山歌民歌"),
            }
        )
    return result

