"""Consistent JSON envelope: every REST response is {"data": ..., "error": ...}.

Status codes:
  400  malformed request (invalid JSON, bad UUID, empty update)
  401  missing/invalid webhook secret
  404  patient or route not found
  422  well-formed request that fails validation
  500  unexpected server or database error (never leaks internals)
"""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas import format_validation_errors

log = logging.getLogger("app.errors")


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: list | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class NotFoundError(ApiError):
    def __init__(self, message: str = "Patient not found"):
        super().__init__(404, "not_found", message)


def envelope(data: Any = None, error: dict | None = None) -> dict:
    return {"data": data, "error": error}


def error_response(status_code: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    error = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status_code, content=envelope(None, error))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError):
        errors = exc.errors()
        if any(e.get("type") == "json_invalid" for e in errors):
            return error_response(400, "invalid_json", "Request body is not valid JSON")
        return error_response(
            422, "validation_error", "One or more fields are invalid",
            format_validation_errors(errors),
        )

    @app.exception_handler(ValidationError)
    async def _pydantic_validation(_: Request, exc: ValidationError):
        return error_response(
            422, "validation_error", "One or more fields are invalid",
            format_validation_errors(exc.errors()),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return error_response(404, "not_found", f"No route for {request.method} {request.url.path}")
        return error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError):
        log.warning("integrity_error: %s", exc.orig)
        return error_response(422, "constraint_violation", "The data violates a database constraint")

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError):
        log.exception("database_error")
        return error_response(500, "database_error", "A database error occurred. Please try again.")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled_error")
        return error_response(500, "internal_error", "An unexpected error occurred.")
