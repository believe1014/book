"""Pytest harness: in-process FastAPI TestClient against a throwaway SQLite DB.

環境變數必須在 import app 之前設定（config/engine 於 import 時綁定）。每個測試
以 drop_all/create_all 重建 schema 達成隔離。回應信封統一為 {"data": ...}。
"""
import os
import pathlib
import tempfile

# ── 必須在 import app 之前設定環境 ──────────────────────────────────────────────
_TMPDIR = tempfile.mkdtemp(prefix="booktest_")
os.environ["BOOK_DB_PATH"] = str(pathlib.Path(_TMPDIR) / "test.db")
os.environ["BOOK_JWT_SECRET"] = "test-secret-for-pytest-only"
os.environ["BOOK_STORAGE_DIR"] = str(pathlib.Path(_TMPDIR) / "storage")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app import models  # noqa: E402,F401  — 確保所有資料表註冊進 metadata
from app.database import engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.locks import lock_manager  # noqa: E402
from app.services.rate_limit import login_rate_limiter  # noqa: E402

init_db()


@pytest.fixture(scope="session")
def client():
    """Session 級 TestClient（觸發 lifespan：init_db + MCP session manager）。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_db(client):
    """每個測試前重建 schema 並清空行程級全域鎖狀態，達成完整隔離。

    lock_manager 是行程級單例（記憶體），不隨 DB 重建而清空；每次測試 DB 從空
    schema 重新自增 id，若不清鎖會有跨測試/跨檔案的 chapter_id 汙染（造成 test_ws
    等檔案間歇性 KeyError）。故在此全域統一清空，取代各測試檔各自定義的清鎖 fixture。
    """
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    if hasattr(lock_manager, "_locks"):
        lock_manager._locks.clear()
    # S3：清空行程級登入限流狀態，避免跨測試污染既有登入測試。
    login_rate_limiter.clear()
    yield
    if hasattr(lock_manager, "_locks"):
        lock_manager._locks.clear()
    login_rate_limiter.clear()


def _register(client, email, password, name):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "name": name or email.split("@")[0]},
    )


@pytest.fixture
def user_factory(client):
    """建立使用者並回傳 {token, headers, user}。"""
    def make(email="a@test.com", password="pw-123456", name=None):
        r = _register(client, email, password, name)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        token = data["token"]
        return {
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
            "user": data["user"],
        }
    return make


@pytest.fixture
def auth(user_factory):
    """預設使用者（a@test.com）。"""
    return user_factory()
