from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Protect A2A and dispatcher endpoints while leaving discovery cards public."""

    def __init__(self, app: ASGIApp, *, header_name: str, expected_key: str) -> None:
        super().__init__(app)
        self.header_name = header_name
        self.expected_key = expected_key

    @staticmethod
    def _is_public(path: str) -> bool:
        return (
            path in {"/", "/healthz", "/readyz", "/docs", "/openapi.json"}
            or path.endswith("/.well-known/agent-card.json")
        )

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        if self._is_public(request.url.path):
            return await call_next(request)

        supplied = request.headers.get(self.header_name, "")
        if not supplied or not hmac.compare_digest(supplied, self.expected_key):
            return JSONResponse(
                {
                    "type": "https://a2a-protocol.org/errors/unauthorized",
                    "title": "Unauthorized",
                    "status": 401,
                    "detail": f"Missing or invalid {self.header_name} header",
                },
                status_code=401,
                headers={"WWW-Authenticate": f'ApiKey name="{self.header_name}"'},
            )
        return await call_next(request)
