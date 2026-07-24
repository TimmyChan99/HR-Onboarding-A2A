from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from app.config import Settings
from app.langflow_client import LangflowClient
from app.schemas import ExecutorWebhookCallback


def command() -> dict[str, object]:
    return {
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


def profile_result() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "SUCCEEDED",
        "artifact_type": "EMPLOYEE_PROFILE_CONTEXT",
        "data": {"employee": {"employee_id": "employee-123"}},
        "warnings": [],
        "errors": [],
        "metadata": {},
    }


@pytest.mark.asyncio
@respx.mock
async def test_webhook_mode_posts_raw_command_to_agent_webhook() -> None:
    webhook_url = "https://stg-agentic.example/api/v1/webhook/profile-webhook"
    route = respx.post(webhook_url).mock(
        return_value=httpx.Response(200, json=profile_result())
    )
    settings = Settings(
        _env_file=None,
        langflow_execution_mode="webhook",
        langflow_profile_webhook_url=webhook_url,
    )
    client = LangflowClient(settings)

    try:
        result = await client.run_agent(
            agent_key="profile",
            command=command(),
            session_id="case-123:req-123",
            expected_artifact_type="EMPLOYEE_PROFILE_CONTEXT",
        )
    finally:
        await client.close()

    assert result.artifact_type == "EMPLOYEE_PROFILE_CONTEXT"
    assert route.called
    assert json.loads(route.calls[0].request.content) == command()
    assert str(route.calls[0].request.url.params) == ""


@pytest.mark.asyncio
@respx.mock
async def test_webhook_mode_waits_for_the_executor_callback() -> None:
    webhook_url = "https://stg-agentic.example/api/v1/webhook/profile-webhook"
    triggered = asyncio.Event()

    async def webhook_trigger(_: httpx.Request) -> httpx.Response:
        triggered.set()
        return httpx.Response(
            202,
            json={"message": "Task started in the background", "status": "in progress"},
        )

    respx.post(webhook_url).mock(side_effect=webhook_trigger)
    settings = Settings(
        _env_file=None,
        langflow_execution_mode="webhook",
        langflow_profile_webhook_url=webhook_url,
    )
    client = LangflowClient(settings)

    try:
        waiting_result = asyncio.create_task(
            client.run_agent(
                agent_key="profile",
                command=command(),
                session_id="case-123:req-123",
                expected_artifact_type="EMPLOYEE_PROFILE_CONTEXT",
            )
        )
        await asyncio.wait_for(triggered.wait(), timeout=1)
        accepted = await client.complete_webhook_callback(
            "profile",
            ExecutorWebhookCallback(
                request_id="req-123",
                run_id="run-123",
                correlation_id="case-123:req-123",
                result=profile_result(),
            ),
        )
        result = await waiting_result
    finally:
        await client.close()

    assert accepted is True
    assert result.data["employee"]["employee_id"] == "employee-123"


@pytest.mark.asyncio
@respx.mock
async def test_run_api_mode_keeps_existing_flow_id_request_format() -> None:
    route = respx.post(
        "https://stg-agentic.example/api/v1/run/profile-flow",
        params={"stream": "false"},
    ).mock(return_value=httpx.Response(200, json=profile_result()))
    settings = Settings(
        _env_file=None,
        langflow_base_url="https://stg-agentic.example",
        langflow_execution_mode="run_api",
        langflow_profile_flow_id="profile-flow",
    )
    client = LangflowClient(settings)

    try:
        result = await client.run_agent(
            agent_key="profile",
            command=command(),
            session_id="case-123:req-123",
            expected_artifact_type="EMPLOYEE_PROFILE_CONTEXT",
        )
    finally:
        await client.close()

    assert result.artifact_type == "EMPLOYEE_PROFILE_CONTEXT"
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["input_value"] == json.dumps(command(), ensure_ascii=False)
    assert body["session_id"] == "case-123:req-123"


def test_executor_readiness_uses_the_selected_mode() -> None:
    webhook_settings = Settings(
        _env_file=None,
        langflow_execution_mode="webhook",
        langflow_profile_webhook_url="https://stg-agentic.example/profile",
    )
    run_api_settings = Settings(
        _env_file=None,
        langflow_execution_mode="run_api",
        langflow_profile_flow_id="profile-flow",
        langflow_knowledge_flow_id="knowledge-flow",
        langflow_planning_flow_id="planning-flow",
    )

    assert webhook_settings.missing_executor_agents() == ["knowledge", "planning"]
    assert run_api_settings.missing_executor_agents() == []
