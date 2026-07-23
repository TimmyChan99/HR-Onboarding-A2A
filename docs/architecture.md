# Architecture

```mermaid
flowchart LR
    B[Augmented Talents Backend] -->|request payload| O[WF-01 Langflow Orchestration Agent]
    O -->|MCP tool| M[MCP Gateway]
    M -->|shared service call| D[External A2A Dispatcher]
    D -->|discover card + A2A message| PA[Profile A2A Server]
    D -->|discover card + A2A message| KA[Knowledge A2A Server]
    D -->|discover card + A2A message| PLA[Planning A2A Server]
    PA -->|Langflow Run API| PF[WF-02 Profile Executor]
    KA -->|Langflow Run API| KF[WF-03 Knowledge Executor]
    PLA -->|Langflow Run API| PLF[WF-04 Planning Executor]
    O -->|single secured HTTP callback| B
```

The three A2A servers are logical agents hosted by one FastAPI process. Each publishes a distinct Agent Card and A2A HTTP+JSON interface, owns a distinct task table, and delegates specialty reasoning to one Langflow flow.

## Responsibility boundary

| Component | Responsibility |
|---|---|
| Backend | Source request, business state, authorization context, idempotency authority, callback storage |
| Langflow Orchestrator | Operation classification, dependency planning, parallel/series selection, synthesis, final callback |
| MCP gateway | Typed, bearer-authenticated `dispatch_onboarding_agents` tool for WF-01 |
| External dispatcher | High-level tool for Langflow; performs actual A2A client behavior |
| A2A protocol server | Agent Cards, skill advertisement, messages, task lifecycle, artifacts, task storage, transport auth |
| Langflow Profile flow | Employee context, completeness, constraints |
| Langflow Knowledge flow | RAG, policies, requirements, citations |
| Langflow Planning flow | Generate, revise, adapt, explain plans |
