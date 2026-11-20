"""Retrieval over policy clauses: a local BM25 index (default, offline) or Azure AI Search."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from .documents import Chunk

PRODUCTS = {"motor", "home", "travel"}


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


class Retriever(Protocol):
    name: str

    def search(self, query: str, top_k: int = 3, product: str | None = None) -> list[Hit]: ...


# --- local BM25 ------------------------------------------------------------------------------

_STOP = frozenset(
    {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "if", "in", "is", "it", "of", "on", "or"}
    | {"the", "to", "was", "were", "we", "with", "you", "your", "our", "this", "that"}
)


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [_stem(w) for w in words if w not in _STOP and len(w) > 1]


def _stem(word: str) -> str:
    """A deliberately tiny stemmer: enough to match plurals and simple verb forms ('pipes', 'leaking')."""
    for suffix in ("ation", "ion", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


class BM25Retriever:
    """Okapi BM25 over clause chunks. No network, no model download; good enough for short legal clauses."""

    name = "bm25"

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(f"{c.section} {c.section} {c.text}")) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = sum(self.lengths) / len(self.lengths)
        df = Counter(term for doc in self.docs for term in doc)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, top_k: int = 3, product: str | None = None) -> list[Hit]:
        terms = tokenize(query)
        hits = []
        for chunk, doc, length in zip(self.chunks, self.docs, self.lengths, strict=True):
            if product and chunk.product != product:
                continue
            score = 0.0
            for t in terms:
                tf = doc.get(t, 0)
                if tf:
                    norm = tf + self.k1 * (1 - self.b + self.b * length / self.avg_len)
                    score += self.idf[t] * tf * (self.k1 + 1) / norm
            if score > 0:
                hits.append(Hit(chunk, round(score, 4)))
        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]


# --- Azure AI Search -------------------------------------------------------------------------


class AzureSearchRetriever:
    """Same interface on Azure AI Search. Enabled with RETRIEVER=azure_search (see .env.example).

    Uses full-text search by default; set AZURE_SEARCH_SEMANTIC_CONFIG to re-rank with the semantic ranker.
    `upload` creates the index if needed and pushes the chunks (`make index-azure`).
    """

    name = "azure_search"

    def __init__(self, endpoint: str, index_name: str, api_key: str | None, semantic_config: str | None = None):
        from azure.search.documents import SearchClient

        self._endpoint, self._index_name = endpoint, index_name
        self._credential = _azure_credential(api_key)
        self._semantic_config = semantic_config
        self._client = SearchClient(endpoint, index_name, self._credential)

    def search(self, query: str, top_k: int = 3, product: str | None = None) -> list[Hit]:
        if product is not None and product not in PRODUCTS:  # never interpolate untrusted text into a filter
            raise ValueError(f"Unknown product: {product}")
        options: dict = {"top": top_k, "select": ["clause", "product", "document", "section", "text"]}
        if product:
            options["filter"] = f"product eq '{product}'"
        if self._semantic_config:
            options |= {"query_type": "semantic", "semantic_configuration_name": self._semantic_config}
        results = self._client.search(search_text=query, **options)
        return [
            Hit(
                Chunk(r["clause"], r["product"], r["document"], r["section"], r["text"]),
                float(r.get("@search.reranker_score") or r["@search.score"]),
            )
            for r in results
        ]

    def upload(self, chunks: list[Chunk]) -> None:
        """Creates or updates the index (with a semantic configuration if one is set) and uploads the chunks."""
        from azure.search.documents.indexes import SearchIndexClient
        from azure.search.documents.indexes.models import (
            SearchableField,
            SearchFieldDataType,
            SearchIndex,
            SemanticConfiguration,
            SemanticField,
            SemanticPrioritizedFields,
            SemanticSearch,
            SimpleField,
        )

        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="clause", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="product", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="document", type=SearchFieldDataType.String),
            SearchableField(name="section", type=SearchFieldDataType.String),
            SearchableField(name="text", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        ]
        semantic = None
        if self._semantic_config:
            semantic = SemanticSearch(
                default_configuration_name=self._semantic_config,
                configurations=[
                    SemanticConfiguration(
                        name=self._semantic_config,
                        prioritized_fields=SemanticPrioritizedFields(
                            title_field=SemanticField(field_name="section"),
                            content_fields=[SemanticField(field_name="text")],
                        ),
                    )
                ],
            )
        SearchIndexClient(self._endpoint, self._credential).create_or_update_index(
            SearchIndex(name=self._index_name, fields=fields, semantic_search=semantic)
        )
        results = self._client.upload_documents(
            [{"id": c.clause.replace(".", "_"), **c.__dict__} for c in chunks]  # keys allow no dots
        )
        failed = [r.key for r in results if not r.succeeded]
        if failed:
            raise RuntimeError(f"Azure AI Search rejected {len(failed)} documents: {failed}")


def _azure_credential(api_key: str | None):
    """API key if given, otherwise Microsoft Entra ID (managed identity in Azure, `az login` locally)."""
    if api_key:
        from azure.core.credentials import AzureKeyCredential

        return AzureKeyCredential(api_key)
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()
