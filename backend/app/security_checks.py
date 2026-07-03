"""啟動期安全設定檢查（S2：JWT 預設密鑰生產防護）。

抽成純函式以便單元測試。`main.py` 的 lifespan 啟動時（init_db 之後）呼叫
`assert_secure_config(settings)`：

- production 且 jwt_secret 仍為預設值或長度 < 32 → raise RuntimeError（fail-fast，
  拒絕以不安全設定啟動）。
- 非 production 但使用弱／預設密鑰 → 僅記錄 warning，不阻擋開發／測試。
"""
import logging

from .config import DEFAULT_JWT_SECRET

logger = logging.getLogger("uvicorn.error")

MIN_SECRET_LEN = 32


def _secret_is_weak(secret: str) -> bool:
    return secret == DEFAULT_JWT_SECRET or len(secret) < MIN_SECRET_LEN


def assert_secure_config(settings) -> None:
    """驗證關鍵安全設定；production 下不安全即 raise RuntimeError。"""
    if _secret_is_weak(settings.jwt_secret):
        if settings.environment == "production":
            raise RuntimeError(
                "不安全的 JWT 設定：production 環境的 BOOK_JWT_SECRET 仍為預設值或"
                f"長度不足（需 ≥ {MIN_SECRET_LEN} 字元的強隨機值）。請設定 "
                "BOOK_JWT_SECRET 後再啟動。"
            )
        logger.warning(
            "JWT 密鑰為預設或弱值（長度 < %d）；僅適用於開發／測試環境。"
            "生產部署請設定強隨機的 BOOK_JWT_SECRET。",
            MIN_SECRET_LEN,
        )
