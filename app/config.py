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
    log_dir: str = "logs"
    log_request_body_max_bytes: int = 4000
    log_response_body_max_bytes: int = 4000
    host: str = "0.0.0.0"
    port: int = 8080

    public_base_url: str = "http://localhost:8080"
    internal_base_url: str = "http://127.0.0.1:8080"

    a2a_api_key: SecretStr = Field(default=SecretStr("development-only-change-me"))
    a2a_api_key_header: str = "X-A2A-API-Key"
    mcp_bearer_token: SecretStr = Field(
        default=SecretStr("development-only-mcp-change-me")
    )
    executor_callback_bearer_token: SecretStr = Field(
        default=SecretStr("development-only-executor-callback-change-me")
    )

    database_url: str = "sqlite+aiosqlite:///./data/a2a_tasks.db"

    langflow_base_url: str = "https://stg-agentic.abafusion.ai"
    langflow_api_key: SecretStr = Field(default=SecretStr(""))
    langflow_api_key_header: str = "x-api-key"
    langflow_api_key_prefix: str = ""
    langflow_execution_mode: Literal["run_api", "webhook"] = "run_api"
    knowledge_agent_mode: Literal["langflow", "internal"] = "langflow"
    langflow_profile_flow_id: str = ""
    langflow_knowledge_flow_id: str = ""
    langflow_planning_flow_id: str = ""
    langflow_profile_webhook_url: str = ""
    langflow_knowledge_webhook_url: str = ""
    langflow_planning_webhook_url: str = ""
    langflow_timeout_seconds: float = 1000.0
    langflow_max_attempts: int = 3
    langflow_output_component: str = ""
    langflow_api_style: Literal["legacy", "wrapped", "auto"] = "auto"
    a2a_client_timeout_seconds: float = Field(
        default=1500.0,
        validation_alias="A2A_CLIENT_TIMEOUT_SECONDS",
    )
    dispatch_wait_seconds: float = 5.0
    dispatch_result_ttl_seconds: float = 900.0
    verify_tls: bool = True

    google_api_key: SecretStr = Field(default=SecretStr(""))
    internal_knowledge_model: str = "gemini-2.5-flash"
    internal_knowledge_docs_path: str = "knowledge"
    internal_knowledge_chroma_path: str = "data/knowledge_chroma"
    internal_knowledge_chroma_collection: str = "onboarding_knowledge"
    internal_knowledge_top_k: int = 5

    @field_validator(
        "public_base_url",
        "internal_base_url",
        "langflow_base_url",
        "langflow_profile_webhook_url",
        "langflow_knowledge_webhook_url",
        "langflow_planning_webhook_url",
    )
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("langflow_max_attempts")
    @classmethod
    def validate_attempts(cls, value: int) -> int:
        if value < 1 or value > 5:
            raise ValueError("LANGFLOW_MAX_ATTEMPTS must be between 1 and 5")
        return value

    @field_validator("log_request_body_max_bytes", "log_response_body_max_bytes")
    @classmethod
    def validate_log_body_limit(cls, value: int) -> int:
        if value < 0 or value > 100_000:
            raise ValueError("Log body preview limits must be between 0 and 100000")
        return value

    @field_validator("internal_knowledge_top_k")
    @classmethod
    def validate_internal_knowledge_top_k(cls, value: int) -> int:
        if value < 1 or value > 20:
            raise ValueError("INTERNAL_KNOWLEDGE_TOP_K must be between 1 and 20")
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

    def webhook_url_for(self, agent_key: str) -> str:
        mapping = {
            "profile": self.langflow_profile_webhook_url,
            "knowledge": self.langflow_knowledge_webhook_url,
            "planning": self.langflow_planning_webhook_url,
        }
        try:
            webhook_url = mapping[agent_key]
        except KeyError as exc:
            raise ValueError(f"Unknown agent key: {agent_key}") from exc
        if not webhook_url:
            raise RuntimeError(
                f"Langflow webhook URL is not configured for agent '{agent_key}'"
            )
        return webhook_url

    def missing_executor_agents(self) -> list[str]:
        configured = (
            self.webhook_url_for
            if self.langflow_execution_mode == "webhook"
            else self.flow_id_for
        )
        missing: list[str] = []
        for agent_key in ("profile", "knowledge", "planning"):
            if agent_key == "knowledge" and self.knowledge_agent_mode == "internal":
                continue
            if agent_key == "knowledge" and self.knowledge_agent_mode == "langflow":
                try:
                    self.webhook_url_for(agent_key)
                except RuntimeError:
                    missing.append(agent_key)
                continue
            try:
                configured(agent_key)
            except RuntimeError:
                missing.append(agent_key)
        return missing

    def executor_callback_url_for(self, agent_key: str) -> str:
        if agent_key not in {"profile", "knowledge", "planning"}:
            raise ValueError(f"Unknown agent key: {agent_key}")
        return f"{self.public_base_url}/executors/{agent_key}/callback"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
