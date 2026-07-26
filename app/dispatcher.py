from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass

from app.a2a_client import InternalA2AClient
from app.config import Settings
from app.schemas import DispatchRequest, DispatchResponse, DispatchResult


@dataclass(slots=True)
class DispatchJob:
    task: asyncio.Task[DispatchResponse]
    created_at: float
    last_seen_at: float
    dispatch_id: str


class A2ADispatcher:
    """High-level API used as one API Request tool by the Langflow Supervisor.

    This is not a replacement protocol. It is a convenience façade that performs
    real A2A discovery and SendMessage calls to the logical A2A agents.
    """

    def __init__(self, client: InternalA2AClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self._jobs: dict[str, DispatchJob] = {}
        self._lock = asyncio.Lock()

    async def dispatch(self, request: DispatchRequest) -> DispatchResponse:
        wait_seconds = self.settings.dispatch_wait_seconds
        if wait_seconds <= 0:
            return await self._execute(request)

        key = self._dispatch_key(request)
        job = await self._job_for(key, request)

        try:
            return await asyncio.wait_for(
                asyncio.shield(job.task),
                timeout=wait_seconds,
            )
        except TimeoutError:
            return self._in_progress_response(request, job.dispatch_id)

    async def close(self) -> None:
        async with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            if not job.task.done():
                job.task.cancel()
        await asyncio.gather(*(job.task for job in jobs), return_exceptions=True)

    async def _execute(self, request: DispatchRequest) -> DispatchResponse:
        if request.mode == "parallel":
            parallel_results = await asyncio.gather(
                *(self.client.invoke(call) for call in request.calls)
            )
            return DispatchResponse(mode=request.mode, results=list(parallel_results))

        series_results: list[DispatchResult] = []
        for call in request.calls:
            series_results.append(await self.client.invoke(call))

        return DispatchResponse(mode=request.mode, results=series_results)

    async def _job_for(self, key: str, request: DispatchRequest) -> DispatchJob:
        now = time.monotonic()
        async with self._lock:
            self._cleanup_locked(now)
            job = self._jobs.get(key)
            if job is not None:
                job.last_seen_at = now
                return job

            job = DispatchJob(
                task=asyncio.create_task(self._execute(request)),
                created_at=now,
                last_seen_at=now,
                dispatch_id=str(uuid.uuid4()),
            )
            self._jobs[key] = job
            return job

    def _cleanup_locked(self, now: float) -> None:
        ttl = self.settings.dispatch_result_ttl_seconds
        expired_keys = [
            key
            for key, job in self._jobs.items()
            if job.task.done() and now - job.last_seen_at > ttl
        ]
        for key in expired_keys:
            self._jobs.pop(key, None)

    @staticmethod
    def _dispatch_key(request: DispatchRequest) -> str:
        canonical = json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _in_progress_response(
        request: DispatchRequest,
        dispatch_id: str,
    ) -> DispatchResponse:
        return DispatchResponse(
            mode=request.mode,
            results=[
                DispatchResult(
                    agent=call.agent,
                    skill_id=call.skill_id,
                    status="TASK_STATE_WORKING",
                    context_id=call.request.correlation_id,
                    artifact=None,
                    error={
                        "code": "DISPATCH_IN_PROGRESS",
                        "message": (
                            "Dispatch is still running. Retry the same "
                            "dispatch_onboarding_agents call to retrieve the "
                            "completed result."
                        ),
                        "retryable": True,
                        "dispatch_id": dispatch_id,
                    },
                )
                for call in request.calls
            ],
        )
