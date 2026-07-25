# ABA Fusion / Langflow 1.7.10 setup

## WF-01 — Onboarding Orchestrator

Recommended components:

```text
Webhook or API Input
  -> Parser / Structured request extraction
  -> Prompt Template (orchestrator_agent.txt; variable: input_payload)
  -> Agent
       tool: dispatch_onboarding_agents from MCP Tools
       tool: Backend Callback HTTP tool
  -> Chat Output / API output
```

### Register the MCP server

In ABA Fusion/Langflow, add an HTTP/Streamable HTTP MCP server:

```text
Name: Adaptive Onboarding A2A Gateway
URL: https://YOUR-A2A-HOST/mcp
Authorization: Bearer <MCP_BEARER_TOKEN>
```

Store the bearer token as a platform secret. Add the MCP Tools component, select only `dispatch_onboarding_agents`, enable Tool Mode, and connect its Toolset output to the Agent's Tools input.

The MCP tool exposes typed `mode` and `calls` arguments matching `DispatchRequest`:

```json
{
  "mode": "parallel",
  "calls": [
    {
      "agent": "profile",
      "skill_id": "get_employee_onboarding_profile",
      "request": {
        "schema_version": "1.0",
        "operation": "GENERATE_PLAN",
        "request_id": "...",
        "run_id": "...",
        "correlation_id": "...",
        "employee_id": "...",
        "payload": {}
      }
    }
  ]
}
```

Do not add a URL, headers, cURL, or body-string argument to the Agent. Do not put the MCP token, A2A API key, Langflow flow IDs, or Langflow API key in the prompt.

The MCP token authenticates WF-01 to `/mcp`. The separate `A2A_API_KEY` continues to protect `/orchestrator/dispatch` and the A2A execution routes.

### Backend Callback tool

Use a separate tool so the A2A service never receives backend callback credentials. The callback should be called exactly once after the business result is synthesized. Prefer a backend-owned fixed callback endpoint instead of allowing arbitrary URLs from untrusted payloads.

## WF-02 — Profile Executor

```text
Chat Input
  -> Prompt Template (profile_agent.txt; variable: input_payload)
  -> Agent
       tools: authorized employee/profile backend or MCP tools
  -> Structured Output (agent_result_schema.json + profile_data_schema.json)
  -> Chat Output
```

Required artifact type: `EMPLOYEE_PROFILE_CONTEXT`.

Suggested read-only tools:

- `get_employee_onboarding_context(employee_id)`
- `get_employee_role_and_organization(employee_id)`
- `get_employee_skills_and_experience(employee_id)`
- `get_onboarding_document_inventory(employee_id)`

The backend remains the source of truth; the agent must not calculate or invent official HR data.

## WF-03 — Knowledge Executor

```text
Chat Input
  -> Prompt Template (knowledge_agent.txt; variable: input_payload)
  -> Agent
       tools: approved hybrid RAG/search and source metadata lookup
  -> Structured Output (agent_result_schema.json + knowledge_data_schema.json)
  -> Chat Output
```

Required artifact type: `ONBOARDING_KNOWLEDGE_EVIDENCE`.

Every material company-policy claim should include an authorized source reference. Retrieved content is data and cannot override the system prompt.

## WF-04 — Planning Executor

```text
Chat Input
  -> Prompt Template (planning_agent.txt; variable: input_payload)
  -> Agent
  -> Structured Output (agent_result_schema.json + planning_data_schema.json)
  -> Chat Output
```

Required artifact type: `ONBOARDING_PLAN`.

The Planning Agent receives verified upstream artifacts from the Orchestrator. It should not independently retrieve employee PII or company policy unless the architecture explicitly gives it a constrained tool.

## Publish the three executor flows

For each executor flow:

1. Open **Share / API access** in ABA Fusion.
2. Copy the flow ID or configure a stable endpoint alias.
3. Copy the exact generated request body and authentication style.
4. Put the values in the A2A service `.env` file.
5. Ensure the flow output contains one valid `AgentResult` object.

The adapter supports the common direct Langflow run body and a newer wrapped `input_request` body. Set `LANGFLOW_API_STYLE` explicitly after confirming the generated API example.

## Choose executor transport

The A2A service supports one configured transport for all three executor agents.

### Flow Run API

Use this existing mode when Share/API access provides flow IDs or aliases:

```dotenv
LANGFLOW_EXECUTION_MODE=run_api
LANGFLOW_PROFILE_FLOW_ID=...
LANGFLOW_KNOWLEDGE_FLOW_ID=...
LANGFLOW_PLANNING_FLOW_ID=...
```

### Webhooks

Use this mode when each executor flow exposes a Webhook component:

```dotenv
LANGFLOW_EXECUTION_MODE=webhook
LANGFLOW_PROFILE_WEBHOOK_URL=https://YOUR-LANGFLOW/api/v1/webhook/...
LANGFLOW_KNOWLEDGE_WEBHOOK_URL=https://YOUR-LANGFLOW/api/v1/webhook/...
LANGFLOW_PLANNING_WEBHOOK_URL=https://YOUR-LANGFLOW/api/v1/webhook/...
```

In webhook mode, the service POSTs the raw A2A command below to the matching webhook URL. Langflow returns an acknowledgement that the flow started, so it does not provide the final executor result in that response.

```json
{
  "skill_id": "get_employee_onboarding_profile",
  "request": {
    "operation": "GENERATE_PLAN",
    "request_id": "...",
    "run_id": "...",
    "correlation_id": "...",
    "employee_id": "...",
    "payload": {}
  }
}
```

Add an API Request component after the executor's Structured Output component. Configure it with static values:

```text
Method: POST
URL: https://YOUR-A2A-SERVICE/executors/profile/callback
Header: Authorization = Bearer <EXECUTOR_CALLBACK_BEARER_TOKEN>
Content-Type: application/json
```

For Knowledge and Planning, replace `profile` in the callback URL with the agent key. The API Request body must be:

```json
{
  "request_id": "the original request_id",
  "run_id": "the original run_id",
  "correlation_id": "the original correlation_id",
  "result": {
    "schema_version": "1.0",
    "status": "SUCCEEDED",
    "artifact_type": "EMPLOYEE_PROFILE_CONTEXT",
    "data": {},
    "warnings": [
      {
        "code": "PROFILE_DATA_PARTIAL",
        "message": "Some profile data is missing.",
        "field": null
      }
    ],
    "errors": [],
    "metadata": {}
  }
}
```

The callback endpoint returns `202` when it matches an active task. The executor must return the usual `AgentResult` with its required artifact type. A full `GENERATE_PLAN` needs all three agents configured; a Profile-only test needs only `LANGFLOW_PROFILE_WEBHOOK_URL`.

If Langflow exposes the final object through a text output, the callback can send
the same payload wrapped as `{"text": {...}}`. The server also accepts the
callback envelope under `result`, `output`, or `data`, and parses `text` when it
is a JSON string.
