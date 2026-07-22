from __future__ import annotations

import pytest

from app.registry import KNOWLEDGE_AGENT, PLANNING_AGENT, PROFILE_AGENT
from app.schemas import A2ACommand
from app.validation import InputRequiredError, validate_command


def base_request(operation: str = "GENERATE_PLAN") -> dict:
    return {
        "schema_version": "1.0",
        "operation": operation,
        "request_id": "req",
        "run_id": "run",
        "correlation_id": "corr",
        "employee_id": "emp",
        "payload": {},
    }


def test_profile_requires_employee_id() -> None:
    request = base_request()
    request["employee_id"] = None
    command = A2ACommand(
        skill_id="get_employee_onboarding_profile",
        request=request,
    )
    with pytest.raises(InputRequiredError):
        validate_command(PROFILE_AGENT, command)


def test_profile_accepts_employee_id() -> None:
    command = A2ACommand(
        skill_id="assess_profile_completeness",
        request=base_request(),
    )
    validate_command(PROFILE_AGENT, command)


def test_knowledge_requires_search_context() -> None:
    command = A2ACommand(
        skill_id="search_onboarding_knowledge",
        request=base_request("ANSWER_QUESTION"),
    )
    with pytest.raises(InputRequiredError):
        validate_command(KNOWLEDGE_AGENT, command)


def test_knowledge_accepts_question() -> None:
    request = base_request("ANSWER_QUESTION")
    request["payload"] = {"question": "Which training is mandatory?"}
    command = A2ACommand(
        skill_id="answer_onboarding_question",
        request=request,
    )
    validate_command(KNOWLEDGE_AGENT, command)


def test_generate_requires_both_contexts() -> None:
    request = base_request()
    request["payload"] = {"profile_context": {"status": "SUCCEEDED"}}
    command = A2ACommand(skill_id="generate_onboarding_plan", request=request)
    with pytest.raises(InputRequiredError):
        validate_command(PLANNING_AGENT, command)


def test_generate_accepts_verified_context_payloads() -> None:
    request = base_request()
    request["payload"] = {
        "profile_context": {"status": "SUCCEEDED"},
        "knowledge_context": {"status": "SUCCEEDED"},
    }
    command = A2ACommand(skill_id="generate_onboarding_plan", request=request)
    validate_command(PLANNING_AGENT, command)


def test_revise_requires_plan_and_instructions() -> None:
    request = base_request("REVISE_PLAN")
    command = A2ACommand(skill_id="revise_onboarding_plan", request=request)
    with pytest.raises(InputRequiredError):
        validate_command(PLANNING_AGENT, command)


def test_adapt_requires_plan_and_trigger() -> None:
    request = base_request("ADAPT_PLAN")
    request["payload"] = {"current_plan": {"plan_id": "plan-1"}}
    command = A2ACommand(skill_id="adapt_onboarding_plan", request=request)
    with pytest.raises(InputRequiredError):
        validate_command(PLANNING_AGENT, command)


def test_explain_requires_plan_and_question() -> None:
    request = base_request("ANSWER_QUESTION")
    request["payload"] = {"current_plan": {"plan_id": "plan-1"}}
    command = A2ACommand(skill_id="explain_onboarding_plan", request=request)
    with pytest.raises(InputRequiredError):
        validate_command(PLANNING_AGENT, command)


def test_unsupported_skill_is_rejected() -> None:
    command = A2ACommand(skill_id="invent_plan", request=base_request())
    with pytest.raises(ValueError):
        validate_command(PLANNING_AGENT, command)
