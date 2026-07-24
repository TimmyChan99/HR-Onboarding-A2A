from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from app.logging_config import configure_logging
from app.request_logging import RequestAuditMiddleware


@pytest.mark.asyncio
async def test_request_audit_log_is_written_by_date_with_redaction(
    tmp_path: Path,
) -> None:
    configure_logging("INFO", str(tmp_path))

    app = FastAPI()
    app.add_middleware(RequestAuditMiddleware)

    @app.post("/example")
    async def example(request: Request) -> dict[str, Any]:
        body = await request.json()
        return {
            "accepted": True,
            "request_id": body["request"]["request_id"],
        }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://onboarding.example.test",
    ) as client:
        response = await client.post(
            "/example",
            headers={
                "Authorization": "Bearer secret-token",
                "X-A2A-API-Key": "a2a-secret",
            },
            json={
                "request": {
                    "request_id": "req-123",
                    "run_id": "run-123",
                    "correlation_id": "case-123:req-123",
                },
                "api_key": "hidden",
            },
        )

    assert response.status_code == 200

    audit_path = tmp_path / f"audit-{date.today().isoformat()}.jsonl"
    app_path = tmp_path / f"app-{date.today().isoformat()}.jsonl"
    assert audit_path.exists()
    assert app_path.exists()

    event = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert event["event"] == "http_request"
    assert event["method"] == "POST"
    assert event["path"] == "/example"
    assert event["status_code"] == 200
    assert event["request_id"] == "req-123"
    assert event["request_headers"]["authorization"] == "[redacted]"
    assert event["request_headers"]["x-a2a-api-key"] == "[redacted]"
    assert event["request_body"]["api_key"] == "[redacted]"
    assert event["response_body"] == {"accepted": True, "request_id": "req-123"}
