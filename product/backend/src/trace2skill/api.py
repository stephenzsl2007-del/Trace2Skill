from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .models import RunKind, TERMINAL_STATUSES
from .dispatcher import LocalMvpDispatcher
from .objects import ObjectStore
from .repository import Repository
from .service import ProductSettings, RunService


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: RunKind
    fixture_ids: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["prepare", "confirm"]
    approval_id: str | None = None
    challenge: str | None = None
    manifest_hash: str | None = None


def create_app(settings: ProductSettings | None = None, dispatcher: Any = None) -> FastAPI:
    configured = settings or ProductSettings(
        Path(
            os.environ.get(
                "TRACE2SKILL_DATA_DIR",
                str(Path(__file__).resolve().parents[3] / "data"),
            )
        ).resolve()
    )
    repository = Repository(configured.database_path)
    objects = ObjectStore(configured.object_path)
    if dispatcher is None and settings is None:
        dispatcher = LocalMvpDispatcher(repository, objects)
    service = RunService(repository, objects, dispatcher)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        repository.recover_incomplete_runs()
        yield

    app = FastAPI(title="Trace2Skill", version="0.3.0", lifespan=lifespan)
    app.state.repository = repository
    app.state.objects = objects
    app.state.service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.3.0"}

    @app.post("/runs", status_code=status.HTTP_202_ACCEPTED)
    async def create_run(
        request: RunCreate, idempotency_key: str | None = Header(None, alias="Idempotency-Key")
    ) -> dict[str, Any]:
        run, created = service.create_run(request.kind.value, request.model_dump(), idempotency_key)
        service.start(run, created)
        return {**run, "created": created}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        return run

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        try:
            return await service.cancel(run_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found") from exc

    @app.get("/runs/{run_id}/events")
    async def stream_events(
        run_id: str, request: Request, last_event_id: str | None = Header(None, alias="Last-Event-ID")
    ) -> StreamingResponse:
        if not repository.get_run(run_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")

        async def generate() -> AsyncIterator[str]:
            cursor = last_event_id
            while not await request.is_disconnected():
                try:
                    events = repository.list_events(run_id, cursor)
                except KeyError as exc:
                    yield f"event: error\ndata: {json.dumps({'detail': 'unknown Last-Event-ID'})}\n\n"
                    return
                for event in events:
                    cursor = event["event_id"]
                    yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                run = repository.get_run(run_id)
                if run and run["status"] in {item.value for item in TERMINAL_STATUSES} and not events:
                    return
                if not events:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/traces/{trace_id}")
    def get_trace(trace_id: str) -> dict[str, Any]:
        trace = repository.get_trace(trace_id)
        if not trace:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "trace not found")
        return {**trace, "content": objects.get_json(trace["object_ref"])}

    @app.get("/skills")
    def list_skills() -> list[dict[str, Any]]:
        return repository.list_skills()

    @app.get("/skills/{name}/{version}")
    def get_skill(name: str, version: str) -> dict[str, Any]:
        skill = repository.get_skill(name, version)
        if not skill:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found")
        return {**skill, "evaluations": repository.skill_evaluations(name, version)}

    @app.post("/skills/{name}/{version}/validate", status_code=status.HTTP_202_ACCEPTED)
    async def validate_skill(name: str, version: str) -> dict[str, Any]:
        if not repository.get_skill(name, version):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found")
        run, created = service.create_run(
            RunKind.VALIDATION.value, {"name": name, "version": version, "config": {}}, None
        )
        service.start(run, created)
        return {**run, "created": created}

    @app.post("/skills/{name}/{version}/publish")
    def publish_skill(name: str, version: str, request: PublishRequest) -> dict[str, Any]:
        try:
            if request.action == "prepare":
                return service.prepare_publish(name, version)
            if not request.approval_id or not request.challenge or not request.manifest_hash:
                raise ValueError("confirm requires approval_id, challenge, and manifest_hash")
            return service.confirm_publish(
                name,
                version,
                request.approval_id,
                request.challenge,
                request.manifest_hash,
            )
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return app


app = create_app()
