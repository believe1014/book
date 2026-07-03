"""FastAPI entry point for 協作撰書系統 backend (spec §8.1/8.2)."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .database import init_db
from .errors import (
    APIError, api_error_handler, http_exception_handler,
    validation_exception_handler,
)
from .mcp_server import mcp_server
from .routers import auth, books, chapters, comments, content, media, ws
from .security_checks import assert_secure_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # S2: 拒絕以不安全的 JWT 設定在 production 啟動（fail-fast）。
    assert_secure_config(settings)
    os.makedirs(settings.storage_dir, exist_ok=True)
    # Run the MCP streamable-HTTP session manager for the app's lifetime
    # (the mounted sub-app's own lifespan is not invoked by the parent).
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(title="協作撰書系統 API", version="1.0", lifespan=lifespan)

# CORS for the Vite dev server (spec §8.1)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# S5：輕量安全回應標頭（不設 CSP 以免破壞現有 SPA）。
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


# Unified error envelope (spec §5.1)
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)


# Serve uploaded media (spec §6.5: local ./storage)
os.makedirs(settings.storage_dir, exist_ok=True)
app.mount("/storage", StaticFiles(directory=settings.storage_dir), name="storage")

# MCP server (remote streamable HTTP) — Bearer-JWT authenticated. Mounted so it
# ships with the same deployment; reachable at /mcp.
app.mount("/mcp", mcp_server.streamable_http_app())

# Routers
app.include_router(auth.router)
app.include_router(books.router)
app.include_router(chapters.router)
app.include_router(content.router)
app.include_router(media.router)
app.include_router(comments.router)
app.include_router(ws.router)


@app.get("/api/health")
def health():
    return {"data": {"status": "ok"}}


# Serve the built frontend SPA for single-container deploys. Registered last so
# /api, /storage and /ws always take precedence. Unknown (non-asset) paths fall
# back to index.html for client-side routing (react-router).
if os.path.isdir(settings.frontend_dir):
    _frontend_root = os.path.realpath(settings.frontend_dir)
    _index_html = os.path.join(_frontend_root, "index.html")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith(("api/", "storage/", "ws/", "mcp")):
            raise StarletteHTTPException(status_code=404)
        candidate = os.path.realpath(os.path.join(_frontend_root, full_path))
        if (
            full_path
            and candidate.startswith(_frontend_root + os.sep)
            and os.path.isfile(candidate)
        ):
            return FileResponse(candidate)
        return FileResponse(_index_html)
