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


def _settings(environment, jwt_secret):
    return types.SimpleNamespace(environment=environment, jwt_secret=jwt_secret)


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
