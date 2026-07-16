from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.utils.logger import logger


async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError):
    """Handle validation errors. Uses loguru opt(raw=True) to avoid format-string parsing of '{' in error text."""
    logger.opt(raw=True).error("[VALIDATION] " + repr(exc.errors()) + "\n")

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "Validation Error",
            "detail": exc.errors(),
            "message": "Invalid request parameters"
        }
    )


async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException):
    """Handle HTTP exceptions. Avoid loguru format-string parsing on exc.detail."""
    logger.opt(
        raw=True).error(
        f"[HTTP {exc.status_code}] " +
        repr(
            exc.detail) +
        "\n")

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "HTTP Error",
            "status_code": exc.status_code,
            "message": exc.detail
        }
    )


async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions. opt(exception=True) + raw=True so untrusted curly braces don't blow up loguru."""
    logger.opt(
        exception=True,
        raw=True).error(
        "[UNEXPECTED] " +
        repr(
            str(exc)) +
        "\n")

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred. Please try again later."
        }
    )
