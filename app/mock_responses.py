from __future__ import annotations

from typing import Any

from app.registry import AGENTS
from app.schemas import A2ACommand, AgentResult


def build_mock_agent_result(agent_key: str, command_data: dict[str, Any]) -> AgentResult:
    """Return placeholder AgentResult data until Langflow workflows are ready."""
    command = A2ACommand.model_validate(command_data)
    spec = AGENTS[agent_key]

    data_builders = {
        "profile": _profile_data,
        "knowledge": _knowledge_data,
        "planning": _planning_data,
    }
    data = data_builders[agent_key](command)

    return AgentResult(
        schema_version="1.0",
        status="SUCCEEDED",
        artifact_type=spec.artifact_type,
        data=data,
        warnings=[
            "Mock placeholder response. Replace with the Langflow workflow output when ready."
        ],
        errors=[],
        metadata={
            "mock": True,
            "agent": agent_key,
            "skill_id": command.skill_id,
            "request_id": command.request.request_id,
            "run_id": command.request.run_id,
            "correlation_id": command.request.correlation_id,
        },
    )


def _profile_data(command: A2ACommand) -> dict[str, Any]:
    request = command.request
    payload = request.payload
    role = payload.get("role") or payload.get("job_title") or "Software Engineer"
    department = payload.get("department") or "Engineering"
    location = payload.get("location") or "Remote"

    return {
        "employee": {
            "employee_id": request.employee_id,
            "name": payload.get("employee_name", "Mock Employee"),
            "role": role,
            "department": department,
            "location": location,
            "start_date": payload.get("start_date"),
            "manager": payload.get("manager", {"name": "Mock Manager"}),
        },
        "organization": {
            "tenant_id": request.tenant_id,
            "business_unit": payload.get("business_unit", department),
            "work_mode": payload.get("work_mode", "hybrid"),
        },
        "skills": [
            {"name": "Python", "level": "intermediate", "source": "mock_profile"},
            {"name": "Product onboarding", "level": "beginner", "source": "mock_profile"},
        ],
        "experience": [
            {
                "summary": "Placeholder experience record for workflow integration testing.",
                "years": payload.get("years_of_experience"),
            }
        ],
        "profile_completeness": {
            "ready_for_planning": True,
            "score": 0.82,
            "missing_fields": [],
        },
        "onboarding_constraints": [
            {
                "type": "availability",
                "description": "Use manager-confirmed availability once Profile workflow is live.",
                "severity": "low",
            }
        ],
        "source_references": [
            {
                "system": "mock_profile_agent",
                "record_id": request.employee_id,
                "confidence": 0.8,
            }
        ],
    }


def _knowledge_data(command: A2ACommand) -> dict[str, Any]:
    request = command.request
    payload = request.payload
    role = payload.get("role") or "new hire"
    department = payload.get("department") or "assigned department"
    question = payload.get("question") or payload.get("query")

    return {
        "answer": (
            "Mock policy answer for integration testing."
            if question
            else None
        ),
        "requirements": [
            {
                "id": "REQ-MOCK-001",
                "title": "Complete HR orientation",
                "mandatory": True,
                "applies_to": role,
                "due": "week_1",
            },
            {
                "id": "REQ-MOCK-002",
                "title": "Review department onboarding guide",
                "mandatory": True,
                "applies_to": department,
                "due": "week_1",
            },
        ],
        "mandatory_training": [
            {
                "id": "TRAIN-MOCK-SEC",
                "title": "Security and access basics",
                "due": "day_3",
                "owner": "IT",
            }
        ],
        "procedures": [
            {
                "id": "PROC-MOCK-ACCESS",
                "title": "Request required tools and system access",
                "owner": "IT Service Desk",
            }
        ],
        "tools": [
            {"name": "HR portal", "purpose": "Employee paperwork"},
            {"name": "Collaboration suite", "purpose": "Team communication"},
        ],
        "contacts": [
            {"role": "Manager", "name": "Mock Manager"},
            {"role": "HR onboarding partner", "name": "Mock HR Partner"},
        ],
        "sources": [
            {
                "title": "Mock Onboarding Handbook",
                "section": "Getting Started",
                "url": None,
            }
        ],
        "unresolved_questions": [],
        "confidence": 0.75,
    }


def _planning_data(command: A2ACommand) -> dict[str, Any]:
    if command.skill_id == "revise_onboarding_plan":
        return _revision_plan_data(command)
    if command.skill_id == "adapt_onboarding_plan":
        return _adapted_plan_data(command)
    if command.skill_id == "explain_onboarding_plan":
        return _plan_explanation_data(command)
    return _generated_plan_data(command)


