"""Configuration from environment variables only (12-factor), so the same image runs locally and in Kubernetes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    llm_backend: str = "offline"  # offline | azure_openai
    retriever: str = "bm25"  # bm25 | azure_search
    max_steps: int = 6
    top_k: int = 3
    trace_dir: Path | None = Path("traces")
    audit_log: Path = Path("audit/decisions.jsonl")

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None  # empty -> Microsoft Entra ID
    azure_openai_deployment: str = "gpt-4.1-mini"
    azure_openai_api_version: str = "2025-04-01-preview"

    azure_search_endpoint: str | None = None
    azure_search_api_key: str | None = None  # empty -> Microsoft Entra ID
    azure_search_index: str = "policy-clauses"
    azure_search_semantic_config: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        trace_dir = env("TRACE_DIR", "traces")
        return cls(
            llm_backend=env("LLM_BACKEND", "offline"),
            retriever=env("RETRIEVER", "bm25"),
            max_steps=int(env("MAX_STEPS", "6")),
            top_k=int(env("TOP_K", "3")),
            trace_dir=Path(trace_dir) if trace_dir else None,
            audit_log=Path(env("AUDIT_LOG", "audit/decisions.jsonl")),
            azure_openai_endpoint=env("AZURE_OPENAI_ENDPOINT") or None,
            azure_openai_api_key=env("AZURE_OPENAI_API_KEY") or None,
            azure_openai_deployment=env("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
            azure_openai_api_version=env("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            azure_search_endpoint=env("AZURE_SEARCH_ENDPOINT") or None,
            azure_search_api_key=env("AZURE_SEARCH_API_KEY") or None,
            azure_search_index=env("AZURE_SEARCH_INDEX", "policy-clauses"),
            azure_search_semantic_config=env("AZURE_SEARCH_SEMANTIC_CONFIG") or None,
        )
