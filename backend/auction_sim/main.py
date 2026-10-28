"""Authenticated transport; output is projected for the current account."""

import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .auth import AuthError, AuthService, InvalidCredentials, RateLimited, UsernameTaken
from .models import (
    AuthResponse,
    Credentials,
    EventsResponse,
    RunConfig,
    RunsResponse,
    RunView,
    UsersResponse,
)
from .receiver import Receiver
from .storage import Store

COOKIE_NAME = "auction_session"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    database = (
        db_path
        or os.environ.get("AUCTION_DATABASE_URL")
        or os.environ.get("AUCTION_DB_PATH", "data/auction.sqlite3")
    )
    store = Store(database)
    receiver = Receiver(store)
    auth = AuthService(store)
    secure_cookie = os.environ.get("AUCTION_COOKIE_SECURE", "false").lower() in {"1", "true"}
    allowed_origins = {
        origin.strip().rstrip("/")
        for origin in os.environ.get(
            "AUCTION_ALLOWED_ORIGINS",
            "http://127.0.0.1:8000,http://localhost:8000,"
            "http://127.0.0.1:5173,http://localhost:5173",
        ).split(",")
        if origin.strip()
    }

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(store.initialize)
        yield
        await run_in_threadpool(store.close)

    app = FastAPI(title="HAN2601 Auction Simulator", version="0.2.0", lifespan=lifespan)
    app.state.receiver = receiver
    app.state.auth = auth

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(KeyError)
    async def missing_run(request: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={"detail": "Auction run not found"})

    @app.exception_handler(PermissionError)
    async def forbidden(request: Request, exc: PermissionError):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_operation(request: Request, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError):
        status, headers = 422, {}
        if isinstance(exc, InvalidCredentials):
            status = 401
        elif isinstance(exc, UsernameTaken):
            status = 409
        elif isinstance(exc, RateLimited):
            status = 429
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(status_code=status, content={"detail": str(exc)}, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": "; ".join(errors)})

    def check_origin(origin: str | None):
        if origin is not None and origin not in allowed_origins:
            raise HTTPException(403, "Request origin is not allowed")

    def session(request: Request) -> dict:
        identity = auth.authenticate(request.cookies.get(COOKIE_NAME, ""))
        if identity is None:
            raise HTTPException(401, "Please sign in")
        return identity

    def user(identity: dict = Depends(session)) -> dict:
        return identity["user"]

    def mutation(request: Request, identity: dict = Depends(session)) -> dict:
        check_origin(request.headers.get("origin"))
        csrf = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(csrf.encode("utf-8"), identity["csrf_token"].encode("utf-8")):
            raise HTTPException(403, "Invalid CSRF token; reload and try again")
        return identity["user"]

    def administrator(viewer: dict = Depends(user)) -> dict:
        if viewer["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        return viewer

    def admin_mutation(viewer: dict = Depends(mutation)) -> dict:
        if viewer["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        return viewer

    def login_request(request: Request):
        # JSON plus a strict Origin allowlist prevents cross-site form/login CSRF.
        check_origin(request.headers.get("origin"))
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "Use application/json")

    def start_session(request: Request, response: Response, credentials: Credentials) -> dict:
        result = auth.login(
            credentials.username,
            credentials.password,
            remote_key=request.client.host if request.client else "unknown",
        )
        previous = request.cookies.get(COOKIE_NAME)
        if previous:
            auth.logout(previous)
        response.set_cookie(
            COOKIE_NAME,
            result["token"],
            max_age=max(1, int(result["expires_at"] - time.time())),
            httponly=True,
            secure=secure_cookie,
            samesite="strict",
            path="/",
        )
        return {"user": result["user"], "csrf_token": result["csrf_token"]}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post(
        "/api/auth/register",
        status_code=201,
        response_model=AuthResponse,
        dependencies=[Depends(login_request)],
    )
    def register(credentials: Credentials, request: Request, response: Response):
        auth.register(
            credentials.username,
            credentials.password,
            remote_key=request.client.host if request.client else "unknown",
        )
        return start_session(request, response, credentials)

    @app.post("/api/auth/login", response_model=AuthResponse, dependencies=[Depends(login_request)])
    def login(credentials: Credentials, request: Request, response: Response):
        return start_session(request, response, credentials)

    @app.get("/api/auth/me", response_model=AuthResponse)
    def me(identity: dict = Depends(session)):
        return {"user": identity["user"], "csrf_token": identity["csrf_token"]}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, viewer: dict = Depends(mutation)):
        auth.logout(request.cookies[COOKIE_NAME])
        response.delete_cookie(COOKIE_NAME, httponly=True, secure=secure_cookie, samesite="strict")
        return {"ok": True}

    @app.get("/api/admin/users", response_model=UsersResponse)
    def users(viewer: dict = Depends(administrator)):
        return {"users": auth.list_users()}

    @app.get("/api/runs", response_model=RunsResponse)
    def runs(viewer: dict = Depends(user)):
        return {"runs": receiver.list_runs(viewer)}

    @app.post("/api/runs", status_code=201, response_model=RunView)
    def create_run(config: RunConfig, viewer: dict = Depends(admin_mutation)):
        known = {account["id"] for account in auth.list_users()}
        if any(
            b.owner_user_id is not None and b.owner_user_id not in known for b in config.bidders
        ):
            raise HTTPException(422, "Assigned bidder account does not exist")
        return receiver.create(config, viewer)

    @app.get("/api/runs/{run_id}", response_model=RunView)
    def get_run(run_id: str, viewer: dict = Depends(user)):
        return receiver.get(run_id, viewer)

    @app.post("/api/runs/{run_id}/advance", response_model=RunView)
    def advance(run_id: str, viewer: dict = Depends(admin_mutation)):
        return receiver.advance(run_id, viewer)

    @app.post("/api/runs/{run_id}/execute", response_model=RunView)
    def execute(run_id: str, viewer: dict = Depends(admin_mutation)):
        return receiver.execute(run_id, viewer)

    @app.get("/api/runs/{run_id}/events", response_model=EventsResponse)
    def events(
        run_id: str, after: int = Query(default=0, ge=0, le=2**63 - 1), viewer: dict = Depends(user)
    ):
        return {"events": receiver.events(run_id, after, viewer)}

    @app.get("/api/runs/{run_id}/replay", response_model=RunView)
    def replay(
        run_id: str,
        through: int | None = Query(default=None, ge=1, le=2**63 - 1),
        viewer: dict = Depends(user),
    ):
        return receiver.replay(run_id, through, viewer)

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str, viewer: dict = Depends(user)):
        return JSONResponse(
            receiver.export(run_id, viewer),
            headers={"Content-Disposition": f'attachment; filename="auction-{run_id}.json"'},
        )

    @app.websocket("/api/runs/{run_id}/ws")
    async def stream(
        websocket: WebSocket, run_id: str, after: int = Query(default=0, ge=0, le=2**63 - 1)
    ):
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            await websocket.close(code=4403)
            return
        token = websocket.cookies.get(COOKIE_NAME, "")
        identity = await run_in_threadpool(auth.authenticate, token)
        if identity is None:
            await websocket.close(code=4401)
            return
        try:
            await run_in_threadpool(receiver.get, run_id, identity["user"])
        except KeyError:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        cursor, idle_ticks = after, 0
        try:
            while True:
                # Recheck expiry/logout and current role while the connection remains open.
                identity = await run_in_threadpool(auth.authenticate, token)
                if identity is None:
                    await websocket.close(code=4401)
                    break
                try:
                    batch = await run_in_threadpool(
                        receiver.events, run_id, cursor, identity["user"]
                    )
                except KeyError:
                    await websocket.close(code=4404)
                    break
                if batch:
                    await websocket.send_json({"type": "events", "events": batch})
                    cursor, idle_ticks = batch[-1]["seq"], 0
                else:
                    idle_ticks += 1
                    if idle_ticks % 20 == 0:
                        await websocket.send_json({"type": "heartbeat"})
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
