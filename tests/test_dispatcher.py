from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.dispatcher import A2ADispatcher
from app.schemas import DispatchCall, DispatchRequest, DispatchResult


class SlowA2AClient:
    def __init__(self, *, delay: float = 0.05) -> None:
        self.delay = delay
        self.invoke_count = 0

    async def invoke(self, call: DispatchCall) -> DispatchResult:
        self.invoke_count += 1
        await asyncio.sleep(self.delay)
        return DispatchResult(
            agent=call.agent,
            skill_id=call.skill_id,
            status="TASK_STATE_COMPLETED",
            context_id=call.request.correlation_id,
            artifact={"ok": True},
        )


def dispatch_request() -> DispatchRequest:
    return DispatchRequest.model_validate(
        {
            "mode": "series",
            "calls": [
                {
                    "agent": "planning",
                    "skill_id": "generate_onboarding_plan",
                    "request": {
                        "operation": "GENERATE_PLAN",
                        "request_id": "req-123",
                        "run_id": "run-123",
                        "correlation_id": "case-123:req-123",
                        "payload": {},
                    },
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_dispatcher_returns_in_progress_before_mcp_timeout() -> None:
    client = SlowA2AClient(delay=0.05)
    settings = Settings(
        _env_file=None,
        dispatch_wait_seconds=0.001,
        dispatch_result_ttl_seconds=60,
    )
    dispatcher = A2ADispatcher(client, settings)

    try:
        response = await dispatcher.dispatch(dispatch_request())
    finally:
        await dispatcher.close()

    result = response.results[0]
    assert result.status == "TASK_STATE_WORKING"
    assert result.error is not None
    assert result.error["code"] == "DISPATCH_IN_PROGRESS"
    assert result.error["retryable"] is True


@pytest.mark.asyncio
async def test_dispatcher_reuses_background_job_for_identical_retry() -> None:
    client = SlowA2AClient(delay=0.01)
    settings = Settings(
        _env_file=None,
        dispatch_wait_seconds=0.001,
        dispatch_result_ttl_seconds=60,
    )
    dispatcher = A2ADispatcher(client, settings)
    request = dispatch_request()

    try:
        first = await dispatcher.dispatch(request)
        await asyncio.sleep(0.03)
        second = await dispatcher.dispatch(request)
    finally:
        await dispatcher.close()

    assert first.results[0].status == "TASK_STATE_WORKING"
    assert second.results[0].status == "TASK_STATE_COMPLETED"
    assert second.results[0].artifact == {"ok": True}
    assert client.invoke_count == 1
