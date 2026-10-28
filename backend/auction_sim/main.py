"""FastAPI transport for the trusted, local researcher console."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .models import EventsResponse, RunConfig, RunsResponse, RunView
from .receiver import Receiver
from .storage import Store


def create_app(db_path: str | Path | None = None) -> FastAPI:
    store = Store(db_path or os.environ.get("AUCTION_DB_PATH", "data/auction.sqlite3"))
    receiver = Receiver(store)

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(store.initialize)
        yield

    app = FastAPI(title="HAN2601 Auction Simulator", version="0.1.0", lifespan=lifespan)
    app.state.receiver = receiver

    @app.exception_handler(KeyError)
    async def missing_run(request: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={"detail": "Auction run not found"})

    @app.exception_handler(ValueError)
    async def invalid_operation(request: Request, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": "; ".join(errors)})

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/runs", response_model=RunsResponse)
    def runs():
        return {"runs": receiver.list_runs()}

    @app.post("/api/runs", status_code=201, response_model=RunView)
    def create_run(config: RunConfig):
        return receiver.create(config)

    @app.get("/api/runs/{run_id}", response_model=RunView)
    def get_run(run_id: str):
        return receiver.get(run_id)

    @app.post("/api/runs/{run_id}/advance", response_model=RunView)
    def advance(run_id: str):
        return receiver.advance(run_id)

    @app.post("/api/runs/{run_id}/execute", response_model=RunView)
    def execute(run_id: str):
        return receiver.execute(run_id)

    @app.get("/api/runs/{run_id}/events", response_model=EventsResponse)
    def events(run_id: str, after: int = Query(default=0, ge=0, le=2**63 - 1)):
        return {"events": receiver.events(run_id, after)}

    @app.get("/api/runs/{run_id}/replay", response_model=RunView)
    def replay(run_id: str, through: int | None = Query(default=None, ge=1)):
        return receiver.replay(run_id, through)

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str):
        return JSONResponse(
            receiver.export(run_id),
            headers={
                "Content-Disposition": f'attachment; filename="auction-{run_id}.json"',
            },
        )

    @app.websocket("/api/runs/{run_id}/ws")
    async def stream(
        websocket: WebSocket,
        run_id: str,
        after: int = Query(default=0, ge=0, le=2**63 - 1),
    ):
        try:
            await run_in_threadpool(receiver.get, run_id)
        except KeyError:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        cursor, idle_ticks = after, 0
        try:
            while True:
                batch = await run_in_threadpool(receiver.events, run_id, cursor)
                if batch:
                    await websocket.send_json({"type": "events", "events": batch})
                    cursor, idle_ticks = batch[-1]["seq"], 0
                else:
                    idle_ticks += 1
                    if idle_ticks % 20 == 0:
                        await websocket.send_json({"type": "heartbeat"})
                # Read as well as send to detect an orderly client disconnect promptly.
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=0.25)
                    if message["type"] == "websocket.disconnect":
                        break
                except asyncio.TimeoutError:
                    pass
        except WebSocketDisconnect:
            pass

    frontend_dir = Path(os.environ.get("AUCTION_FRONTEND_DIR", "frontend/dist"))
    if (frontend_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def home():
        index = frontend_dir / "index.html"
        if index.is_file():
            return FileResponse(index)
        return {
            "message": "Backend ready. Start frontend with npm run dev in frontend/.",
            "api_docs": "/docs",
        }

    return app


app = create_app()
