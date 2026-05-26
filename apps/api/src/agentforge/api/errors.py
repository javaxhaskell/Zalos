"""Typed error hierarchy and global FastAPI exception handler.

Every error surfaced to the user follows the same envelope:

    { "error_code": "...", "message": "...", "technical_detail": "...", "session_id": "..." }

The frontend's ``ErrorBanner`` component renders all errors through this shape.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import OperationalError

from agentforge.schemas.common import ErrorCode


class APIError(Exception):
    """Base for typed application errors.

    All subclasses set an ``error_code`` from the canonical enum so the UI
    can render a deterministic message.
    """

    error_code: ErrorCode = ErrorCode.UNKNOWN
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        technical_detail: str | None = None,
        session_id: UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.technical_detail = technical_detail
        self.session_id = session_id
        self.details = details or {}

    def to_envelope(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code.value,
            "message": self.message,
            "technical_detail": self.technical_detail,
            "session_id": str(self.session_id) if self.session_id else None,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Upload-time errors
# ---------------------------------------------------------------------------


class FileTooLargeError(APIError):
    error_code = ErrorCode.FILE_TOO_LARGE
    http_status = 413


class UnsupportedFileTypeError(APIError):
    error_code = ErrorCode.UNSUPPORTED_FILE_TYPE
    http_status = 415


class UploadLimitExceededError(APIError):
    error_code = ErrorCode.UPLOAD_LIMIT_EXCEEDED
    http_status = 413


class MalformedCSVError(APIError):
    error_code = ErrorCode.MALFORMED_CSV
    http_status = 422


# ---------------------------------------------------------------------------
# Tool / loop errors
# ---------------------------------------------------------------------------


class ToolNotRegisteredError(APIError):
    error_code = ErrorCode.TOOL_NOT_REGISTERED
    http_status = 400


class ValidationLoopExhaustedError(APIError):
    error_code = ErrorCode.VALIDATION_LOOP_EXHAUSTED
    http_status = 500


class GeneratedCodeFailedError(APIError):
    error_code = ErrorCode.GENERATED_CODE_FAILED
    http_status = 500


class TestFailedError(APIError):
    error_code = ErrorCode.TEST_FAILED
    http_status = 200  # not an HTTP error; user-visible workflow state


class CommandTimeoutError(APIError):
    error_code = ErrorCode.COMMAND_TIMEOUT
    http_status = 504


# ---------------------------------------------------------------------------
# Workflow state errors
# ---------------------------------------------------------------------------


class WorkflowStateError(APIError):
    """Raised when an illegal state transition is attempted."""

    error_code = ErrorCode.UNKNOWN
    http_status = 409


class ApprovalRequiredError(APIError):
    error_code = ErrorCode.APPROVAL_DECLINED
    http_status = 409


# ---------------------------------------------------------------------------
# Budget errors
# ---------------------------------------------------------------------------


class BudgetExhaustedError(APIError):
    http_status = 429

    def __init__(self, error_code: ErrorCode, message: str, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.error_code = error_code


# ---------------------------------------------------------------------------
# Global handlers
# ---------------------------------------------------------------------------


def register_exception_handlers(app: FastAPI) -> None:
    """Wire global exception handlers into a FastAPI app."""

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=jsonable_encoder(exc.to_envelope()),
        )

    @app.exception_handler(OperationalError)
    async def handle_db_operational_error(
        request: Request, exc: OperationalError
    ) -> JSONResponse:
        detail = str(exc.orig) if exc.orig is not None else str(exc)
        lowered = detail.lower()
        if "no such column" in lowered or "deleted_at" in lowered:
            return JSONResponse(
                status_code=503,
                content=jsonable_encoder(
                    {
                        "error_code": "db_schema_outdated",
                        "message": (
                            "Database schema is out of date. "
                            "Run `make migrate` from the repo root."
                        ),
                        "technical_detail": detail,
                    }
                ),
            )
        return JSONResponse(
            status_code=500,
            content=jsonable_encoder(
                {
                    "error_code": ErrorCode.UNKNOWN.value,
                    "message": "Database error.",
                    "technical_detail": detail,
                }
            ),
        )

    @app.exception_handler(PydanticValidationError)
    async def handle_validation_error(
        request: Request, exc: PydanticValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {
                    "error_code": "validation_error",
                    "message": "Request validation failed.",
                    "details": exc.errors(),
                }
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Last-resort handler; never leak stack traces to the client.
        return JSONResponse(
            status_code=500,
            content=jsonable_encoder(
                {
                    "error_code": ErrorCode.UNKNOWN.value,
                    "message": "Something went wrong on the server.",
                    "technical_detail": type(exc).__name__,
                }
            ),
        )
