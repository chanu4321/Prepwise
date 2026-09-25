import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE = "Something went wrong. Please try again."


class ApiError(Exception):
    """An error the client is allowed to see: returned as {"detail": ..., "code": ...}."""

    def __init__(self, status_code: int, code: str, detail: str, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.headers = headers


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code, headers=exc.headers)


async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.info("Request validation failed on %s %s: %s", request.method, request.url.path, [(e.get("loc"), e.get("type")) for e in exc.errors()])
    return JSONResponse({"detail": "Some of the information sent was missing or invalid.", "code": "invalid_request"}, status_code=422)


class CatchAllErrorsMiddleware:
    """Turns unhandled exceptions into a generic JSON 500.

    Installed inside CORSMiddleware so error responses still carry CORS headers
    (Starlette's own 500 handler sits outside CORS, so browsers couldn't read it).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if response_started:
                raise
            response = JSONResponse({"detail": GENERIC_ERROR_MESSAGE, "code": "internal_error"}, status_code=500)
            await response(scope, receive, send)


def install_error_handling(app: FastAPI) -> None:
    """Call before adding CORSMiddleware: the last middleware added is the outermost."""
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_middleware(CatchAllErrorsMiddleware)
