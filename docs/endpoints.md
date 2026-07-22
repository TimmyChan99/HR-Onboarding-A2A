# Endpoint reference

The service mounts three logical A2A agents under one FastAPI origin.

## Discovery

| Agent | Agent Card |
|---|---|
| Profile | `GET /agents/profile/.well-known/agent-card.json` |
| Knowledge | `GET /agents/knowledge/.well-known/agent-card.json` |
| Planning | `GET /agents/planning/.well-known/agent-card.json` |

Agent Cards are public. They advertise the `HTTP+JSON` A2A 1.0 interface and API-key transport authentication.

## Standard A2A HTTP+JSON routes

For each `{agent}` in `profile`, `knowledge`, and `planning`:

| Operation | Route |
|---|---|
| Send message | `POST /agents/{agent}/message:send` |
| Send streaming message | `POST /agents/{agent}/message:stream` |
| Get task | `GET /agents/{agent}/tasks/{task_id}` |
| List tasks | `GET /agents/{agent}/tasks` |
| Cancel task | `POST /agents/{agent}/tasks/{task_id}:cancel` |
| Subscribe to task | `GET` or `POST /agents/{agent}/tasks/{task_id}:subscribe` |
| Extended Agent Card | `GET /agents/{agent}/extendedAgentCard` when advertised |

The MVP advertises `streaming=false`, `push_notifications=false`, and `extended_agent_card=false`. Clients must respect the Agent Card instead of assuming every exposed route is supported for production use.

## Langflow-facing convenience API

`POST /orchestrator/dispatch`

This is a project-specific façade designed for the ABA Fusion API Request tool. It accepts one or more high-level calls and internally performs real A2A discovery and messaging.

`GET /orchestrator/agents`

Returns the fixed logical-agent registry and skill identifiers for diagnostics. It is protected by the service API key.

## Operations and skills

| Backend operation | Primary skills |
|---|---|
| `GENERATE_PLAN` | `get_employee_onboarding_profile`, `get_role_onboarding_requirements`, `generate_onboarding_plan` |
| `REVISE_PLAN` | optional Profile/Knowledge refresh, then `revise_onboarding_plan` |
| `ANSWER_QUESTION` | `answer_onboarding_question`, `get_employee_onboarding_profile`, or `explain_onboarding_plan` |
| `ADAPT_PLAN` | optional Profile/Knowledge refresh, then `adapt_onboarding_plan` |

## Non-A2A callback

The final callback to the Augmented Talents backend remains a normal authenticated HTTP request sent by the Langflow Orchestrator. It is not exposed by this service because the callback URL and backend authentication belong to the application integration boundary.
