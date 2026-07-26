from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_rest_routes
from a2a.server.tasks import DatabaseTaskStore
from fastapi import FastAPI, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from starlette.applications import Starlette

from app.a2a_client import InternalA2AClient
from app.auth import APIKeyMiddleware
from app.cards import build_agent_card
from app.config import Settings, get_settings
from app.dispatcher import A2ADispatcher
from app.executor import LangflowAgentExecutor
from app.internal_knowledge_agent import InternalKnowledgeAgent
from app.langflow_client import LangflowClient
from app.logging_config import configure_logging
from app.mcp_gateway import create_onboarding_mcp
from app.registry import AGENTS
from app.request_logging import RequestAuditMiddleware
from app.schemas import DispatchRequest, DispatchResponse, ExecutorWebhookCallback

settings: Settings = get_settings()
configure_logging(settings.log_level, settings.log_dir)
logger = logging.getLogger(__name__)

Path("data").mkdir(parents=True, exist_ok=True)
engine: AsyncEngine = create_async_engine(settings.database_url, pool_pre_ping=True)
langflow_client = LangflowClient(settings)
internal_knowledge_agent = (
    InternalKnowledgeAgent(settings)
    if settings.knowledge_agent_mode == "internal"
    else None
)
internal_a2a_client = InternalA2AClient(settings)
dispatcher = A2ADispatcher(internal_a2a_client, settings)
onboarding_mcp = create_onboarding_mcp(
    dispatcher,
    public_base_url=settings.public_base_url,
)
task_stores: dict[str, DatabaseTaskStore] = {}


def build_agent_subapp(agent_key: str) -> Starlette:
    spec = AGENTS[agent_key]
    card = build_agent_card(spec, settings)
    store = DatabaseTaskStore(
        engine=engine,
        create_table=True,
        table_name=f"a2a_{agent_key}_tasks",
    )
    task_stores[agent_key] = store
    handler = DefaultRequestHandler(
        agent_card=card,
        agent_executor=LangflowAgentExecutor(
            spec=spec,
            langflow_client=langflow_client,
            internal_knowledge_agent=internal_knowledge_agent,
        ),
        task_store=store,
    )

    routes = []
    routes.extend(create_agent_card_routes(agent_card=card))
    routes.extend(create_rest_routes(request_handler=handler, path_prefix=""))
    return Starlette(routes=routes)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with onboarding_mcp.session_manager.run():
        for store in task_stores.values():
            await store.initialize()
        logger.info("A2A onboarding service started", extra={"agents": list(AGENTS)})
        try:
            yield
        finally:
            await dispatcher.close()
            await internal_a2a_client.close()
            await langflow_client.close()
            await engine.dispose()
            logger.info("A2A onboarding service stopped")


app = FastAPI(
    title="Augmented Talents Onboarding A2A Service",
    version="1.0.0",
    description=(
        "A2A 1.0 protocol façade for Profile, Knowledge, and Planning executor "
        "agents implemented as Langflow flows."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    APIKeyMiddleware,
    header_name=settings.a2a_api_key_header,
    expected_key=settings.a2a_api_key.get_secret_value(),
    mcp_bearer_token=settings.mcp_bearer_token.get_secret_value(),
    executor_callback_bearer_token=(
        settings.executor_callback_bearer_token.get_secret_value()
    ),
)
app.add_middleware(
    RequestAuditMiddleware,
    request_body_max_bytes=settings.log_request_body_max_bytes,
    response_body_max_bytes=settings.log_response_body_max_bytes,
)


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "a2a-onboarding-langflow",
        "version": "1.0.0",
        "protocol": "A2A 1.0 HTTP+JSON",
        "orchestrator_transport": "MCP Streamable HTTP",
        "agents": {
            key: {
                "card": f"/agents/{key}/.well-known/agent-card.json",
                "send_message": f"/agents/{key}/message:send",
                "get_task": f"/agents/{key}/tasks/{{task_id}}",
                "cancel_task": f"/agents/{key}/tasks/{{task_id}}:cancel",
            }
            for key in AGENTS
        },
        "langflow_tool_endpoint": "/mcp",
        "rest_dispatch_endpoint": "/orchestrator/dispatch",
    }


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def ready() -> dict[str, Any]:
    missing = settings.missing_executor_agents()
    return {
        "status": "ready" if not missing else "configuration-required",
        "execution_mode": settings.langflow_execution_mode,
        "knowledge_agent_mode": settings.knowledge_agent_mode,
        "missing_executor_agents": missing,
    }


@app.get("/orchestrator/agents")
async def list_agents() -> dict[str, Any]:
    return {
        key: {
            "name": spec.name,
            "description": spec.description,
            "skills": sorted(spec.skill_ids),
            "card_url": f"{settings.public_base_url}/agents/{key}/.well-known/agent-card.json",
        }
        for key, spec in AGENTS.items()
    }


@app.post("/orchestrator/dispatch", response_model=DispatchResponse)
async def dispatch(request: DispatchRequest) -> DispatchResponse:
    try:
        return await dispatcher.dispatch(request)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Dispatcher failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/executors/{agent_key}/callback", status_code=status.HTTP_202_ACCEPTED)
async def executor_callback(
    agent_key: str,
    callback: ExecutorWebhookCallback,
) -> dict[str, bool]:
    if agent_key not in AGENTS:
        raise HTTPException(status_code=404, detail="Unknown executor agent")
    accepted = await langflow_client.complete_webhook_callback(agent_key, callback)
    if not accepted:
        raise HTTPException(
            status_code=404,
            detail="No active executor webhook callback matches this request",
        )
    return {"accepted": True}


for key in AGENTS:
    app.mount(f"/agents/{key}", build_agent_subapp(key))

app.mount("/mcp", onboarding_mcp.streamable_http_app())
