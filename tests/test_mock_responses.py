from __future__ import annotations

import pytest

from app.mock_responses import build_mock_agent_result


def base_command(skill_id: str, payload: dict | None = None) -> dict:
    return {
        "skill_id": skill_id,
        "request": {
            "schema_version": "1.0",
            "operation": "GENERATE_PLAN",
            "request_id": "req-mock",
            "run_id": "run-mock",
            "correlation_id": "corr-mock",
            "employee_id": "emp-mock",
            "onboarding_id": "onb-mock",
            "tenant_id": "tenant-mock",
            "payload": payload or {},
        },
    }


@pytest.mark.parametrize(
    ("agent_key", "skill_id", "artifact_type"),
    [
        (
            "profile",
            "get_employee_onboarding_profile",
            "EMPLOYEE_PROFILE_CONTEXT",
        ),
        (
            "knowledge",
            "search_onboarding_knowledge",
            "ONBOARDING_KNOWLEDGE_EVIDENCE",
        ),
        (
            "planning",
            "generate_onboarding_plan",
            "ONBOARDING_PLAN",
        ),
    ],
)
def test_mock_agent_result_shapes(
    agent_key: str,
    skill_id: str,
    artifact_type: str,
) -> None:
    payload = {
        "role": "Frontend Engineer",
        "department": "Engineering",
        "profile_context": {"data": {"employee": {"role": "Frontend Engineer"}}},
        "knowledge_context": {"data": {"sources": [{"title": "Mock Handbook"}]}},
    }

    result = build_mock_agent_result(agent_key, base_command(skill_id, payload))

    assert result.status == "SUCCEEDED"
    assert result.artifact_type == artifact_type
    assert result.metadata["mock"] is True
    assert result.metadata["agent"] == agent_key
    assert result.data


def test_planning_mock_revision_increments_version() -> None:
    command = base_command(
        "revise_onboarding_plan",
        {
            "current_plan": {
                "plan_id": "plan-1",
                "version": 2,
                "tasks": [{"id": "task-1", "title": "Existing task"}],
            },
            "requested_changes": "Add product training.",
        },
    )
    command["request"]["operation"] = "REVISE_PLAN"

    result = build_mock_agent_result("planning", command)

    assert result.data["plan_id"] == "plan-1"
    assert result.data["version"] == 3
    assert result.data["change_summary"]["preserved_completed_tasks"] is True
