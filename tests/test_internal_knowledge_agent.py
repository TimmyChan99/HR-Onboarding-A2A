from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from app.config import Settings
from app.internal_knowledge_agent import InternalKnowledgeAgent, _load_chunks
from app.schemas import A2ACommand


def knowledge_command(payload: dict[str, object] | None = None) -> A2ACommand:
    return A2ACommand(
        skill_id="answer_onboarding_question",
        request={
            "schema_version": "1.0",
            "operation": "ANSWER_QUESTION",
            "request_id": "req-knowledge",
            "run_id": "run-knowledge",
            "correlation_id": "case-1:req-knowledge",
            "employee_id": "emp-1",
            "case_id": "case-1",
            "payload": payload or {"question": "Can employees work remotely?"},
        },
    )


@pytest.mark.asyncio
async def test_internal_knowledge_agent_returns_supported_evidence(
    tmp_path: Path,
) -> None:
    docs_path = tmp_path / "knowledge"
    docs_path.mkdir()
    (docs_path / "hybrid-work.md").write_text(
        "Hybrid work policy allows eligible employees to work remotely up to "
        "two days per week. Remote setup security protocols must be followed.",
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        knowledge_agent_mode="internal",
        internal_knowledge_docs_path=str(docs_path),
        internal_knowledge_top_k=3,
        google_api_key="",
    )

    result = await InternalKnowledgeAgent(settings).run(knowledge_command())

    assert result.status == "SUCCEEDED"
    assert result.artifact_type == "ONBOARDING_KNOWLEDGE_EVIDENCE"
    assert result.data["knowledge_status"] == "SUPPORTED"
    assert result.data["evidence"]
    assert result.data["direct_answer"] is not None
    assert result.metadata["skill_id"] == "answer_onboarding_question"


@pytest.mark.asyncio
async def test_internal_knowledge_agent_handles_empty_local_knowledge(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        knowledge_agent_mode="internal",
        internal_knowledge_docs_path=str(tmp_path / "missing"),
        google_api_key="",
    )

    result = await InternalKnowledgeAgent(settings).run(knowledge_command())

    assert result.status == "SUCCEEDED"
    assert result.data["knowledge_status"] == "NOT_FOUND"
    assert result.data["evidence_confidence"] == "NONE"
    assert result.warnings[0].code == "NO_RELEVANT_KNOWLEDGE_FOUND"


def test_internal_knowledge_mode_skips_langflow_knowledge_readiness() -> None:
    settings = Settings(
        _env_file=None,
        langflow_execution_mode="webhook",
        knowledge_agent_mode="internal",
        langflow_profile_webhook_url="https://example.test/profile",
        langflow_planning_webhook_url="https://example.test/planning",
    )

    assert settings.missing_executor_agents() == []


def test_langflow_knowledge_mode_requires_knowledge_webhook() -> None:
    settings = Settings(
        _env_file=None,
        langflow_execution_mode="run_api",
        knowledge_agent_mode="langflow",
        langflow_profile_flow_id="profile-flow",
        langflow_knowledge_flow_id="knowledge-flow",
        langflow_planning_flow_id="planning-flow",
    )

    assert settings.missing_executor_agents() == ["knowledge"]


def test_internal_knowledge_loader_extracts_pdf_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePage:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class FakePdfReader:
        def __init__(self, _: str) -> None:
            self.pages = [
                FakePage("Remote work policy allows two days from home."),
                FakePage("Security training is mandatory during onboarding."),
            ]

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakePdfReader
    monkeypatch.setitem(sys.modules, "pypdf", fake_pypdf)

    docs_path = tmp_path / "knowledge"
    docs_path.mkdir()
    (docs_path / "enterprise-manual.pdf").write_bytes(b"%PDF fake")

    chunks = _load_chunks(docs_path)

    assert [chunk.page for chunk in chunks] == [1, 2]
    assert chunks[0].title == "Enterprise Manual"
    assert chunks[1].text == "Security training is mandatory during onboarding."
