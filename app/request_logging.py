from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

audit_logger = logging.getLogger("audit.http")

SECRET_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-a2a-api-key",
    "x-api-key",
}
SECRET_FIELD_MARKERS = ("authorization", "api_key", "password", "secret", "token")


class RequestAuditMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        request_body_max_bytes: int = 4000,
        response_body_max_bytes: int = 4000,
    ) -> None:
        self.app = app
        self.request_body_max_bytes = request_body_max_bytes
        self.response_body_max_bytes = response_body_max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter()
        status_code = 500
        request_chunks: list[bytes] = []
        response_chunks: list[bytes] = []
        request_size = 0
        response_size = 0
        request_truncated = False
        response_truncated = False
        error: str | None = None

        async def receive_wrapper() -> Message:
            nonlocal request_size, request_truncated
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                request_size += len(body)
                request_truncated = _append_limited(
                    request_chunks,
                    body,
                    limit=self.request_body_max_bytes,
                )
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal response_size, response_truncated, status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                response_size += len(body)
                response_truncated = _append_limited(
                    response_chunks,
                    body,
                    limit=self.response_body_max_bytes,
                )
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception as exc:
            error = exc.__class__.__name__
            raise
        finally:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            _log_request(
                scope=scope,
                status_code=status_code,
                duration_ms=duration_ms,
                request_body=b"".join(request_chunks),
                request_size=request_size,
                request_truncated=request_truncated,
                response_body=b"".join(response_chunks),
                response_size=response_size,
                response_truncated=response_truncated,
                error=error,
            )


def _append_limited(chunks: list[bytes], body: bytes, *, limit: int) -> bool:
    if limit <= 0 or not body:
        return bool(body)
    current_size = sum(len(chunk) for chunk in chunks)
    remaining = limit - current_size
    if remaining <= 0:
        return True
    chunks.append(body[:remaining])
    return len(body) > remaining


def _log_request(
    *,
    scope: Scope,
    status_code: int,
    duration_ms: float,
    request_body: bytes,
    request_size: int,
    request_truncated: bool,
    response_body: bytes,
    response_size: int,
    response_truncated: bool,
    error: str | None,
) -> None:
    headers = _headers_from_scope(scope)
    request_preview = _json_preview(request_body, truncated=request_truncated)
    response_preview = _json_preview(response_body, truncated=response_truncated)
    identifiers = _extract_identifiers(request_preview)

    audit_logger.info(
        "HTTP request completed",
        extra={
            "event": "http_request",
            "method": scope["method"],
            "path": scope["path"],
            "query_string": scope.get("query_string", b"").decode("latin-1"),
            "status_code": status_code,
            "duration_ms": duration_ms,
            "client_ip": _client_ip(scope, headers),
            "user_agent": headers.get("user-agent"),
            "request_headers": _redact_headers(headers),
            "request_body": request_preview,
            "request_size_bytes": request_size,
            "response_body": response_preview,
            "response_size_bytes": response_size,
            "error": error,
            **identifiers,
        },
    )


def _headers_from_scope(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "[redacted]" if key in SECRET_HEADER_NAMES else value
        for key, value in headers.items()
    }


def _client_ip(scope: Scope, headers: dict[str, str]) -> str | None:
    forwarded_for = headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", maxsplit=1)[0].strip()
    client = scope.get("client")
    if not client:
        return None
    return str(client[0])


def _json_preview(body: bytes, *, truncated: bool) -> Any:
    if not body:
        return None
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        text = body.decode("utf-8", errors="replace")
        return f"{text}...[truncated]" if truncated else text
    redacted = _redact_json(value)
    if truncated:
        return {"truncated": True, "preview": redacted}
    return redacted


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _is_secret_field(key) else _redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    return value


def _is_secret_field(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(marker in normalized for marker in SECRET_FIELD_MARKERS)


def _extract_identifiers(payload: Any) -> dict[str, str]:
    identifiers: dict[str, str] = {}

    def visit(value: Any) -> None:
        if {"request_id", "run_id", "correlation_id"}.issubset(identifiers):
            return
        if isinstance(value, dict):
            for key in ("request_id", "run_id", "correlation_id", "agent", "skill_id"):
                found = value.get(key)
                if isinstance(found, str):
                    identifiers[key] = found
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return identifiers
