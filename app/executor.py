from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from a2a.helpers import new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Task, TaskState, TaskStatus
from pydantic import ValidationError

from app.internal_knowledge_agent import InternalKnowledgeAgent
from app.langflow_client import LangflowClient, LangflowInvocationError
from app.message_utils import MessagePayloadError, command_from_message, data_part
from app.registry import AgentSpec
from app.schemas import A2ACommand, AgentResult
from app.validation import InputRequiredError, validate_command

logger = logging.getLogger(__name__)


class LangflowAgentExecutor(AgentExecutor):
    """A2A task executor that delegates specialty work to one Langflow flow.

    The external service owns protocol concerns (task lifecycle, persistence,
    artifacts, authentication). The Langflow flow owns domain reasoning and tool
    use for one specialty only.
    """

    def __init__(
        self,
        *,
        spec: AgentSpec,
        langflow_client: LangflowClient,
        internal_knowledge_agent: InternalKnowledgeAgent | None = None,
    ) -> None:
        self.spec = spec
        self.langflow_client = langflow_client
        self.internal_knowledge_agent = internal_knowledge_agent
        self._active: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_message = context.message
        if user_message is None:
            raise ValueError("A2A execution requires a user message")

        # DefaultRequestHandler allocates these IDs for new work. Generate them
        # defensively only when an alternative handler did not supply them.
        task_id = context.task_id or user_message.task_id or str(uuid.uuid4())
        context_id = (
            context.context_id
            or user_message.context_id
            or str(uuid.uuid4())
        )

        if context.current_task is not None:
            initial_task = Task()
            initial_task.CopyFrom(context.current_task)
            # Preserve the new user turn in task history for stateful continuations.
            initial_task.history.append(user_message)
        else:
            initial_task = Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[user_message],
            )

        # A2A v1 task mode requires the Task to be the first emitted event.
        await event_queue.enqueue_event(initial_task)

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
        )
        await updater.start_work(
            message=updater.new_agent_message(
                parts=[new_text_part(f"{self.spec.name} is processing the request.")]
            )
        )

        try:
            raw_command = command_from_message(user_message)
            command = A2ACommand.model_validate(raw_command)
            validate_command(self.spec, command)

            worker = asyncio.create_task(self._run_command(command))
            async with self._lock:
                self._active[task_id] = worker

            result = await worker
            result.metadata.update(
                {
                    "agent": self.spec.key,
                    "skill_id": command.skill_id,
                    "request_id": command.request.request_id,
                    "run_id": command.request.run_id,
                    "correlation_id": command.request.correlation_id,
                }
            )
            await updater.add_artifact(
                name=self.spec.artifact_type.lower(),
                parts=[data_part(result.model_dump(mode="json"))],
                metadata={
                    "artifact_type": self.spec.artifact_type,
                    "schema_version": result.schema_version,
                },
                last_chunk=True,
            )

            completion_message = updater.new_agent_message(
                parts=[
                    new_text_part(
                        f"{self.spec.name} completed with result status {result.status}."
                    )
                ]
            )
            if result.status in {"SUCCEEDED", "PARTIAL_SUCCESS"}:
                await updater.complete(message=completion_message)
            else:
                await updater.failed(message=completion_message)
        except InputRequiredError as exc:
            await updater.requires_input(
                message=updater.new_agent_message(
                    parts=[
                        new_text_part(
                            "Additional input is required: "
                            f"{exc}. Missing fields: {', '.join(exc.fields)}"
                        )
                    ]
                )
            )
        except asyncio.CancelledError:
            await updater.cancel(
                message=updater.new_agent_message(
                    parts=[new_text_part("The task was canceled.")]
                )
            )
            raise
        except (ValidationError, MessagePayloadError, ValueError) as exc:
            logger.warning(
                "Invalid A2A agent request",
                extra={"agent": self.spec.key, "task_id": task_id},
            )
            await updater.failed(
                message=updater.new_agent_message(
                    parts=[new_text_part(f"Invalid request: {exc}")]
                )
            )
        except LangflowInvocationError as exc:
            logger.exception(
                "Langflow agent execution failed",
                extra={"agent": self.spec.key, "task_id": task_id},
            )
            await updater.failed(
                message=updater.new_agent_message(
                    parts=[
                        new_text_part(
                            "Executor flow failed. "
                            f"Retryable: {exc.retryable}. Error: {exc}"
                        )
                    ]
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Unexpected executor failure",
                extra={"agent": self.spec.key, "task_id": task_id},
            )
            await updater.failed(
                message=updater.new_agent_message(
                    parts=[new_text_part(f"Unexpected executor failure: {exc}")]
                )
            )
        finally:
            async with self._lock:
                self._active.pop(task_id, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        context_id = context.context_id or ""
        if not task_id and context.current_task is not None:
            task_id = context.current_task.id
            context_id = context.current_task.context_id
        if not task_id:
            raise ValueError("Cancellation requires a task ID")

        async with self._lock:
            worker = self._active.get(task_id)
        if worker and not worker.done():
            worker.cancel()

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
        )
        await updater.cancel(
            message=updater.new_agent_message(
                parts=[new_text_part("Cancellation was requested.")]
            )
        )

    async def _run_command(self, command: A2ACommand) -> AgentResult:
        if self.spec.key == "knowledge" and self.internal_knowledge_agent is not None:
            return await self.internal_knowledge_agent.run(command)

        return await self.langflow_client.run_agent(
            agent_key=self.spec.key,
            command=command.model_dump(mode="json"),
            session_id=command.request.correlation_id,
            expected_artifact_type=self.spec.artifact_type,
        )
