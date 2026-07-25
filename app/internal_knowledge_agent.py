from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import logging
import re
import types
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from app.config import Settings
from app.schemas import A2ACommand, AgentResult

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_VECTOR_SIZE = 384


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    id: str
    text: str
    source: str
    title: str
    page: int | None = None


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: KnowledgeChunk
    score: float
    retriever: str


class KnowledgeState(TypedDict, total=False):
    command: A2ACommand
    query: str
    retrieved: list[RetrievedChunk]
    answer: str | None


class InternalKnowledgeAgent:
    """Small in-server Knowledge executor with local hybrid retrieval."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(self, command: A2ACommand) -> AgentResult:
        if _has_langgraph():
            try:
                return await self._run_with_langgraph(command)
            except Exception:  # noqa: BLE001
                logger.exception("Internal Knowledge LangGraph run failed; using fallback")
        return await self._run_linear(command)

    async def _run_with_langgraph(self, command: A2ACommand) -> AgentResult:
        langgraph_graph: Any = importlib.import_module("langgraph.graph")
        end = langgraph_graph.END
        state_graph = langgraph_graph.StateGraph

        graph = state_graph(KnowledgeState)

        async def retrieve(state: KnowledgeState) -> KnowledgeState:
            query = _query_from_command(state["command"])
            return {
                **state,
                "query": query,
                "retrieved": await asyncio.to_thread(self._retrieve, query),
            }

        async def generate(state: KnowledgeState) -> KnowledgeState:
            answer = await self._generate_answer(
                command=state["command"],
                query=state["query"],
                retrieved=state["retrieved"],
            )
            return {**state, "answer": answer}

        graph.add_node("retrieve", retrieve)
        graph.add_node("generate", generate)
        graph.set_entry_point("retrieve")
        graph.add_edge("retrieve", "generate")
        graph.add_edge("generate", end)
        state = await graph.compile().ainvoke({"command": command})
        return self._build_result(
            command=command,
            query=state["query"],
            retrieved=state["retrieved"],
            answer=state["answer"],
        )

    async def _run_linear(self, command: A2ACommand) -> AgentResult:
        query = _query_from_command(command)
        retrieved = await asyncio.to_thread(self._retrieve, query)
        answer = await self._generate_answer(
            command=command,
            query=query,
            retrieved=retrieved,
        )
        return self._build_result(
            command=command,
            query=query,
            retrieved=retrieved,
            answer=answer,
        )

    def _retrieve(self, query: str) -> list[RetrievedChunk]:
        chunks = _load_chunks(Path(self.settings.internal_knowledge_docs_path))
        if not chunks:
            return []

        ranked: dict[str, RetrievedChunk] = {}
        for result in self._retrieve_bm25(query, chunks):
            ranked[result.chunk.id] = result
        for result in self._retrieve_chroma(query, chunks):
            current = ranked.get(result.chunk.id)
            if current is None or result.score > current.score:
                ranked[result.chunk.id] = result

        return sorted(
            ranked.values(),
            key=lambda item: item.score,
            reverse=True,
        )[: self.settings.internal_knowledge_top_k]

    def _retrieve_bm25(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
    ) -> list[RetrievedChunk]:
        tokens = [_tokenize(chunk.text) for chunk in chunks]
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        try:
            rank_bm25: Any = importlib.import_module("rank_bm25")
        except ImportError:
            scores = [_lexical_score(query_tokens, chunk_tokens) for chunk_tokens in tokens]
        else:
            scores = list(rank_bm25.BM25Okapi(tokens).get_scores(query_tokens))

        if not any(score > 0 for score in scores):
            scores = [_lexical_score(query_tokens, chunk_tokens) for chunk_tokens in tokens]

        return [
            RetrievedChunk(chunk=chunk, score=float(score), retriever="bm25")
            for chunk, score in zip(chunks, scores, strict=True)
            if score > 0
        ]

    def _retrieve_chroma(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
    ) -> list[RetrievedChunk]:
        try:
            chromadb: Any = importlib.import_module("chromadb")
        except ImportError:
            return []

        try:
            client = chromadb.PersistentClient(
                path=self.settings.internal_knowledge_chroma_path
            )
            collection = client.get_or_create_collection(
                name=self.settings.internal_knowledge_chroma_collection,
                embedding_function=_HashEmbeddingFunction(),
            )
            existing = collection.get(ids=[chunk.id for chunk in chunks])
            existing_ids = set(existing.get("ids", []))
            missing = [chunk for chunk in chunks if chunk.id not in existing_ids]
            if missing:
                collection.add(
                    ids=[chunk.id for chunk in missing],
                    documents=[chunk.text for chunk in missing],
                    metadatas=[_chunk_metadata(chunk) for chunk in missing],
                )
            results = collection.query(
                query_texts=[query],
                n_results=min(self.settings.internal_knowledge_top_k, len(chunks)),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Internal Knowledge Chroma retrieval failed")
            return []

        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        by_id = {chunk.id: chunk for chunk in chunks}
        retrieved: list[RetrievedChunk] = []
        for chunk_id, distance in zip(ids, distances, strict=False):
            chunk = by_id.get(chunk_id)
            if chunk is not None:
                retrieved.append(
                    RetrievedChunk(
                        chunk=chunk,
                        score=1.0 / (1.0 + float(distance)),
                        retriever="chroma",
                    )
                )
        return retrieved

    async def _generate_answer(
        self,
        *,
        command: A2ACommand,
        query: str,
        retrieved: list[RetrievedChunk],
    ) -> str | None:
        if not retrieved:
            return None

        api_key = self.settings.google_api_key.get_secret_value()
        if not api_key:
            return _extractive_answer(command, retrieved)

        try:
            genai: Any = importlib.import_module("google.genai")
        except ImportError:
            logger.warning("google-genai is not installed; using extractive answer")
            return _extractive_answer(command, retrieved)

        prompt = _gemini_prompt(command=command, query=query, retrieved=retrieved)

        def call_gemini() -> str:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=self.settings.internal_knowledge_model,
                contents=prompt,
            )
            return str(getattr(response, "text", "") or "").strip()

        try:
            answer = await asyncio.to_thread(call_gemini)
        except Exception:  # noqa: BLE001
            logger.exception("Gemini generation failed; using extractive answer")
            return _extractive_answer(command, retrieved)
        return answer or _extractive_answer(command, retrieved)

    def _build_result(
        self,
        *,
        command: A2ACommand,
        query: str,
        retrieved: list[RetrievedChunk],
        answer: str | None,
    ) -> AgentResult:
        request = command.request
        evidence = [
            {
                "evidence_id": f"evidence-{index}",
                "claim_supported": _short_text(item.chunk.text, 220),
                "document_id": item.chunk.source,
                "document_title": item.chunk.title,
                "section": None,
                "page": str(item.chunk.page) if item.chunk.page is not None else None,
                "version": None,
                "effective_date": None,
                "url": None,
                "relevant_excerpt": _short_text(item.chunk.text, 360),
                "retriever": item.retriever,
                "score": round(item.score, 4),
            }
            for index, item in enumerate(retrieved, start=1)
        ]
        warnings: list[Any] = []
        if not retrieved:
            warnings.append(
                {
                    "code": "NO_RELEVANT_KNOWLEDGE_FOUND",
                    "message": (
                        "No local Knowledge documents matched the request. "
                        "Add approved company knowledge files to the configured docs path."
                    ),
                    "field": "INTERNAL_KNOWLEDGE_DOCS_PATH",
                }
            )

        data = {
            "knowledge_status": "SUPPORTED" if retrieved else "NOT_FOUND",
            "evidence_confidence": "MEDIUM" if retrieved else "NONE",
            "response_language": request.payload.get("requested_language")
            or request.locale,
            "knowledge_summary": answer,
            "question_category": _question_category(command),
            "direct_answer": answer if request.operation == "ANSWER_QUESTION" else None,
            "conditions": [],
            "exceptions": [],
            "mandatory_requirements": [],
            "recommended_practices": [],
            "constraints": [],
            "deadlines": [],
            "responsible_roles": [],
            "planning_guidance": [],
            "revision_assessment": [],
            "protected_requirements": [],
            "applicable_constraints": [],
            "trigger_analysis": None,
            "continuing_requirements": [],
            "adaptation_constraints": [],
            "documented_alternatives": [],
            "recommended_actions": [],
            "action_request_detected": False,
            "requested_action": None,
            "authorization_or_confirmation_required": False,
            "recommended_routing": None,
            "human_validation_required": False,
            "human_validation_reason": None,
            "missing_information": [] if retrieved else ["approved local knowledge"],
            "clarification_question": None,
            "evidence": evidence,
            "query": query,
        }

        return AgentResult(
            schema_version="1.0",
            status="SUCCEEDED",
            artifact_type="ONBOARDING_KNOWLEDGE_EVIDENCE",
            data=data,
            warnings=warnings,
            errors=[],
            metadata={
                "agent": "knowledge",
                "workflow": "internal",
                "model": self.settings.internal_knowledge_model,
                "skill_id": command.skill_id,
                "operation": request.operation,
                "request_id": request.request_id,
                "run_id": request.run_id,
                "correlation_id": request.correlation_id,
                "case_id": request.case_id,
                "employee_id": request.employee_id,
                "retrieved_result_count": len(retrieved),
                "used_evidence_count": len(evidence),
            },
        )


def _has_langgraph() -> bool:
    return importlib.util.find_spec("langgraph") is not None


def _load_chunks(path: Path) -> list[KnowledgeChunk]:
    if not path.exists():
        return []

    chunks: list[KnowledgeChunk] = []
    files = [
        file
        for file in sorted(path.rglob("*"))
        if file.is_file() and file.suffix.lower() in {".md", ".txt", ".json", ".pdf"}
    ]
    for file in files:
        chunks.extend(_chunks_from_file(file))
    return chunks


def _chunk_metadata(chunk: KnowledgeChunk) -> dict[str, str | int]:
    metadata: dict[str, str | int] = {
        "source": chunk.source,
        "title": chunk.title,
    }
    if chunk.page is not None:
        metadata["page"] = chunk.page
    return metadata


def _chunks_from_file(file: Path) -> list[KnowledgeChunk]:
    if file.suffix.lower() == ".pdf":
        return _chunks_from_pdf(file)

    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = file.read_text(encoding="utf-8", errors="ignore")
    return _chunks_from_text(text=text, file=file, page=None)


def _chunks_from_pdf(file: Path) -> list[KnowledgeChunk]:
    try:
        pypdf: types.ModuleType = importlib.import_module("pypdf")
    except ImportError:
        logger.warning("pypdf is not installed; skipping PDF knowledge file %s", file)
        return []

    try:
        reader = pypdf.PdfReader(str(file))
    except Exception:  # noqa: BLE001
        logger.exception("Unable to open PDF knowledge file %s", file)
        return []

    chunks: list[KnowledgeChunk] = []
    for page_number, page in enumerate(getattr(reader, "pages", []), start=1):
        try:
            text = str(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            logger.exception("Unable to extract page %s from PDF %s", page_number, file)
            continue
        chunks.extend(_chunks_from_text(text=text, file=file, page=page_number))
    return chunks


def _chunks_from_text(
    *,
    text: str,
    file: Path,
    page: int | None,
) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for index, part in enumerate(_split_text(text), start=1):
        chunk_id = hashlib.sha256(
            f"{file}:{page}:{index}:{part}".encode()
        ).hexdigest()[:24]
        chunks.append(
            KnowledgeChunk(
                id=chunk_id,
                text=part,
                source=str(file),
                title=file.stem.replace("_", " ").replace("-", " ").title(),
                page=page,
            )
        )
    return chunks


def _split_text(text: str, *, chunk_size: int = 1400) -> list[str]:
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not cleaned:
        return []
    return [
        cleaned[start : start + chunk_size]
        for start in range(0, len(cleaned), chunk_size)
    ]


def _query_from_command(command: A2ACommand) -> str:
    payload = command.request.payload
    parts: list[str] = [command.skill_id, command.request.operation]
    for key in (
        "question",
        "query",
        "role",
        "job_title",
        "job_family",
        "department",
        "location",
        "employment_type",
        "topic",
        "onboarding_scope",
    ):
        value = payload.get(key)
        if isinstance(value, str):
            parts.append(value)
    topics = payload.get("topics") or payload.get("requested_topics")
    if isinstance(topics, list):
        parts.extend(str(topic) for topic in topics)
    return " ".join(part for part in parts if part)


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text)]


def _lexical_score(query_tokens: list[str], chunk_tokens: list[str]) -> float:
    counts = Counter(chunk_tokens)
    return float(sum(counts[token] for token in query_tokens))


class _HashEmbeddingFunction:
    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return [_hash_embedding(text) for text in input]


def _hash_embedding(text: str) -> list[float]:
    vector = [0.0] * _VECTOR_SIZE
    for token in _tokenize(text):
        index = int(hashlib.sha256(token.encode()).hexdigest(), 16) % _VECTOR_SIZE
        vector[index] += 1.0
    norm = sum(value * value for value in vector) ** 0.5 or 1.0
    return [value / norm for value in vector]


def _gemini_prompt(
    *,
    command: A2ACommand,
    query: str,
    retrieved: list[RetrievedChunk],
) -> str:
    snippets = "\n\n".join(
        f"[evidence-{index}] {item.chunk.title}\n{item.chunk.text}"
        for index, item in enumerate(retrieved, start=1)
    )
    return (
        "You are the Knowledge Agent for employee onboarding. Answer only from "
        "the supplied evidence. If evidence is insufficient, say so. Keep the "
        "answer concise.\n\n"
        f"Skill: {command.skill_id}\n"
        f"Operation: {command.request.operation}\n"
        f"Query: {query}\n\n"
        f"Evidence:\n{snippets}"
    )


def _extractive_answer(command: A2ACommand, retrieved: list[RetrievedChunk]) -> str:
    prefix = (
        "Based on the local knowledge evidence, "
        if command.request.operation == "ANSWER_QUESTION"
        else "Relevant local onboarding knowledge was found: "
    )
    excerpts = "; ".join(_short_text(item.chunk.text, 180) for item in retrieved[:3])
    return prefix + excerpts


def _question_category(command: A2ACommand) -> str | None:
    if command.request.operation != "ANSWER_QUESTION":
        return None
    text = _query_from_command(command).lower()
    if "security" in text or "compliance" in text:
        return "SECURITY_OR_COMPLIANCE"
    if "training" in text:
        return "TRAINING"
    if "deadline" in text or "timeline" in text:
        return "DEADLINE_OR_TIMELINE"
    if "remote" in text or "policy" in text:
        return "POLICY_INFORMATION"
    return "OTHER"


def _short_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."
