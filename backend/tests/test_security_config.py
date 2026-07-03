"""S2：JWT 預設密鑰生產防護 — assert_secure_config 單元測試。

以純函式方式驗證安全設定檢查，不觸發完整 app lifespan。使用簡單的假 settings
物件（只帶 environment / jwt_secret 兩個屬性）即可，避免污染全域 settings。
"""
import logging
import types

import pytest

from app.config import DEFAULT_JWT_SECRET
from app.security_checks import MIN_SECRET_LEN, assert_secure_config

STRONG_SECRET = "x" * MIN_SECRET_LEN  # 剛好 32 字元的強度示意值


def _settings(environment, jwt_secret, database_url=None):
    return types.SimpleNamespace(
        environment=environment, jwt_secret=jwt_secret, database_url=database_url
    )


def test_production_with_default_secret_raises():
    with pytest.raises(RuntimeError):
        assert_secure_config(_settings("production", DEFAULT_JWT_SECRET))


def test_production_with_short_secret_raises():
    with pytest.raises(RuntimeError):
        assert_secure_config(_settings("production", "too-short"))


def test_production_with_strong_secret_ok():
    # 不應 raise
    assert_secure_config(_settings("production", STRONG_SECRET))


def test_development_with_default_secret_only_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        assert_secure_config(_settings("development", DEFAULT_JWT_SECRET))
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_development_with_strong_secret_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        assert_secure_config(_settings("development", STRONG_SECRET))
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


# ── 修復 3：接上正式 DB 即使忘設 BOOK_ENVIRONMENT 也 fail-fast ────────────────
def test_database_url_set_with_default_secret_raises_even_in_development():
    """已設 database_url（生產通常掛 managed Postgres）+ 預設密鑰 + environment
    未設(development) → 仍 raise，避免忘設 BOOK_ENVIRONMENT 造成閘門失效。"""
    with pytest.raises(RuntimeError):
        assert_secure_config(
            _settings(
                "development",
                DEFAULT_JWT_SECRET,
                database_url="postgresql://user:pw@db.example.com:5432/book",
            )
        )


def test_no_database_url_development_default_only_warns(caplog):
    """純本機 SQLite 開發（無 database_url + development + 預設密鑰）→ 僅警告。"""
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        assert_secure_config(_settings("development", DEFAULT_JWT_SECRET, database_url=None))
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_environment_case_and_whitespace_normalized():
    """environment 比對前 strip + lower，"Production " 也視為生產。"""
    with pytest.raises(RuntimeError):
        assert_secure_config(_settings("Production ", DEFAULT_JWT_SECRET))


def test_conftest_like_env_does_not_raise():
    """模擬 conftest 測試環境（development、非預設但短密鑰、無 database_url）不 raise。

    註：conftest 用的 test-secret 長度 < 32 屬弱值，但無 database_url 且 development，
    故僅警告、不阻擋 app 啟動。
    """
    assert_secure_config(
        _settings("development", "test-secret-for-pytest-only", database_url=None)
    )
