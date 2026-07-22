from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class TestResult:
    name: str
    passed: bool
    duration_ms: int
    detail: str = ""


class API:
    def __init__(self, base_url: str, api_key: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        key: str | None = None,
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        supplied_key = self.api_key if key is None else key
        if supplied_key:
            headers["X-A2A-API-Key"] = supplied_key
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode()
                return response.status, json.loads(raw) if raw else None
        except HTTPError as exc:
            raw = exc.read().decode()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw
            return exc.code, payload
        except URLError as exc:
            raise RuntimeError(f"Cannot reach {self.base_url}: {exc}") from exc


def onboarding_request(operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10]
    return {
        "schema_version": "1.0",
        "operation": operation,
        "request_id": f"req-{suffix}",
        "run_id": f"run-{suffix}",
        "correlation_id": f"corr-{suffix}",
        "employee_id": "emp-test-123",
        "onboarding_id": "onb-test-123",
        "idempotency_key": f"test:{operation.lower()}:{suffix}",
        "payload": payload or {},
        "metadata": {},
    }


def dispatch(agent: str, skill: str, request: dict[str, Any], mode: str = "series") -> dict[str, Any]:
    return {"mode": mode, "calls": [{"agent": agent, "skill_id": skill, "request": request}]}


def result_state(payload: Any, index: int = 0) -> str | None:
    try:
        return payload["results"][index]["status"]
    except (KeyError, IndexError, TypeError):
        return None


def artifact_type(payload: Any, index: int = 0) -> str | None:
    try:
        artifact = payload["results"][index]["artifact"]
        return artifact.get("artifact_type")
    except (AttributeError, KeyError, IndexError, TypeError):
        return None


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_case(name: str, function: Callable[[], None]) -> TestResult:
    started = time.perf_counter()
    try:
        function()
        return TestResult(name=name, passed=True, duration_ms=int((time.perf_counter() - started) * 1000))
    except Exception as exc:  # noqa: BLE001
        return TestResult(
            name=name,
            passed=False,
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail=str(exc),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="A2A onboarding integration test matrix")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--api-key", default="local-a2a-test-secret")
    parser.add_argument("--timeout", type=float, default=40)
    parser.add_argument("--report", default="test-report.json")
    parser.add_argument(
        "--include-mock-failures",
        action="store_true",
        help="Run failure simulations supported by scripts/mock_langflow.py.",
    )
    args = parser.parse_args()
    api = API(args.base_url, args.api_key, args.timeout)
    cases: list[tuple[str, Callable[[], None]]] = []

    def add(name: str, function: Callable[[], None]) -> None:
        cases.append((name, function))

    def health() -> None:
        status, body = api.call("GET", "/healthz", key="")
        expect(status == 200 and body.get("status") == "ok", f"health response: {status} {body}")

    def ready() -> None:
        status, body = api.call("GET", "/readyz", key="")
        expect(status == 200, f"ready response: {status} {body}")
        expect(body.get("status") == "ready", f"server not ready: {body}")

    def cards() -> None:
        expected = {
            "profile": "get_employee_onboarding_profile",
            "knowledge": "search_onboarding_knowledge",
            "planning": "generate_onboarding_plan",
        }
        for agent, skill in expected.items():
            status, body = api.call("GET", f"/agents/{agent}/.well-known/agent-card.json", key="")
            expect(status == 200, f"{agent} card: {status} {body}")
            skill_ids = {item.get("id") for item in body.get("skills", [])}
            expect(skill in skill_ids, f"{agent} card missing {skill}: {skill_ids}")

    def auth_missing() -> None:
        status, _ = api.call("GET", "/orchestrator/agents", key="")
        expect(status == 401, f"expected 401, got {status}")

    def auth_bad() -> None:
        status, _ = api.call("GET", "/orchestrator/agents", key="wrong-key")
        expect(status == 401, f"expected 401, got {status}")

    def profile_success() -> None:
        req = onboarding_request("GENERATE_PLAN", {"role": "Frontend Developer", "department": "Engineering"})
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("profile", "get_employee_onboarding_profile", req))
        expect(status == 200, f"HTTP {status}: {body}")
        expect(result_state(body) == "TASK_STATE_COMPLETED", f"unexpected state: {body}")
        expect(artifact_type(body) == "EMPLOYEE_PROFILE_CONTEXT", f"wrong artifact: {body}")

    def knowledge_success() -> None:
        req = onboarding_request("ANSWER_QUESTION", {"question": "Which security training is mandatory?"})
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("knowledge", "answer_onboarding_question", req))
        expect(status == 200, f"HTTP {status}: {body}")
        expect(result_state(body) == "TASK_STATE_COMPLETED", f"unexpected state: {body}")
        expect(artifact_type(body) == "ONBOARDING_KNOWLEDGE_EVIDENCE", f"wrong artifact: {body}")

    def planning_generate_success() -> None:
        req = onboarding_request(
            "GENERATE_PLAN",
            {
                "profile_context": {"status": "SUCCEEDED", "data": {"employee": {"role": "Frontend Developer"}}},
                "knowledge_context": {"status": "SUCCEEDED", "data": {"requirements": []}},
                "duration_days": 30,
            },
        )
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("planning", "generate_onboarding_plan", req))
        expect(status == 200, f"HTTP {status}: {body}")
        expect(result_state(body) == "TASK_STATE_COMPLETED", f"unexpected state: {body}")
        expect(artifact_type(body) == "ONBOARDING_PLAN", f"wrong artifact: {body}")

    def generate_parallel_stage1() -> None:
        common = onboarding_request("GENERATE_PLAN")
        profile_req = dict(common)
        profile_req["payload"] = {"role": "Frontend Developer", "department": "Engineering"}
        knowledge_req = dict(common)
        knowledge_req["payload"] = {
            "role": "Frontend Developer",
            "department": "Engineering",
            "onboarding_scope": "30_DAY_PLAN",
        }
        body = {
            "mode": "parallel",
            "calls": [
                {"agent": "profile", "skill_id": "get_employee_onboarding_profile", "request": profile_req},
                {"agent": "knowledge", "skill_id": "get_role_onboarding_requirements", "request": knowledge_req},
            ],
        }
        status, response = api.call("POST", "/orchestrator/dispatch", body)
        expect(status == 200, f"HTTP {status}: {response}")
        states = [item.get("status") for item in response.get("results", [])]
        expect(states == ["TASK_STATE_COMPLETED", "TASK_STATE_COMPLETED"], f"unexpected states: {response}")
        contexts = {item.get("context_id") for item in response.get("results", [])}
        expect(contexts == {common["correlation_id"]}, f"correlation not preserved: {response}")

    def question_parallel() -> None:
        common = onboarding_request("ANSWER_QUESTION")
        p1 = dict(common)
        p1["payload"] = {"question": "Which training applies to this employee?"}
        p2 = dict(common)
        p2["payload"] = {"question": "Which training is mandatory?", "role": "Frontend Developer"}
        status, body = api.call(
            "POST",
            "/orchestrator/dispatch",
            {
                "mode": "parallel",
                "calls": [
                    {"agent": "profile", "skill_id": "get_employee_onboarding_profile", "request": p1},
                    {"agent": "knowledge", "skill_id": "answer_onboarding_question", "request": p2},
                ],
            },
        )
        expect(status == 200, f"HTTP {status}: {body}")
        expect(all(item.get("status") == "TASK_STATE_COMPLETED" for item in body.get("results", [])), f"unexpected: {body}")

    def revise_success() -> None:
        req = onboarding_request(
            "REVISE_PLAN",
            {
                "current_plan": {"plan_id": "plan-1", "version": 1, "tasks": []},
                "requested_changes": ["Add product architecture training"],
            },
        )
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("planning", "revise_onboarding_plan", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_COMPLETED", f"unexpected: {status} {body}")

    def adapt_success() -> None:
        req = onboarding_request(
            "ADAPT_PLAN",
            {
                "current_plan": {"plan_id": "plan-1", "version": 1, "tasks": []},
                "progress": {"progress_percentage": 20, "blocked_task_ids": ["task-1"]},
                "adaptation_trigger": "Laptop delivery delayed",
            },
        )
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("planning", "adapt_onboarding_plan", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_COMPLETED", f"unexpected: {status} {body}")

    def explain_success() -> None:
        req = onboarding_request(
            "ANSWER_QUESTION",
            {
                "current_plan": {"plan_id": "plan-1", "version": 1, "tasks": []},
                "question": "Why is security training before repository access?",
            },
        )
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("planning", "explain_onboarding_plan", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_COMPLETED", f"unexpected: {status} {body}")

    def unsupported_skill() -> None:
        req = onboarding_request("GENERATE_PLAN", {"role": "Frontend Developer"})
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("profile", "invent_profile", req))
        expect(status == 200, f"HTTP {status}: {body}")
        expect(result_state(body) == "TASK_STATE_REJECTED", f"unexpected state: {body}")

    def profile_input_required() -> None:
        req = onboarding_request("GENERATE_PLAN", {"role": "Frontend Developer"})
        req["employee_id"] = None
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("profile", "get_employee_onboarding_profile", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_INPUT_REQUIRED", f"unexpected: {status} {body}")

    def knowledge_input_required() -> None:
        req = onboarding_request("ANSWER_QUESTION", {})
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("knowledge", "answer_onboarding_question", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_INPUT_REQUIRED", f"unexpected: {status} {body}")

    def planning_generate_input_required() -> None:
        req = onboarding_request("GENERATE_PLAN", {"profile_context": {"status": "SUCCEEDED"}})
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("planning", "generate_onboarding_plan", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_INPUT_REQUIRED", f"unexpected: {status} {body}")

    def revise_input_required() -> None:
        req = onboarding_request("REVISE_PLAN", {})
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("planning", "revise_onboarding_plan", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_INPUT_REQUIRED", f"unexpected: {status} {body}")

    def adapt_input_required() -> None:
        req = onboarding_request("ADAPT_PLAN", {"current_plan": {"plan_id": "plan-1"}})
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("planning", "adapt_onboarding_plan", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_INPUT_REQUIRED", f"unexpected: {status} {body}")

    def explain_input_required() -> None:
        req = onboarding_request("ANSWER_QUESTION", {"current_plan": {"plan_id": "plan-1"}})
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("planning", "explain_onboarding_plan", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_INPUT_REQUIRED", f"unexpected: {status} {body}")

    def invalid_operation() -> None:
        req = onboarding_request("GENERATE_PLAN", {"role": "Frontend Developer"})
        req["operation"] = "DELETE_PLAN"
        status, _ = api.call("POST", "/orchestrator/dispatch", dispatch("profile", "get_employee_onboarding_profile", req))
        expect(status == 422, f"expected 422, got {status}")

    def empty_calls() -> None:
        status, _ = api.call("POST", "/orchestrator/dispatch", {"mode": "parallel", "calls": []})
        expect(status == 422, f"expected 422, got {status}")

    def retry_once() -> None:
        req = onboarding_request("GENERATE_PLAN", {"role": "Frontend Developer"})
        req["metadata"] = {"mock_scenario": "retry_once"}
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("profile", "get_employee_onboarding_profile", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_COMPLETED", f"retry did not recover: {status} {body}")
        attempt = body["results"][0]["artifact"].get("metadata", {}).get("attempt")
        expect(attempt == 2, f"expected mock attempt 2 after retry, got {attempt}: {body}")

    def partial_success() -> None:
        req = onboarding_request("GENERATE_PLAN", {"role": "Frontend Developer"})
        req["metadata"] = {"mock_scenario": "partial_success"}
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("profile", "get_employee_onboarding_profile", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_COMPLETED", f"unexpected: {status} {body}")
        expect(body["results"][0]["artifact"].get("status") == "PARTIAL_SUCCESS", f"missing partial result: {body}")

    def domain_failed_result() -> None:
        req = onboarding_request("GENERATE_PLAN", {"role": "Frontend Developer"})
        req["metadata"] = {"mock_scenario": "failed_result"}
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("profile", "get_employee_onboarding_profile", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_FAILED", f"unexpected: {status} {body}")

    def malformed_output() -> None:
        req = onboarding_request("GENERATE_PLAN", {"role": "Frontend Developer"})
        req["metadata"] = {"mock_scenario": "malformed_output"}
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("profile", "get_employee_onboarding_profile", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_FAILED", f"unexpected: {status} {body}")

    def wrong_artifact() -> None:
        req = onboarding_request("GENERATE_PLAN", {"role": "Frontend Developer"})
        req["metadata"] = {"mock_scenario": "wrong_artifact_type"}
        status, body = api.call("POST", "/orchestrator/dispatch", dispatch("profile", "get_employee_onboarding_profile", req))
        expect(status == 200 and result_state(body) == "TASK_STATE_FAILED", f"unexpected: {status} {body}")

    add("health endpoint", health)
    add("readiness endpoint", ready)
    add("public Agent Cards and skills", cards)
    add("protected endpoint rejects missing key", auth_missing)
    add("protected endpoint rejects bad key", auth_bad)
    add("Profile Agent success", profile_success)
    add("Knowledge Agent success", knowledge_success)
    add("Planning generate success", planning_generate_success)
    add("GENERATE_PLAN stage 1 parallel", generate_parallel_stage1)
    add("ANSWER_QUESTION Profile+Knowledge parallel", question_parallel)
    add("REVISE_PLAN Planning success", revise_success)
    add("ADAPT_PLAN Planning success", adapt_success)
    add("Plan explanation success", explain_success)
    add("unsupported skill rejected", unsupported_skill)
    add("Profile missing employee_id -> INPUT_REQUIRED", profile_input_required)
    add("Knowledge missing query -> INPUT_REQUIRED", knowledge_input_required)
    add("Generate missing knowledge context -> INPUT_REQUIRED", planning_generate_input_required)
    add("Revise missing plan/instructions -> INPUT_REQUIRED", revise_input_required)
    add("Adapt missing trigger -> INPUT_REQUIRED", adapt_input_required)
    add("Explain missing question -> INPUT_REQUIRED", explain_input_required)
    add("invalid operation -> HTTP 422", invalid_operation)
    add("empty calls -> HTTP 422", empty_calls)

    if args.include_mock_failures:
        add("retryable 503 recovers on second attempt", retry_once)
        add("PARTIAL_SUCCESS result completes with warning", partial_success)
        add("domain FAILED result -> task FAILED", domain_failed_result)
        add("malformed Langflow output -> task FAILED", malformed_output)
        add("wrong artifact type -> task FAILED", wrong_artifact)

    results = [run_case(name, function) for name, function in cases]
    passed = sum(item.passed for item in results)
    failed = len(results) - passed

    for item in results:
        marker = "PASS" if item.passed else "FAIL"
        detail = f" — {item.detail}" if item.detail else ""
        print(f"[{marker}] {item.name} ({item.duration_ms} ms){detail}")

    report = {
        "base_url": args.base_url,
        "generated_at_epoch": int(time.time()),
        "summary": {"total": len(results), "passed": passed, "failed": failed},
        "results": [asdict(item) for item in results],
    }
    report_path = Path(args.report)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSummary: {passed}/{len(results)} passed; report: {report_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
