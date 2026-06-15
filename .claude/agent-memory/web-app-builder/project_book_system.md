---
name: project-book-system
description: 協作撰書系統 — collaborative book-writing platform built from spec.md + design.md at C:\Users\admin\OneDrive\Desktop\book
metadata:
  type: project
---

協作撰書系統 (Collaborative Book Writing System) — multi-user writing platform with Book → Chapter → Content 3-level structure.

**Stack (spec §8.1):** Backend = Python 3.13, FastAPI, Uvicorn, Pydantic v2, SQLModel, SQLite (app.db, WAL), JWT (python-jose), bcrypt (passlib), WebSocket. Frontend = React 18 + Vite, React Router, Zustand, contentEditable rich text (TipTap optional), fetch.

**Brand / design tokens (design.md §10):** warm white bg #FAF9F7, surface #FFFFFF, brand ink-green #2D6A4F (hover #245A42), text #1F2328 / muted #6B7280, border #E4E1DA. Noto Sans TC, body line-height 1.75, editor max-width 720px. radius sm4/md8/lg12. Top bar 56px, left col 280px, right col 320px. 3-column editor layout.

**Word count rule (spec §4.3):** CJK chars counted individually + English/number whitespace-tokens. Mixed content must work (acceptance item).

**Project name to watch on rename:** "撰書系統" / "協作撰書系統" / "BookWriter".

**Why:** Built per two authoritative spec docs; implementation must stay faithful to FR codes, API paths in spec §5, data tables in spec §4, and design.md wireframes.
**How to apply:** When editing this project, re-read spec.md/design.md sections before changing API or schema.

**Status (built 2026-06-14):** Phase 1 (P0) + Phase 2 (P1) implemented and verified end-to-end. Backend `backend/`, frontend `frontend/`.

**Startup:** backend = `backend/venv/Scripts/python.exe -m uvicorn app.main:app --port 8000`; frontend = `cd frontend; npm run dev` (Vite :5173, proxies /api /storage /ws to :8000). README.md has full instructions.

**Verified working (end-to-end):** auth/JWT, books CRUD+softdelete+restore+trash, members/invitations+permission matrix (viewer 403/read-only UI), chapters CRUD+2-level enforcement+cascade delete+reorder, content autosave (debounce 2s)+version snapshots+409 conflict+423 lock, stats (mixed CJK+English word count verified = 9 字 for "這是測試內容 hello world test"), version history list/preview/restore, media upload+quota, WebSocket presence/lock.

**Tests:** `backend/tests/e2e_smoke.py` (35 API checks, run with backend up). `frontend/e2e-ui.mjs` + `e2e-ui2.mjs` (Playwright via system Edge at `C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`, headless — needs both servers up). Playwright is a frontend devDependency.

**Gotcha:** `/api/books/trash` route MUST be declared before `/api/books/{book_id}` in routers/books.py or FastAPI matches trash as book_id.
**Editor choice:** rich text = contentEditable + toolbar (not TipTap) but persists ProseMirror-style doc JSON so backend services/wordcount.py extracts text. Conversion in components/RichTextEditor.jsx (htmlToDoc/docToHtml).
