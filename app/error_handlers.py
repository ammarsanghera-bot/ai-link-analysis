"""Exception handlers and request middleware.

All errors are returned in one envelope (``ErrorResponse``). Stack traces and
internal details are written to the logs only, never to the client.
"""

import logging
import re
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.exceptions import AppError
from app.logging_config import request_id_ctx
from app.schemas import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed"}


def error_response(
    status_code: int, code: str, message: str, details: list[dict[str, Any]] | None = None
) -> JSONResponse:
    """Build the standard error envelope."""
    request_id = request_id_ctx.get()
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details),
        request_id=None if request_id == "-" else request_id,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def register_error_handling(app: FastAPI) -> None:
    """Attach exception handlers and the request-context middleware to ``app``."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        log = logger.error if exc.status_code >= 500 else logger.warning
        log(
            "request failed",
            extra={"error_code": exc.code, "status_code": exc.status_code, "detail": exc.message},
        )
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                # A JSON syntax error is located by character offset, not by field.
                "field": "body"
                if error["type"] == "json_invalid"
                else ".".join(str(part) for part in error["loc"] if part != "body") or "body",
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return error_response(422, "invalid_request", "The request body is invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_ERROR_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "The request could not be handled."
        return error_response(exc.status_code, code, message)

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Assign a request ID, log the request and catch unexpected errors."""
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Last line of defence: log the traceback, return a generic 500.
                logger.exception(
                    "unhandled error",
                    extra={"method": request.method, "path": request.url.path},
                )
                response = error_response(
                    500, "internal_error", "An unexpected internal error occurred."
                )
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                },
            )
            return response
        finally:
            request_id_ctx.reset(token)
