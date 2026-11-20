from __future__ import annotations

from dataclasses import replace

import pytest

from claims_assistant.config import Settings
from claims_assistant.documents import load_chunks
from claims_assistant.retrieval import BM25Retriever
from claims_assistant.service import ClaimsService
from claims_assistant.tools import Toolbox


@pytest.fixture(scope="session")
def chunks():
    return load_chunks()


@pytest.fixture
def toolbox(chunks):
    return Toolbox(BM25Retriever(chunks), chunks)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return replace(Settings(), trace_dir=tmp_path / "traces", audit_log=tmp_path / "audit" / "decisions.jsonl")


@pytest.fixture
def service(settings) -> ClaimsService:
    return ClaimsService(settings)
