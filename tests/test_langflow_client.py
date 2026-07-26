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


def knowledge_command() -> dict[str, object]:
    return {
        "skill_id": "answer_onboarding_question",
        "request": {
            "operation": "ANSWER_QUESTION",
            "request_id": "req-knowledge",
            "run_id": "run-knowledge",
            "correlation_id": "case-knowledge:req-knowledge",
            "employee_id": "employee-123",
            "payload": {"question": "Can employees work remotely?"},
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


def knowledge_result() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "SUCCEEDED",
        "artifact_type": "ONBOARDING_KNOWLEDGE_EVIDENCE",
        "data": {"direct_answer": "Remote work is supported by policy evidence."},
        "warnings": [],
        "errors": [],
        "metadata": {},
    }


def profile_result_with_structured_warning() -> dict[str, object]:
    result = profile_result()
    result["warnings"] = [
        {
            "code": "PROFILE_DATA_PARTIAL",
            "message": "Some profile data is missing.",
            "field": None,
        }
    ]
    return result


def callback_payload() -> dict[str, object]:
    return {
        "request_id": "req-123",
        "run_id": "run-123",
        "correlation_id": "case-123:req-123",
        "result": profile_result_with_structured_warning(),
    }


def test_executor_callback_accepts_text_object_wrapper() -> None:
    callback = ExecutorWebhookCallback.model_validate({"text": callback_payload()})

    assert callback.request_id == "req-123"
    assert callback.result.artifact_type == "EMPLOYEE_PROFILE_CONTEXT"


def test_executor_callback_accepts_text_json_string_wrapper() -> None:
    callback = ExecutorWebhookCallback.model_validate(
        {"text": json.dumps(callback_payload())}
    )

    assert callback.run_id == "run-123"
    assert callback.result.data["employee"]["employee_id"] == "employee-123"


def test_executor_callback_accepts_fenced_text_json_wrapper() -> None:
    callback = ExecutorWebhookCallback.model_validate(
        {"text": f"```json\n{json.dumps(callback_payload())}\n```"}
    )

    assert callback.correlation_id == "case-123:req-123"


def test_executor_callback_extracts_json_fence_from_planning_prose() -> None:
    callback = ExecutorWebhookCallback.model_validate(
        {
            "text": (
                "Now I have all the context. Let me finalize the plan.\n\n"
                "```json\n"
                f"{json.dumps(callback_payload())}\n"
                "```\n"
                "Done."
            )
        }
    )

    assert callback.request_id == "req-123"
    assert callback.result.status == "SUCCEEDED"


def test_langflow_result_extraction_accepts_text_callback_wrapper() -> None:
    extracted = LangflowClient._extract_result(
        {"text": json.dumps(callback_payload())}
    )

    assert extracted["artifact_type"] == "EMPLOYEE_PROFILE_CONTEXT"


def test_langflow_result_extraction_accepts_prose_with_fenced_json() -> None:
    extracted = LangflowClient._extract_result(
        {
            "text": (
                "Template selection complete.\n\n"
                "```json\n"
                f"{json.dumps(callback_payload())}\n"
                "```"
            )
        }
    )

    assert extracted["artifact_type"] == "EMPLOYEE_PROFILE_CONTEXT"


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
async def test_knowledge_langflow_mode_uses_webhook_even_when_global_mode_is_run_api() -> None:
    webhook_url = "https://stg-agentic.example/api/v1/webhook/knowledge-webhook"
    route = respx.post(webhook_url).mock(
        return_value=httpx.Response(200, json=knowledge_result())
    )
    settings = Settings(
        _env_file=None,
        langflow_execution_mode="run_api",
        knowledge_agent_mode="langflow",
        langflow_knowledge_flow_id="knowledge-flow",
        langflow_knowledge_webhook_url=webhook_url,
    )
    client = LangflowClient(settings)

    try:
        result = await client.run_agent(
            agent_key="knowledge",
            command=knowledge_command(),
            session_id="case-knowledge:req-knowledge",
            expected_artifact_type="ONBOARDING_KNOWLEDGE_EVIDENCE",
        )
    finally:
        await client.close()

    assert route.called
    assert result.data["direct_answer"] == "Remote work is supported by policy evidence."


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
async def test_webhook_mode_returns_failed_result_on_executor_422() -> None:
    webhook_url = "https://stg-agentic.example/api/v1/webhook/profile-webhook"
    respx.post(webhook_url).mock(
        return_value=httpx.Response(
            422,
            json={"detail": "Executor payload was invalid"},
        )
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

    assert result.status == "FAILED"
    assert result.artifact_type == "EMPLOYEE_PROFILE_CONTEXT"
    assert result.errors[0].code == "HTTP_422"


@pytest.mark.asyncio
@respx.mock
async def test_webhook_mode_joins_exact_duplicate_in_flight_request() -> None:
    webhook_url = "https://stg-agentic.example/api/v1/webhook/profile-webhook"
    triggered = asyncio.Event()

    async def webhook_trigger(_: httpx.Request) -> httpx.Response:
        triggered.set()
        return httpx.Response(202, json={"status": "in progress"})

    route = respx.post(webhook_url).mock(side_effect=webhook_trigger)
    settings = Settings(
        _env_file=None,
        langflow_execution_mode="webhook",
        langflow_profile_webhook_url=webhook_url,
    )
    client = LangflowClient(settings)

    try:
        first = asyncio.create_task(
            client.run_agent(
                agent_key="profile",
                command=command(),
                session_id="case-123:req-123",
                expected_artifact_type="EMPLOYEE_PROFILE_CONTEXT",
            )
        )
        await asyncio.wait_for(triggered.wait(), timeout=1)
        second = asyncio.create_task(
            client.run_agent(
                agent_key="profile",
                command=command(),
                session_id="case-123:req-123",
                expected_artifact_type="EMPLOYEE_PROFILE_CONTEXT",
            )
        )
        accepted = await client.complete_webhook_callback(
            "profile",
            ExecutorWebhookCallback(
                request_id="req-123",
                run_id="run-123",
                correlation_id="case-123:req-123",
                result=profile_result(),
            ),
        )
        first_result, second_result = await asyncio.gather(first, second)
    finally:
        await client.close()

    assert accepted is True
    assert route.call_count == 1
    assert first_result.data == second_result.data


@pytest.mark.asyncio
@respx.mock
async def test_webhook_mode_rejects_same_callback_ids_with_different_payload() -> None:
    webhook_url = "https://stg-agentic.example/api/v1/webhook/profile-webhook"
    triggered = asyncio.Event()

    async def webhook_trigger(_: httpx.Request) -> httpx.Response:
        triggered.set()
        return httpx.Response(202, json={"status": "in progress"})

    respx.post(webhook_url).mock(side_effect=webhook_trigger)
    settings = Settings(
        _env_file=None,
        langflow_execution_mode="webhook",
        langflow_profile_webhook_url=webhook_url,
        langflow_timeout_seconds=1,
    )
    client = LangflowClient(settings)
    conflicting_command = command()
    conflicting_command["request"]["payload"] = {"changed": True}  # type: ignore[index]

    try:
        first = asyncio.create_task(
            client.run_agent(
                agent_key="profile",
                command=command(),
                session_id="case-123:req-123",
                expected_artifact_type="EMPLOYEE_PROFILE_CONTEXT",
            )
        )
        await asyncio.wait_for(triggered.wait(), timeout=1)
        with pytest.raises(Exception, match="different payload"):
            await client.run_agent(
                agent_key="profile",
                command=conflicting_command,
                session_id="case-123:req-123",
                expected_artifact_type="EMPLOYEE_PROFILE_CONTEXT",
            )
        await client.complete_webhook_callback(
            "profile",
            ExecutorWebhookCallback(
                request_id="req-123",
                run_id="run-123",
                correlation_id="case-123:req-123",
                result=profile_result(),
            ),
        )
        await first
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_webhook_callback_accepts_structured_warnings() -> None:
    webhook_url = "https://stg-agentic.example/api/v1/webhook/profile-webhook"
    triggered = asyncio.Event()

    async def webhook_trigger(_: httpx.Request) -> httpx.Response:
        triggered.set()
        return httpx.Response(202, json={"status": "in progress"})

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
                result=profile_result_with_structured_warning(),
            ),
        )
        result = await waiting_result
    finally:
        await client.close()

    assert accepted is True
    assert result.warnings[0].code == "PROFILE_DATA_PARTIAL"


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
        langflow_knowledge_webhook_url="https://stg-agentic.example/knowledge",
        langflow_planning_flow_id="planning-flow",
    )

    assert webhook_settings.missing_executor_agents() == ["knowledge", "planning"]
    assert run_api_settings.missing_executor_agents() == []
