from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8080

    public_base_url: str = "http://localhost:8080"
    internal_base_url: str = "http://127.0.0.1:8080"

    a2a_api_key: SecretStr = Field(default=SecretStr("development-only-change-me"))
    a2a_api_key_header: str = "X-A2A-API-Key"
    mcp_bearer_token: SecretStr = Field(
        default=SecretStr("development-only-mcp-change-me")
    )

    database_url: str = "sqlite+aiosqlite:///./data/a2a_tasks.db"

    langflow_base_url: str = "https://stg-agentic.abafusion.ai"
    langflow_api_key: SecretStr = Field(default=SecretStr(""))
    langflow_api_key_header: str = "x-api-key"
    langflow_api_key_prefix: str = ""
    langflow_profile_flow_id: str = ""
    langflow_knowledge_flow_id: str = ""
    langflow_planning_flow_id: str = ""
    langflow_timeout_seconds: float = 180.0
    langflow_max_attempts: int = 3
    langflow_output_component: str = ""
    langflow_api_style: Literal["legacy", "wrapped", "auto"] = "auto"
    a2a_client_timeout_seconds: float = 240.0
    verify_tls: bool = True

    @field_validator("public_base_url", "internal_base_url", "langflow_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("langflow_max_attempts")
    @classmethod
    def validate_attempts(cls, value: int) -> int:
        if value < 1 or value > 5:
            raise ValueError("LANGFLOW_MAX_ATTEMPTS must be between 1 and 5")
        return value

    def flow_id_for(self, agent_key: str) -> str:
        mapping = {
            "profile": self.langflow_profile_flow_id,
            "knowledge": self.langflow_knowledge_flow_id,
            "planning": self.langflow_planning_flow_id,
        }
        try:
            flow_id = mapping[agent_key]
        except KeyError as exc:
            raise ValueError(f"Unknown agent key: {agent_key}") from exc
        if not flow_id:
            raise RuntimeError(f"Langflow flow ID is not configured for agent '{agent_key}'")
        return flow_id


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
