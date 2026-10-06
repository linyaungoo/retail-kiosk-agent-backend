"""Structured API errors. Stack traces are logged, never returned to the client."""

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.logging import get_logger

logger = get_logger(__name__)


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_LANGUAGE = "INVALID_LANGUAGE"
    INVALID_KIOSK = "INVALID_KIOSK"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    AGENT_FAILED = "AGENT_FAILED"
    AGENT_NOT_CONFIGURED = "AGENT_NOT_CONFIGURED"
    INVALID_AUDIO = "INVALID_AUDIO"
    UNSUPPORTED_AUDIO = "UNSUPPORTED_AUDIO"
    AUDIO_TOO_LARGE = "AUDIO_TOO_LARGE"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    STT_FAILED = "STT_FAILED"
    STT_TIMEOUT = "STT_TIMEOUT"
    STT_NOT_CONFIGURED = "STT_NOT_CONFIGURED"
    EMPTY_TRANSCRIPT = "EMPTY_TRANSCRIPT"
    TTS_FAILED = "TTS_FAILED"
    TTS_TIMEOUT = "TTS_TIMEOUT"
    TTS_NOT_CONFIGURED = "TTS_NOT_CONFIGURED"
    REALTIME_NOT_CONFIGURED = "REALTIME_NOT_CONFIGURED"
    REALTIME_FAILED = "REALTIME_FAILED"
    REALTIME_TIMEOUT = "REALTIME_TIMEOUT"
    REALTIME_SESSION_NOT_FOUND = "REALTIME_SESSION_NOT_FOUND"
    TOO_MANY_SESSIONS = "TOO_MANY_SESSIONS"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    DATA_SOURCE_ERROR = "DATA_SOURCE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """Raise from services/routes to return a structured error to the kiosk.

    `context` adds top-level fields to the error body, e.g. the transcript and
    answer text when only speech synthesis failed, so the kiosk can still show them.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.context = context or {}


def error_response(
    code: str, message: str, status_code: int, context: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": {"code": code, "message": message}, **(context or {})},
    )


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    logger.warning("app_error", extra={"error_code": exc.code, "status_code": exc.status_code})
    return error_response(exc.code, exc.message, exc.status_code, exc.context)


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    if any(err.get("loc", ())[-1:] == ("language",) for err in exc.errors()):
        return error_response(
            ErrorCode.INVALID_LANGUAGE, "Unsupported language. Use my-MM or en-US.", 422
        )
    fields = [".".join(str(p) for p in err.get("loc", ()) if p != "body") for err in exc.errors()]
    fields = [f for f in fields if f]
    message = f"Invalid request: {', '.join(fields)}" if fields else "Invalid request."
    return error_response(ErrorCode.VALIDATION_ERROR, message, 422)


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = {
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.UNAUTHORIZED,
        404: ErrorCode.NOT_FOUND,
    }.get(exc.status_code, ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else "HTTP_ERROR")
    return error_response(code, str(exc.detail), exc.status_code)


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_error", exc_info=exc)
    return error_response(ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.", 500)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
