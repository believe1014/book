"""啟動期安全設定檢查（S2：JWT 預設密鑰生產防護）。

抽成純函式以便單元測試。`main.py` 的 lifespan 啟動時（init_db 之後）呼叫
`assert_secure_config(settings)`：

fail-fast 條件（raise RuntimeError，拒絕以不安全設定啟動）：
  jwt_secret 為預設值或長度 < 32，且下列任一成立：
    - environment == "production"，或
    - 已設定 database_url（生產通常掛 managed Postgres → DATABASE_URL 存在）。

即使維運忘了設 BOOK_ENVIRONMENT=production，只要接上正式 DB 就會 fail-fast，
避免以弱密鑰啟動。純本機 SQLite 開發（無 database_url 且 development）維持僅
warning，不阻擋開發／測試。

environment 比對前先 strip + lower 正規化，避免 "Production "／大小寫誤判。
"""
import logging

from .config import DEFAULT_JWT_SECRET

logger = logging.getLogger("uvicorn.error")

MIN_SECRET_LEN = 32


def _secret_is_weak(secret: str) -> bool:
    return secret == DEFAULT_JWT_SECRET or len(secret) < MIN_SECRET_LEN


def _is_production(settings) -> bool:
    return str(getattr(settings, "environment", "")).strip().lower() == "production"


def _has_database_url(settings) -> bool:
    return bool(getattr(settings, "database_url", None))


def assert_secure_config(settings) -> None:
    """驗證關鍵安全設定；不安全且處於（或疑似）生產環境即 raise RuntimeError。"""
    if _secret_is_weak(settings.jwt_secret):
        if _is_production(settings) or _has_database_url(settings):
            raise RuntimeError(
                "不安全的 JWT 設定：偵測到生產環境（environment=production 或已設定 "
                "DATABASE_URL），但 BOOK_JWT_SECRET 仍為預設值或長度不足"
                f"（需 ≥ {MIN_SECRET_LEN} 字元的強隨機值）。請設定 BOOK_JWT_SECRET "
                "後再啟動。"
            )
        logger.warning(
            "JWT 密鑰為預設或弱值（長度 < %d）；僅適用於開發／測試環境。"
            "生產部署請設定強隨機的 BOOK_JWT_SECRET。",
            MIN_SECRET_LEN,
        )
