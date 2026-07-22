from __future__ import annotations

from a2a.types import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)

from app.config import Settings
from app.registry import AgentSpec


def build_agent_card(spec: AgentSpec, settings: Settings) -> AgentCard:
    skills = [
        AgentSkill(
            id=skill_id,
            name=name,
            description=description,
            tags=list(tags),
            examples=list(examples),
            input_modes=["application/json"],
            output_modes=["application/json"],
        )
        for skill_id, name, description, tags, examples in spec.skills
    ]

    card = AgentCard(
        name=spec.name,
        description=spec.description,
        version="1.0.0",
        documentation_url=f"{settings.public_base_url}/docs",
        provider=AgentProvider(
            organization="Augmented Talents Onboarding Project",
            url=settings.public_base_url,
        ),
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            extended_agent_card=False,
        ),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="HTTP+JSON",
                protocol_version="1.0",
                url=f"{settings.public_base_url}/agents/{spec.key}",
            )
        ],
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=skills,
    )

    api_key_scheme = SecurityScheme()
    api_key_scheme.api_key_security_scheme.CopyFrom(
        APIKeySecurityScheme(
            description="Service-to-service API key for onboarding A2A calls. Use HTTPS.",
            location="header",
            name=settings.a2a_api_key_header,
        )
    )
    card.security_schemes["onboardingApiKey"].CopyFrom(api_key_scheme)

    requirement = SecurityRequirement()
    requirement.schemes["onboardingApiKey"].CopyFrom(StringList())
    card.security_requirements.append(requirement)

    return card
