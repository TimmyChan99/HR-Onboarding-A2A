from __future__ import annotations

from typing import Any

from app.registry import AgentSpec
from app.schemas import A2ACommand


class InputRequiredError(ValueError):
    def __init__(self, message: str, *, fields: list[str] | None = None) -> None:
        super().__init__(message)
        self.fields = fields or []


def _has_any(payload: dict[str, Any], keys: set[str]) -> bool:
    return any(key in payload and payload[key] not in (None, "", [], {}) for key in keys)


def validate_command(agent: AgentSpec, command: A2ACommand) -> None:
    if command.skill_id not in agent.skill_ids:
        raise ValueError(
            f"Skill '{command.skill_id}' is not advertised by the {agent.key} agent"
        )

    req = command.request
    payload = req.payload

    if agent.key == "profile":
        if not req.employee_id:
            raise InputRequiredError(
                "employee_id is required by the Profile Agent",
                fields=["employee_id"],
            )
        return

    if agent.key == "knowledge":
        if not _has_any(
            payload,
            {
                "question",
                "query",
                "role",
                "department",
                "location",
                "employment_type",
                "onboarding_scope",
                "topics",
            },
        ):
            raise InputRequiredError(
                "The Knowledge Agent needs a question, query, role, department, or onboarding scope",
                fields=["payload.question|query|role|department|onboarding_scope"],
            )
        return

    if agent.key == "planning":
        if command.skill_id == "generate_onboarding_plan":
            missing = [
                field
                for field in ("profile_context", "knowledge_context")
                if not payload.get(field)
            ]
            if missing:
                raise InputRequiredError(
                    "Plan generation requires verified profile and knowledge context",
                    fields=[f"payload.{field}" for field in missing],
                )
        elif command.skill_id == "revise_onboarding_plan":
            missing = []
            if not payload.get("current_plan"):
                missing.append("payload.current_plan")
            if not _has_any(payload, {"requested_changes", "feedback", "revision_reason"}):
                missing.append("payload.requested_changes|feedback|revision_reason")
            if missing:
                raise InputRequiredError(
                    "Plan revision requires the current plan and explicit revision instructions",
                    fields=missing,
                )
        elif command.skill_id == "adapt_onboarding_plan":
            missing = []
            if not payload.get("current_plan"):
                missing.append("payload.current_plan")
            if not _has_any(payload, {"progress", "adaptation_trigger", "blockers", "changes"}):
                missing.append("payload.progress|adaptation_trigger|blockers|changes")
            if missing:
                raise InputRequiredError(
                    "Plan adaptation requires the current plan and a progress or change trigger",
                    fields=missing,
                )
        elif command.skill_id == "explain_onboarding_plan":
            missing = []
            if not _has_any(payload, {"current_plan", "plan"}):
                missing.append("payload.current_plan|plan")
            if not payload.get("question"):
                missing.append("payload.question")
            if missing:
                raise InputRequiredError(
                    "Plan explanation requires a plan and a question",
                    fields=missing,
                )
