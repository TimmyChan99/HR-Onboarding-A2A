from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.schemas import AgentResult, ExecutorWebhookCallback

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 502, 503, 504}
_JSON_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)


class LangflowInvocationError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class WebhookCallbackKey:
    agent_key: str
    request_id: str
    run_id: str
    correlation_id: str


class LangflowClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        api_key_value = (
            f"{settings.langflow_api_key_prefix}{settings.langflow_api_key.get_secret_value()}"
        )
        headers = {
            "Content-Type": "application/json",
            "accept": "application/json",
        }
        if api_key_value:
            headers[settings.langflow_api_key_header] = api_key_value
        self._client = httpx.AsyncClient(
            base_url=settings.langflow_base_url,
            timeout=httpx.Timeout(settings.langflow_timeout_seconds),
            verify=settings.verify_tls,
            headers=headers,
        )
        self._webhook_callbacks: dict[WebhookCallbackKey, asyncio.Future[AgentResult]] = {}
        self._callback_lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._callback_lock:
            for callback in self._webhook_callbacks.values():
                if not callback.done():
                    callback.cancel()
            self._webhook_callbacks.clear()
        await self._client.aclose()

    async def run_agent(
        self,
        *,
        agent_key: str,
        command: dict[str, Any],
        session_id: str,
        expected_artifact_type: str,
    ) -> AgentResult:
        if self.settings.langflow_execution_mode == "webhook":
            return await self._run_webhook_agent(
                agent_key=agent_key,
                command=command,
                expected_artifact_type=expected_artifact_type,
            )

        last_error: Exception | None = None
        for attempt in range(1, self.settings.langflow_max_attempts + 1):
            started = time.perf_counter()
            try:
                response = await self._execute_agent(
                    agent_key=agent_key,
                    command=command,
                    session_id=session_id,
                )
                if response.status_code in _RETRYABLE_STATUS:
                    raise LangflowInvocationError(
                        f"Langflow returned retryable HTTP {response.status_code}",
                        retryable=True,
                    )
                response.raise_for_status()
                raw = response.json()
                result_dict = self._extract_result(raw)
                result = AgentResult.model_validate(result_dict)
                if result.artifact_type != expected_artifact_type:
                    raise LangflowInvocationError(
                        "Langflow returned artifact_type "
                        f"'{result.artifact_type}', expected '{expected_artifact_type}'"
                    )
                duration_ms = int((time.perf_counter() - started) * 1000)
                logger.info(
                    "Langflow agent completed",
                    extra={
                        "agent": agent_key,
                        "attempt": attempt,
                        "duration_ms": duration_ms,
                    },
                )
                return result
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = LangflowInvocationError(str(exc), retryable=True)
            except LangflowInvocationError as exc:
                last_error = exc
                if not exc.retryable:
                    break
            except (httpx.HTTPStatusError, ValueError, json.JSONDecodeError) as exc:
                last_error = LangflowInvocationError(str(exc), retryable=False)
                break

            if attempt < self.settings.langflow_max_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))

        if isinstance(last_error, LangflowInvocationError):
            raise last_error
        raise LangflowInvocationError("Langflow execution failed without a detailed error")


    async def _execute_agent(
        self,
        *,
        agent_key: str,
        command: dict[str, Any],
        session_id: str,
    ) -> httpx.Response:
        flow_id = self.settings.flow_id_for(agent_key)
        direct_input: dict[str, Any] = {
            "input_value": json.dumps(command, ensure_ascii=False),
            "input_type": "chat",
            "output_type": "chat",
            "session_id": session_id,
        }
        if self.settings.langflow_output_component:
            direct_input["output_component"] = self.settings.langflow_output_component
        return await self._post_run_flow(flow_id, direct_input)

    async def _post_webhook(
        self,
        agent_key: str,
        command: dict[str, Any],
    ) -> httpx.Response:
        """Invoke an executor's Webhook component with the raw A2A command."""
        return await self._client.post(
            self.settings.webhook_url_for(agent_key),
            json=command,
        )

    async def _run_webhook_agent(
        self,
        *,
        agent_key: str,
        command: dict[str, Any],
        expected_artifact_type: str,
    ) -> AgentResult:
        key = self._callback_key(agent_key, command)
        callback = await self._register_webhook_callback(key)
        started = time.perf_counter()

        try:
            response = await self._post_webhook(agent_key, command)
            if response.status_code in _RETRYABLE_STATUS:
                raise LangflowInvocationError(
                    f"Langflow webhook returned retryable HTTP {response.status_code}",
                    retryable=True,
                )
            response.raise_for_status()

            direct_result = self._direct_result_or_none(response)
            result = (
                direct_result
                if direct_result is not None
                else await asyncio.wait_for(
                    callback,
                    timeout=self.settings.langflow_timeout_seconds,
                )
            )
            if result.artifact_type != expected_artifact_type:
                raise LangflowInvocationError(
                    "Langflow returned artifact_type "
                    f"'{result.artifact_type}', expected '{expected_artifact_type}'"
                )
            logger.info(
                "Langflow webhook agent completed",
                extra={
                    "agent": agent_key,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            return result
        except TimeoutError as exc:
            raise LangflowInvocationError(
                "Timed out waiting for the executor webhook callback",
                retryable=False,
            ) from exc
        except httpx.TimeoutException as exc:
            raise LangflowInvocationError(str(exc), retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise LangflowInvocationError(str(exc), retryable=False) from exc
        finally:
            await self._remove_webhook_callback(key)

    async def complete_webhook_callback(
        self,
        agent_key: str,
        callback: ExecutorWebhookCallback,
    ) -> bool:
        key = WebhookCallbackKey(
            agent_key=agent_key,
            request_id=callback.request_id,
            run_id=callback.run_id,
            correlation_id=callback.correlation_id,
        )
        async with self._callback_lock:
            waiting = self._webhook_callbacks.get(key)
            if waiting is None:
                return False
            if not waiting.done():
                waiting.set_result(callback.result)
            return True

    async def _register_webhook_callback(
        self,
        key: WebhookCallbackKey,
    ) -> asyncio.Future[AgentResult]:
        async with self._callback_lock:
            if key in self._webhook_callbacks:
                raise LangflowInvocationError(
                    "A webhook callback is already pending for this agent request"
                )
            callback: asyncio.Future[AgentResult] = asyncio.get_running_loop().create_future()
            self._webhook_callbacks[key] = callback
            return callback

    async def _remove_webhook_callback(self, key: WebhookCallbackKey) -> None:
        async with self._callback_lock:
            self._webhook_callbacks.pop(key, None)

    @staticmethod
    def _callback_key(agent_key: str, command: dict[str, Any]) -> WebhookCallbackKey:
        request = command.get("request")
        if not isinstance(request, dict):
            raise LangflowInvocationError("Webhook command is missing a request object")
        try:
            return WebhookCallbackKey(
                agent_key=agent_key,
                request_id=str(request["request_id"]),
                run_id=str(request["run_id"]),
                correlation_id=str(request["correlation_id"]),
            )
        except KeyError as exc:
            raise LangflowInvocationError(
                f"Webhook command is missing {exc.args[0]}"
            ) from exc

    @classmethod
    def _direct_result_or_none(cls, response: httpx.Response) -> AgentResult | None:
        try:
            raw = response.json()
            result_dict = cls._extract_result(raw)
            return AgentResult.model_validate(result_dict)
        except (LangflowInvocationError, ValueError, json.JSONDecodeError):
            return None

    async def _post_run_flow(
        self,
        flow_id: str,
        direct_input: dict[str, Any],
    ) -> httpx.Response:
        """Call ABA Fusion/Langflow while supporting both common run payloads.

        Langflow 1.7 deployments commonly expose the direct SimplifiedAPIRequest
        body. Newer deployments may wrap it under ``input_request``. In ``auto``
        mode, a 422 from the direct shape is treated as a schema mismatch and the
        wrapped shape is attempted once; the flow is not executed on a 422.
        """
        path = f"/api/v1/run/{flow_id}"
        params = {"stream": "false"}
        style = self.settings.langflow_api_style

        if style == "wrapped":
            return await self._client.post(
                path,
                params=params,
                json={"input_request": direct_input, "context": {}},
            )

        response = await self._client.post(path, params=params, json=direct_input)
        if style == "auto" and response.status_code == 422:
            logger.info("Retrying Langflow call with wrapped input_request payload")
            return await self._client.post(
                path,
                params=params,
                json={"input_request": direct_input, "context": {}},
            )
        return response

    @classmethod
    def _extract_result(cls, raw: Any) -> dict[str, Any]:
        embedded = cls._find_result_object(raw)
        if embedded is not None:
            return embedded

        standard_paths = [
            ("outputs", 0, "outputs", 0, "results", "message", "text"),
            ("outputs", 0, "outputs", 0, "results", "text", "data", "text"),
            ("message",),
            ("result", "message"),
        ]
        for path in standard_paths:
            candidate = cls._dig(raw, path)
            if isinstance(candidate, str):
                parsed = cls._parse_json_text(candidate)
                if parsed is not None:
                    return parsed

        for candidate in cls._collect_strings(raw):
            parsed = cls._parse_json_text(candidate)
            if parsed is not None:
                return parsed

        raise LangflowInvocationError(
            "No structured AgentResult JSON was found in the Langflow response"
        )

    @staticmethod
    def _dig(value: Any, path: tuple[Any, ...]) -> Any:
        current = value
        for key in path:
            if isinstance(key, int):
                if not isinstance(current, list) or len(current) <= key:
                    return None
                current = current[key]
            else:
                if not isinstance(current, dict):
                    return None
                current = current.get(key)
        return current

    @classmethod
    def _find_result_object(cls, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if {"status", "artifact_type", "data"}.issubset(value):
                return value
            for nested in value.values():
                found = cls._find_result_object(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = cls._find_result_object(nested)
                if found is not None:
                    return found
        return None

    @classmethod
    def _collect_strings(cls, value: Any) -> list[str]:
        strings: list[str] = []
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for nested in value.values():
                strings.extend(cls._collect_strings(nested))
        elif isinstance(value, list):
            for nested in value:
                strings.extend(cls._collect_strings(nested))
        return strings

    @staticmethod
    def _parse_json_text(text: str) -> dict[str, Any] | None:
        candidate = text.strip()
        fenced = _JSON_FENCE.match(candidate)
        if fenced:
            candidate = fenced.group(1).strip()
        if not candidate.startswith("{"):
            return None
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
