from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.auth import APIKeyMiddleware
from app.mcp_gateway import (
    MCPKnowledgeDispatchCall,
    MCPPlanningDispatchCall,
    MCPProfileDispatchCall,
    create_onboarding_mcp,
)
from app.registry import AGENTS
from app.schemas import DispatchRequest, DispatchResponse, DispatchResult


class RecordingDispatcher:
    def __init__(self) -> None:
        self.request: DispatchRequest | None = None

    async def dispatch(self, request: DispatchRequest) -> DispatchResponse:
        self.request = request
        call = request.calls[0]
        return DispatchResponse(
            mode=request.mode,
            results=[
                DispatchResult(
                    agent=call.agent,
                    skill_id=call.skill_id,
                    status="TASK_STATE_COMPLETED",
                    context_id=call.request.correlation_id,
                    artifact={"mock": True},
                )
            ],
        )


def build_test_app() -> tuple[FastAPI, Any, RecordingDispatcher]:
    dispatcher = RecordingDispatcher()
    mcp = create_onboarding_mcp(
        dispatcher,
        public_base_url="https://onboarding.example.test",
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        APIKeyMiddleware,
        header_name="X-A2A-API-Key",
        expected_key="a2a-secret",
        mcp_bearer_token="mcp-secret",
        executor_callback_bearer_token="callback-secret",
    )

    @app.post("/orchestrator/dispatch")
    async def rest_dispatch() -> dict[str, bool]:
        return {"ok": True}

    app.mount("/mcp", mcp.streamable_http_app())
    return app, mcp, dispatcher


def dispatch_arguments() -> dict[str, Any]:
    return {
        "mode": "parallel",
        "calls": [
            {
                "agent": "profile",
                "skill_id": "get_employee_onboarding_profile",
                "request": {
                    "operation": "GENERATE_PLAN",
                    "request_id": "req-123",
                    "run_id": "run-123",
                    "correlation_id": "case-123:req-123",
                    "employee_id": "employee-123",
                    "payload": {},
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_mcp_tool_is_typed_and_invokes_shared_dispatcher() -> None:
    app, mcp, dispatcher = build_test_app()
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer mcp-secret",
        "Host": "onboarding.example.test",
    }

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=transport,
            base_url="https://onboarding.example.test",
            headers=headers,
            follow_redirects=True,
        ) as http_client,
        streamable_http_client(
            "https://onboarding.example.test/mcp",
            http_client=http_client,
            terminate_on_close=False,
        ) as (read_stream, write_stream, _),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool(
            "dispatch_onboarding_agents",
            dispatch_arguments(),
        )

    assert [tool.name for tool in tools.tools] == ["dispatch_onboarding_agents"]
    schema = tools.tools[0].inputSchema
    assert schema["properties"]["calls"]["minItems"] == 1
    assert schema["properties"]["calls"]["maxItems"] == 10
    definitions = schema["$defs"]
    model_skills = {
        "profile": set(
            definitions[MCPProfileDispatchCall.__name__]["properties"]["skill_id"]["enum"]
        ),
        "knowledge": set(
            definitions[MCPKnowledgeDispatchCall.__name__]["properties"]["skill_id"]["enum"]
        ),
        "planning": set(
            definitions[MCPPlanningDispatchCall.__name__]["properties"]["skill_id"]["enum"]
        ),
    }
    assert model_skills == {key: set(spec.skill_ids) for key, spec in AGENTS.items()}
    assert result.isError is False
    assert result.structuredContent == {
        "mode": "parallel",
        "results": [
            {
                "agent": "profile",
                "skill_id": "get_employee_onboarding_profile",
                "status": "TASK_STATE_COMPLETED",
                "task_id": None,
                "context_id": "case-123:req-123",
                "artifact": {"mock": True},
                "error": None,
            }
        ],
    }
    assert dispatcher.request is not None
    assert dispatcher.request.calls[0].request.employee_id == "employee-123"


@pytest.mark.asyncio
async def test_mcp_and_a2a_credentials_are_not_interchangeable() -> None:
    app, _, _ = build_test_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://onboarding.example.test",
    ) as client:
        missing_mcp = await client.post("/mcp/", json={})
        a2a_key_on_mcp = await client.post(
            "/mcp/",
            headers={"X-A2A-API-Key": "a2a-secret"},
            json={},
        )
        missing_callback_token = await client.post(
            "/executors/profile/callback",
            json={},
        )
        mcp_token_on_rest = await client.post(
            "/orchestrator/dispatch",
            headers={"Authorization": "Bearer mcp-secret"},
            json={},
        )
        a2a_key_on_rest = await client.post(
            "/orchestrator/dispatch",
            headers={"X-A2A-API-Key": "a2a-secret"},
            json={},
        )

    assert missing_mcp.status_code == 401
    assert missing_mcp.json()["error"] == "MCP_AUTHENTICATION_REQUIRED"
    assert a2a_key_on_mcp.status_code == 401
    assert missing_callback_token.status_code == 401
    assert (
        missing_callback_token.json()["error"]
        == "EXECUTOR_CALLBACK_AUTHENTICATION_REQUIRED"
    )
    assert mcp_token_on_rest.status_code == 401
    assert a2a_key_on_rest.status_code == 200
