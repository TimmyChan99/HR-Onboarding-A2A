from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI

from app.mock_responses import build_mock_agent_result

app = FastAPI(title="Mock Langflow Executor Runtime")


@app.post("/api/v1/run/{flow_id}")
async def run_flow(flow_id: str, body: dict[str, Any]) -> dict[str, Any]:
    request = body.get("input_request", body)
    command = json.loads(request["input_value"])
    agent_key = _agent_key_from_flow_id(flow_id)
    result = build_mock_agent_result(agent_key, command).model_dump(mode="json")
    return {
        "outputs": [
            {
                "outputs": [
                    {"results": {"message": {"text": json.dumps(result)}}}
                ]
            }
        ],
        "session_id": request.get("session_id"),
    }


def _agent_key_from_flow_id(flow_id: str) -> str:
    normalized = flow_id.lower()
    if "profile" in normalized:
        return "profile"
    if "knowledge" in normalized:
        return "knowledge"
    return "planning"
