from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.types import GetTaskRequest, Message, Part, Role, SendMessageRequest, TaskState
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from app.config import Settings
from app.registry import AGENTS
from app.schemas import DispatchCall, DispatchResult

logger = logging.getLogger(__name__)

_TERMINAL_OR_INTERRUPT_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
    "TASK_STATE_INPUT_REQUIRED",
    "TASK_STATE_AUTH_REQUIRED",
}


class A2AInvocationError(RuntimeError):
    pass


class InternalA2AClient:
    """Protocol-compliant A2A client used by the Langflow-facing dispatcher.

    For every call it discovers the Agent Card, verifies the requested skill,
    chooses the HTTP+JSON binding, sends an A2A SendMessageRequest, and
    normalizes the resulting Task/Artifact for the Langflow Supervisor.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def close(self) -> None:
        # Each invoke call owns and closes its transport client.
        return None

    def base_url_for(self, agent: str) -> str:
        return f"{self.settings.internal_base_url}/agents/{agent}"

    def _http_headers(self) -> dict[str, str]:
        return {
            self.settings.a2a_api_key_header: (
                self.settings.a2a_api_key.get_secret_value()
            ),
            "A2A-Version": "1.0",
            "accept": "application/json, application/a2a+json",
        }

    async def invoke(self, call: DispatchCall) -> DispatchResult:
        spec = AGENTS[call.agent]
        if call.skill_id not in spec.skill_ids:
            return DispatchResult(
                agent=call.agent,
                skill_id=call.skill_id,
                status="TASK_STATE_REJECTED",
                error={
                    "code": "SKILL_NOT_ADVERTISED",
                    "message": f"Skill '{call.skill_id}' is not advertised by {spec.name}",
                },
            )

        task_id: str | None = None
        context_id = call.request.correlation_id
        latest_state = "TASK_STATE_UNSPECIFIED"
        artifact: dict[str, Any] | None = None

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.a2a_client_timeout_seconds),
                verify=self.settings.verify_tls,
                headers=self._http_headers(),
            ) as http_client:
                base_url = self.base_url_for(call.agent)
                resolver = A2ACardResolver(http_client, base_url)
                card = await resolver.get_agent_card()

                advertised_skills = {skill.id for skill in card.skills}
                if call.skill_id not in advertised_skills:
                    raise A2AInvocationError(
                        f"Remote Agent Card does not advertise skill '{call.skill_id}'"
                    )

                config = ClientConfig(
                    streaming=False,
                    polling=False,
                    httpx_client=http_client,
                    supported_protocol_bindings=["HTTP+JSON"],
                    use_client_preference=True,
                    accepted_output_modes=["application/json"],
                )
                client = await create_client(card, client_config=config)
                try:
                    command = {
                        "skill_id": call.skill_id,
                        "request": call.request.model_dump(mode="json"),
                    }
                    data_value = ParseDict(command, Value())
                    message = Message(
                        role=Role.ROLE_USER,
                        message_id=str(uuid.uuid4()),
                        context_id=context_id,
                        parts=[Part(data=data_value, media_type="application/json")],
                    )
                    request = SendMessageRequest(message=message)

                    async for event in client.send_message(request):
                        if event.HasField("task"):
                            task_id = event.task.id
                            context_id = event.task.context_id or context_id
                            latest_state = TaskState.Name(event.task.status.state)
                            artifact = self._artifact_from_task(event.task) or artifact
                        elif event.HasField("status_update"):
                            latest_state = TaskState.Name(
                                event.status_update.status.state
                            )
                        elif event.HasField("artifact_update"):
                            artifact = self._artifact_to_dict(
                                event.artifact_update.artifact
                            )
                        elif event.HasField("message"):
                            message_dict = MessageToDict(
                                event.message,
                                preserving_proto_field_name=True,
                            )
                            return DispatchResult(
                                agent=call.agent,
                                skill_id=call.skill_id,
                                status="DIRECT_MESSAGE",
                                context_id=event.message.context_id or context_id,
                                artifact={"message": message_dict},
                            )

                    if task_id and latest_state not in _TERMINAL_OR_INTERRUPT_STATES:
                        # Non-streaming implementations normally return an aggregated
                        # terminal Task. Retrieve once defensively if they respond early.
                        task = await client.get_task(GetTaskRequest(id=task_id))
                        latest_state = TaskState.Name(task.status.state)
                        artifact = self._artifact_from_task(task) or artifact
                finally:
                    # The SDK transport owns the supplied HTTP client, so close the
                    # A2A client before leaving the AsyncClient context.
                    await client.close()

            error = None
            if latest_state != "TASK_STATE_COMPLETED":
                error = {
                    "code": latest_state,
                    "message": f"Remote A2A task finished in state {latest_state}",
                }

            return DispatchResult(
                agent=call.agent,
                skill_id=call.skill_id,
                status=latest_state,
                task_id=task_id,
                context_id=context_id,
                artifact=artifact,
                error=error,
            )
        except (httpx.HTTPError, A2AInvocationError, ValueError) as exc:
            logger.exception(
                "A2A invocation failed",
                extra={"agent": call.agent, "skill_id": call.skill_id},
            )
            return DispatchResult(
                agent=call.agent,
                skill_id=call.skill_id,
                status="TASK_STATE_FAILED",
                task_id=task_id,
                context_id=context_id,
                error={
                    "code": "A2A_INVOCATION_FAILED",
                    "message": str(exc),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Unexpected A2A invocation failure",
                extra={"agent": call.agent, "skill_id": call.skill_id},
            )
            return DispatchResult(
                agent=call.agent,
                skill_id=call.skill_id,
                status="TASK_STATE_FAILED",
                task_id=task_id,
                context_id=context_id,
                error={
                    "code": "A2A_CLIENT_INTERNAL_ERROR",
                    "message": str(exc),
                },
            )

    @classmethod
    def _artifact_from_task(cls, task: Any) -> dict[str, Any] | None:
        if not task.artifacts:
            return None
        return cls._artifact_to_dict(task.artifacts[-1])

    @staticmethod
    def _artifact_to_dict(artifact: Any) -> dict[str, Any]:
        raw = MessageToDict(artifact, preserving_proto_field_name=True)
        for part in raw.get("parts", []):
            data = part.get("data")
            if isinstance(data, dict):
                return data
        return raw
