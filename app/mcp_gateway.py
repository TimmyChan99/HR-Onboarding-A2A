from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import urlsplit

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas import DispatchRequest, DispatchResponse, OnboardingRequest

logger = logging.getLogger(__name__)


class Dispatcher(Protocol):
    async def dispatch(self, request: DispatchRequest) -> DispatchResponse: ...


class MCPProfileDispatchCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: Literal["profile"]
    skill_id: Literal[
        "get_employee_onboarding_profile",
        "assess_profile_completeness",
        "identify_onboarding_constraints",
    ]
    request: OnboardingRequest


class MCPKnowledgeDispatchCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: Literal["knowledge"]
    skill_id: Literal[
        "search_onboarding_knowledge",
        "answer_onboarding_question",
        "get_role_onboarding_requirements",
    ]
    request: OnboardingRequest


class MCPPlanningDispatchCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: Literal["planning"]
    skill_id: Literal[
        "generate_onboarding_plan",
        "revise_onboarding_plan",
        "adapt_onboarding_plan",
        "explain_onboarding_plan",
    ]
    request: OnboardingRequest


MCPDispatchCall = Annotated[
    MCPProfileDispatchCall | MCPKnowledgeDispatchCall | MCPPlanningDispatchCall,
    Field(discriminator="agent"),
]


def create_onboarding_mcp(
    dispatcher: Dispatcher,
    *,
    public_base_url: str = "http://localhost:8080",
) -> FastMCP:
    """
    Create the MCP interface used by the Langflow Orchestrator.

    The dispatcher argument must expose:

        await dispatcher.dispatch(request: DispatchRequest) -> DispatchResponse
    """

    mcp = FastMCP(
        name="Adaptive Onboarding A2A Gateway",
        instructions=(
            "Provides a controlled tool for invoking the Adaptive Onboarding "
            "A2A dispatcher. The tool coordinates Profile, Knowledge, and "
            "Planning remote agents."
        ),
        stateless_http=True,
        json_response=True,
        transport_security=_transport_security(public_base_url),
        # The MCP application is mounted at /mcp by the parent application.
        streamable_http_path="/",
    )

    @mcp.tool(
        name="dispatch_onboarding_agents",
        description=(
            "Dispatch one or more onboarding tasks to the Profile, Knowledge, "
            "or Planning A2A agents. Use parallel mode only for independent "
            "calls. Dependent Planning work must be invoked in a later tool call "
            "after upstream artifacts have been received."
        ),
    )
    async def dispatch_onboarding_agents(
        mode: Literal["parallel", "series"],
        calls: Annotated[list[MCPDispatchCall], Field(min_length=1, max_length=10)],
        ctx: Context[Any, Any, Any],
    ) -> dict[str, Any]:
        """
        Invoke the A2A dispatcher.

        Args:
            mode:
                "parallel" for independent calls or "series" for sequential
                execution.
            calls:
                Non-empty list of A2A call descriptions. Each item must contain
                agent, skill_id, and request.
        """

        await ctx.info(
            f"Dispatching {len(calls)} onboarding A2A call(s) in {mode} mode."
        )

        try:
            dispatch_request = DispatchRequest.model_validate(
                {
                    "mode": mode,
                    "calls": [call.model_dump(mode="json") for call in calls],
                }
            )
        except ValidationError as exc:
            logger.warning("Invalid MCP dispatcher request: %s", exc)

            raise ValueError(
                "Invalid onboarding dispatcher request: "
                f"{exc.errors(include_url=False)}"
            ) from exc

        result = await dispatcher.dispatch(dispatch_request)

        await ctx.info("Onboarding A2A dispatch completed.")

        return result.model_dump(mode="json")

    return mcp


def _transport_security(public_base_url: str) -> TransportSecuritySettings:
    parsed = urlsplit(public_base_url)
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_origins = [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    ]

    if parsed.netloc:
        allowed_hosts.append(parsed.netloc)
        if parsed.scheme:
            allowed_origins.append(f"{parsed.scheme}://{parsed.netloc}")

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(allowed_hosts)),
        allowed_origins=list(dict.fromkeys(allowed_origins)),
    )