def _generated_plan_data(command: A2ACommand) -> dict[str, Any]:
    request = command.request
    payload = request.payload
    profile = _artifact_data(payload.get("profile_context"))
    knowledge = _artifact_data(payload.get("knowledge_context"))
    employee = profile.get("employee", {})
    role = employee.get("role") or payload.get("role") or "new hire"

    return {
        "plan_id": request.onboarding_id or "plan-mock-001",
        "version": 1,
        "title": f"Mock 30-day onboarding plan for {role}",
        "status": "draft",
        "start_date": employee.get("start_date") or payload.get("start_date"),
        "duration_days": payload.get("duration_days", 30),
        "phases": [
            {"id": "phase-1", "title": "Launch", "days": "1-5"},
            {"id": "phase-2", "title": "Ramp", "days": "6-20"},
            {"id": "phase-3", "title": "Contribute", "days": "21-30"},
        ],
        "milestones": [
            {"id": "ms-1", "title": "Access ready", "target_day": 3},
            {"id": "ms-2", "title": "First manager review", "target_day": 10},
            {"id": "ms-3", "title": "First meaningful contribution", "target_day": 30},
        ],
        "tasks": [
            {
                "id": "task-mock-001",
                "title": "Complete HR orientation",
                "owner": "employee",
                "due_day": 1,
                "source_requirement": "REQ-MOCK-001",
            },
            {
                "id": "task-mock-002",
                "title": "Finish security and access basics",
                "owner": "employee",
                "due_day": 3,
                "source_requirement": "TRAIN-MOCK-SEC",
            },
            {
                "id": "task-mock-003",
                "title": "Meet manager and confirm first-week outcomes",
                "owner": "manager",
                "due_day": 5,
            },
        ],
        "sources": knowledge.get("sources", []),
        "assumptions": [
            "Mock response assumes a standard 30-day onboarding timeline.",
            "Replace mock task sequencing with Planning workflow output when ready.",
        ],
    }


def _revision_plan_data(command: A2ACommand) -> dict[str, Any]:
    request = command.request
    payload = request.payload
    current_plan = _object(payload.get("current_plan"))
    requested_changes = (
        payload.get("requested_changes")
        or payload.get("feedback")
        or payload.get("revision_reason")
        or "Mock requested change"
    )
    tasks = list(current_plan.get("tasks", []))
    tasks.append(
        {
            "id": "task-mock-revision",
            "title": "Review requested onboarding plan change",
            "owner": "manager",
            "due_day": 7,
        }
    )

    return {
        "plan_id": current_plan.get("plan_id") or request.onboarding_id or "plan-mock-001",
        "version": _next_version(current_plan),
        "title": current_plan.get("title", "Mock revised onboarding plan"),
        "status": "revised",
        "duration_days": current_plan.get("duration_days", 30),
        "phases": current_plan.get("phases", []),
        "milestones": current_plan.get("milestones", []),
        "tasks": tasks,
        "change_summary": {
            "summary": "Mock revision applied for integration testing.",
            "requested_changes": requested_changes,
            "preserved_completed_tasks": True,
        },
        "sources": current_plan.get("sources", []),
        "assumptions": ["Planning workflow is not live; this is a placeholder revision."],
    }


def _adapted_plan_data(command: A2ACommand) -> dict[str, Any]:
    request = command.request
    payload = request.payload
    current_plan = _object(payload.get("current_plan"))
    trigger = (
        payload.get("adaptation_trigger")
        or payload.get("progress")
        or payload.get("blockers")
        or payload.get("changes")
        or "Mock adaptation trigger"
    )

    return {
        "plan_id": current_plan.get("plan_id") or request.onboarding_id or "plan-mock-001",
        "version": _next_version(current_plan),
        "title": current_plan.get("title", "Mock adapted onboarding plan"),
        "status": "adapted",
        "duration_days": current_plan.get("duration_days", 30),
        "phases": current_plan.get("phases", []),
        "milestones": current_plan.get("milestones", []),
        "tasks": current_plan.get("tasks", []),
        "adaptation_summary": {
            "summary": "Mock adaptation generated for integration testing.",
            "trigger": trigger,
            "changed_future_tasks_only": True,
        },
        "sources": current_plan.get("sources", []),
        "assumptions": ["Planning workflow is not live; this is a placeholder adaptation."],
    }


def _plan_explanation_data(command: A2ACommand) -> dict[str, Any]:
    payload = command.request.payload
    current_plan = _object(payload.get("current_plan") or payload.get("plan"))
    question = payload.get("question", "Why is this plan structured this way?")

    return {
        "plan_id": current_plan.get("plan_id") or "plan-mock-001",
        "version": _version(current_plan),
        "title": current_plan.get("title", "Mock onboarding plan explanation"),
        "status": current_plan.get("status", "explained"),
        "duration_days": current_plan.get("duration_days"),
        "phases": current_plan.get("phases", []),
        "milestones": current_plan.get("milestones", []),
        "tasks": current_plan.get("tasks", []),
        "change_summary": {
            "question": question,
            "answer": (
                "Mock explanation: this placeholder keeps the A2A response shape stable "
                "until the Planning workflow can provide grounded reasoning."
            ),
        },
        "sources": current_plan.get("sources", []),
        "assumptions": ["Explanation is placeholder text from the mock Planning agent."],
    }


def _artifact_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    artifact_data = value.get("data")
    return artifact_data if isinstance(artifact_data, dict) else value


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _version(plan: dict[str, Any]) -> int:
    try:
        return int(plan.get("version") or 1)
    except (TypeError, ValueError):
        return 1


def _next_version(plan: dict[str, Any]) -> int:
    return _version(plan) + 1
