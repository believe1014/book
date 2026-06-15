"""FastAPI entry point for 協作撰書系統 backend (spec §8.1/8.2)."""
import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .database import init_db
from .errors import (
    APIError, api_error_handler, http_exception_handler,
    validation_exception_handler,
)
from .routers import auth, books, chapters, content, media, ws

app = FastAPI(title="協作撰書系統 API", version="1.0")

# CORS for the Vite dev server (spec §8.1)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Unified error envelope (spec §5.1)
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)


@app.on_event("startup")
def on_startup():
    init_db()
    os.makedirs(settings.storage_dir, exist_ok=True)


# Serve uploaded media (spec §6.5: local ./storage)
os.makedirs(settings.storage_dir, exist_ok=True)
app.mount("/storage", StaticFiles(directory=settings.storage_dir), name="storage")

# Routers
app.include_router(auth.router)
app.include_router(books.router)
app.include_router(chapters.router)
app.include_router(content.router)
app.include_router(media.router)
app.include_router(ws.router)


@app.get("/api/health")
def health():
    return {"data": {"status": "ok"}}
