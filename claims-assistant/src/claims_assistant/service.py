"""Wires settings, retriever, LLM and agent together, and writes the audit log."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime

from .agent import ClaimsAgent, RunTrace
from .config import Settings
from .documents import load_chunks
from .llm import AzureOpenAIClient, LLMClient, OfflineLLM
from .models import Assessment, ClaimRequest
from .pii import fingerprint, mask_pii
from .prompts import PROMPT_VERSION
from .retrieval import AzureSearchRetriever, BM25Retriever, Retriever
from .tools import Toolbox


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_backend == "offline":
        return OfflineLLM()
    if settings.llm_backend == "azure_openai":
        if not settings.azure_openai_endpoint:
            raise ValueError("LLM_BACKEND=azure_openai needs AZURE_OPENAI_ENDPOINT")
        return AzureOpenAIClient(
            settings.azure_openai_endpoint,
            settings.azure_openai_deployment,
            settings.azure_openai_api_version,
            settings.azure_openai_api_key,
        )
    raise ValueError(f"Unknown LLM_BACKEND: {settings.llm_backend}")


def build_retriever(settings: Settings, chunks) -> Retriever:
    if settings.retriever == "bm25":
        return BM25Retriever(chunks)
    if settings.retriever == "azure_search":
        if not settings.azure_search_endpoint:
            raise ValueError("RETRIEVER=azure_search needs AZURE_SEARCH_ENDPOINT")
        return AzureSearchRetriever(
            settings.azure_search_endpoint,
            settings.azure_search_index,
            settings.azure_search_api_key,
            settings.azure_search_semantic_config,
        )
    raise ValueError(f"Unknown RETRIEVER: {settings.retriever}")


class ClaimsService:
    def __init__(self, settings: Settings, llm: LLMClient | None = None):
        self.settings = settings
        chunks = load_chunks()
        retriever = build_retriever(settings, chunks)
        self.agent = ClaimsAgent(
            llm or build_llm(settings),
            lambda: Toolbox(retriever, chunks, top_k=settings.top_k),
            max_steps=settings.max_steps,
            trace_dir=settings.trace_dir,
        )
        self._audit_lock = threading.Lock()

    def assess(self, claim: ClaimRequest) -> tuple[Assessment, RunTrace]:
        assessment, trace = self.agent.assess(claim)
        self._audit(claim, assessment, trace)
        return assessment, trace

    def _audit(self, claim: ClaimRequest, assessment: Assessment, trace: RunTrace) -> None:
        """Append-only record of every recommendation: what was decided, by which model and prompt, on what basis.

        It stores no personal data: the input is referenced by a hash, the text by its masked form.
        """
        record = {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "trace_id": trace.trace_id,
            "input_fingerprint": fingerprint(claim.model_dump_json()),
            "masked_description": mask_pii(claim.description)[0],
            "llm": trace.llm,
            "model": trace.model,
            "retriever": trace.retriever,
            "prompt_version": PROMPT_VERSION,
            "category": assessment.category,
            "next_step": assessment.next_step,
            "citations": [c.clause for c in assessment.citations],
            "guardrail_flags": assessment.guardrail_flags,
            "decided_by": "pending human review",
        }
        path = self.settings.audit_log
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_lock, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
