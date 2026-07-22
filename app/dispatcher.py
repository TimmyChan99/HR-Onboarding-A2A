from __future__ import annotations

import asyncio

from app.a2a_client import InternalA2AClient
from app.schemas import DispatchRequest, DispatchResponse, DispatchResult


class A2ADispatcher:
    """High-level API used as one API Request tool by the Langflow Supervisor.

    This is not a replacement protocol. It is a convenience façade that performs
    real A2A discovery and SendMessage calls to the logical A2A agents.
    """

    def __init__(self, client: InternalA2AClient) -> None:
        self.client = client

    async def dispatch(self, request: DispatchRequest) -> DispatchResponse:
        if request.mode == "parallel":
            results = await asyncio.gather(
                *(self.client.invoke(call) for call in request.calls)
            )
        else:
            results: list[DispatchResult] = []
            for call in request.calls:
                results.append(await self.client.invoke(call))

        return DispatchResponse(mode=request.mode, results=list(results))
