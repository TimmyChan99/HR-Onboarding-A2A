from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Operation = Literal["GENERATE_PLAN", "REVISE_PLAN", "ANSWER_QUESTION", "ADAPT_PLAN"]
AgentKey = Literal["profile", "knowledge", "planning"]
DispatchMode = Literal["parallel", "series"]
ResultStatus = Literal["SUCCEEDED", "PARTIAL_SUCCESS", "FAILED"]


class ErrorItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    message: str
    field: str | None = None
    retryable: bool = False


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
    warnings: list[str] = Field(default_factory=list)
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
