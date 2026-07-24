from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Apply separate credentials to MCP and A2A/REST transport routes."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        header_name: str,
        expected_key: str,
        mcp_bearer_token: str,
        executor_callback_bearer_token: str,
    ) -> None:
        super().__init__(app)
        self.header_name = header_name
        self.expected_key = expected_key
        self.expected_mcp_authorization = f"Bearer {mcp_bearer_token}"
        self.expected_callback_authorization = (
            f"Bearer {executor_callback_bearer_token}"
        )

    @staticmethod
    def _is_public(path: str) -> bool:
        return (
            path in {"/", "/healthz", "/readyz", "/docs", "/openapi.json"}
            or path.endswith("/.well-known/agent-card.json")
        )

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        if request.url.path == "/mcp" or request.url.path.startswith("/mcp/"):
            supplied = request.headers.get("Authorization", "")
            if not supplied or not hmac.compare_digest(
                supplied,
                self.expected_mcp_authorization,
            ):
                return JSONResponse(
                    {
                        "type": "https://modelcontextprotocol.io/errors/unauthorized",
                        "title": "Unauthorized",
                        "status": 401,
                        "detail": "Missing or invalid Authorization bearer token",
                        "error": "MCP_AUTHENTICATION_REQUIRED",
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return await call_next(request)

        if request.url.path.startswith("/executors/") and request.url.path.endswith(
            "/callback"
        ):
            supplied = request.headers.get("Authorization", "")
            if not supplied or not hmac.compare_digest(
                supplied,
                self.expected_callback_authorization,
            ):
                return JSONResponse(
                    {
                        "type": "https://a2a-onboarding/errors/executor-callback-unauthorized",
                        "title": "Unauthorized",
                        "status": 401,
                        "detail": "Missing or invalid executor callback bearer token",
                        "error": "EXECUTOR_CALLBACK_AUTHENTICATION_REQUIRED",
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return await call_next(request)

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
