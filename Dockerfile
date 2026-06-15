# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend (Vite/React) ----
FROM node:20-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime serving API + built SPA ----
FROM python:3.12-slim AS runtime

# bcrypt/cryptography wheels are prebuilt; keep the image lean.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install backend dependencies first for better layer caching.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Backend source.
COPY backend/ ./backend/

# Built frontend from stage 1 (config.frontend_dir = ../frontend/dist from backend/).
COPY --from=frontend /build/frontend/dist ./frontend/dist

# App data dirs (overridden by a mounted volume in production if desired).
RUN mkdir -p backend/storage

EXPOSE 8000

# Run from backend/ so settings paths (app.db, storage/) resolve as in dev.
WORKDIR /app/backend
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
