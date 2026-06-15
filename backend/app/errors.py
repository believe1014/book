"""Unified API error handling (spec §5.1 error codes + envelope)."""
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class APIError(HTTPException):
    """HTTPException carrying a spec §5.1 error `code`."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail=message)
        self.code = code


# Convenience constructors mapping to spec §5.1.
def bad_request(message="參數驗證失敗"):
    return APIError(400, "BAD_REQUEST", message)


def unauthorized(message="未登入或 token 無效"):
    return APIError(401, "UNAUTHORIZED", message)


def forbidden(message="權限不足"):
    return APIError(403, "FORBIDDEN", message)


def not_found(message="資源不存在或已刪除"):
    return APIError(404, "NOT_FOUND", message)


def conflict(message="衝突"):
    return APIError(409, "CONFLICT", message)


def gone(message="已逾期限"):
    return APIError(410, "GONE", message)


def payload_too_large(message="檔案超過上限"):
    return APIError(413, "PAYLOAD_TOO_LARGE", message)


def locked(message="章節編輯權被他人持有"):
    return APIError(423, "LOCKED", message)


def _envelope(code: str, message: str):
    return {"error": {"code": code, "message": message}}


async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(status_code=exc.status_code, content=_envelope(exc.code, exc.detail))


async def http_exception_handler(request: Request, exc: HTTPException):
    code_map = {
        400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN",
        404: "NOT_FOUND", 409: "CONFLICT", 410: "GONE",
        413: "PAYLOAD_TOO_LARGE", 423: "LOCKED", 500: "INTERNAL_ERROR",
    }
    code = code_map.get(exc.status_code, "INTERNAL_ERROR")
    return JSONResponse(status_code=exc.status_code, content=_envelope(code, str(exc.detail)))


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Surface the first validation message for clarity.
    try:
        msg = exc.errors()[0]["msg"]
    except (IndexError, KeyError):
        msg = "參數驗證失敗"
    return JSONResponse(status_code=400, content=_envelope("BAD_REQUEST", msg))
