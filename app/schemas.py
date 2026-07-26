from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Operation = Literal["GENERATE_PLAN", "REVISE_PLAN", "ANSWER_QUESTION", "ADAPT_PLAN"]
AgentKey = Literal["profile", "knowledge", "planning"]
DispatchMode = Literal["parallel", "series"]
ResultStatus = Literal["SUCCEEDED", "PARTIAL_SUCCESS", "FAILED"]
_JSON_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)
_JSON_FENCE_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)


class ErrorItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    message: str
    field: str | None = None
    retryable: bool = False


class WarningItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    message: str
    field: str | None = None


class OnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    operation: Operation
    request_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    employee_id: str | None = None
    onboarding_id: str | None = None
    case_id: str | None = None
    tenant_id: str | None = None
    idempotency_key: str | None = None
    locale: str = "en"
    requested_by: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2ACommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1)
    request: OnboardingRequest


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    status: ResultStatus
    artifact_type: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str | WarningItem] = Field(default_factory=list)
    errors: list[ErrorItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def result_consistency(self) -> AgentResult:
        if self.status == "FAILED" and not self.errors:
            raise ValueError("FAILED results must include at least one error")
        return self


class ExecutorWebhookCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    result: AgentResult

    @model_validator(mode="before")
    @classmethod
    def normalize_langflow_callback(cls, value: Any) -> Any:
        """Accept common Langflow wrappers around the callback payload."""
        payload = _parse_wrapped_json(value)
        if not isinstance(payload, dict):
            return value

        if _looks_like_callback(payload):
            return payload

        for wrapper_key in ("text", "result", "output", "data"):
            nested = _parse_wrapped_json(payload.get(wrapper_key))
            if _looks_like_callback(nested):
                return nested

        return payload


class DispatchCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: AgentKey
    skill_id: str = Field(min_length=1)
    request: OnboardingRequest


class DispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: DispatchMode = "parallel"
    calls: list[DispatchCall] = Field(min_length=1, max_length=10)


class DispatchResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    agent: AgentKey
    skill_id: str
    status: str
    task_id: str | None = None
    context_id: str | None = None
    artifact: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class DispatchResponse(BaseModel):
    mode: DispatchMode
    results: list[DispatchResult]


def _parse_wrapped_json(value: Any) -> Any:
    if isinstance(value, str):
        parsed = _parse_json_text(value)
        return parsed if parsed is not None else value
    return value


def _looks_like_callback(value: Any) -> bool:
    return isinstance(value, dict) and {
        "request_id",
        "run_id",
        "correlation_id",
        "result",
    }.issubset(value)


def _parse_json_text(text: str) -> Any:
    candidate = text.strip()
    for json_candidate in _json_candidates(candidate):
        try:
            return json.loads(json_candidate)
        except json.JSONDecodeError:
            continue
    return None


def _json_candidates(text: str) -> list[str]:
    candidates = [text]

    full_fence = _JSON_FENCE.match(text)
    if full_fence:
        candidates.append(full_fence.group(1).strip())

    candidates.extend(match.group(1).strip() for match in _JSON_FENCE_BLOCK.finditer(text))

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            _, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        candidates.append(text[index : index + end])

    return list(dict.fromkeys(candidates))
