"""The agent's tools: policy search (RAG), a coverage check and a payout estimate.

Coverage and payout are deterministic lookups on structured policy data, not LLM guesses:
the model decides *which* cause and circumstances apply, the code does the arithmetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .documents import POLICY_DIR, Chunk
from .retrieval import Retriever

PRODUCT_ENUM = ["motor", "home", "travel"]

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_policy",
            "description": "Search the policy terms. Returns the most relevant clauses with their ids (e.g. MOT-2.1).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for, in a few words."},
                    "product": {"type": "string", "enum": PRODUCT_ENUM},
                },
                "required": ["query", "product"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_coverage",
            "description": "Checks whether a cause of damage is covered and whether an exclusion may apply.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "enum": PRODUCT_ENUM},
                    "cause": {
                        "type": "string",
                        "description": "motor: collision|theft|glass|natural; home: fire|water|burglary|flood; "
                        "travel: medical|baggage|cancellation",
                    },
                    "circumstances": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Facts that may trigger an exclusion, e.g. alcohol, gradual_leak, "
                        "no_forced_entry, pre_existing_condition. Empty if none.",
                    },
                },
                "required": ["product", "cause", "circumstances"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_payout",
            "description": "Estimates the payout for a covered cause: claimed amount minus deductible, "
            "capped at the limit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "enum": PRODUCT_ENUM},
                    "cause": {"type": "string"},
                    "claimed_amount": {"type": "number", "description": "PLN"},
                },
                "required": ["product", "cause", "claimed_amount"],
                "additionalProperties": False,
            },
        },
    },
]


class ToolError(Exception):
    """Raised for bad tool input; the message goes back to the model so it can correct itself."""


class Toolbox:
    """One instance per claim, so it also records what the model has seen during that run."""

    def __init__(
        self,
        retriever: Retriever,
        chunks: list[Chunk],
        top_k: int = 3,
        coverage_file: Path = POLICY_DIR / "coverage.json",
    ):
        self.retriever = retriever
        self.chunks = {c.clause: c for c in chunks}
        self.top_k = top_k
        self.coverage = json.loads(coverage_file.read_text("utf-8"))
        self.searched: list[str] = []  # clause ids returned by search_policy, in order (for retrieval metrics)
        # What the tools returned during this run - the guardrails check the model's answer against it.
        self.retrieved: dict[str, Chunk] = {}  # clause id -> chunk the model has seen
        self.last_coverage: dict[str, Any] | None = None
        self.last_payout: dict[str, Any] | None = None

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = {
            "search_policy": self.search_policy,
            "check_coverage": self.check_coverage,
            "estimate_payout": self.estimate_payout,
        }.get(name)
        if handler is None:
            raise ToolError(f"Unknown tool: {name}")
        try:
            return handler(**arguments)
        except TypeError as error:  # missing or unexpected arguments
            raise ToolError(f"Invalid arguments for {name}: {error}") from error

    def search_policy(self, query: str, product: str | None = None) -> dict[str, Any]:
        hits = self.retriever.search(query, top_k=self.top_k, product=self._product(product) if product else None)
        for hit in hits:
            self.retrieved[hit.chunk.clause] = hit.chunk
            self.searched.append(hit.chunk.clause)
        return {
            "results": [
                {"clause": h.chunk.clause, "section": h.chunk.section, "text": h.chunk.text, "score": h.score}
                for h in hits
            ]
        }

    def check_coverage(self, product: str, cause: str, circumstances: list[str]) -> dict[str, Any]:
        terms = self._cause(product, cause)
        exclusions = self.coverage["products"][product]["exclusions"]
        hit = sorted(set(circumstances) & set(exclusions["circumstances"]))
        self.last_coverage = {
            "product": product,
            "cause": cause,
            "covered": not hit,
            "clause": terms["clause"],
            "deductible": terms["deductible"],
            "limit": terms["limit"],
            "required_documents": terms["documents"],
            "always_adjuster": terms.get("always_adjuster", False),
            "possible_exclusion": {"clause": exclusions["clause"], "circumstances": hit} if hit else None,
        }
        # The exclusion clause counts as seen: the model may cite it.
        if hit:
            self._remember(exclusions["clause"])
        self._remember(terms["clause"])
        return self.last_coverage

    def estimate_payout(self, product: str, cause: str, claimed_amount: float) -> dict[str, Any]:
        terms = self._cause(product, cause)
        if claimed_amount < 0:
            raise ToolError("claimed_amount must not be negative")
        payout = max(0.0, min(claimed_amount - terms["deductible"], terms["limit"]))
        self.last_payout = {
            "estimated_payout": round(payout, 2),
            "deductible": terms["deductible"],
            "limit": terms["limit"],
            "clause": terms["clause"],
            "fast_track_limit": self.coverage["fast_track_limit"],
        }
        return self.last_payout

    def _remember(self, clause: str) -> None:
        if clause not in self.retrieved and clause in self.chunks:
            self.retrieved[clause] = self.chunks[clause]

    def _product(self, product: str) -> str:
        if product not in self.coverage["products"]:
            raise ToolError(f"Unknown product '{product}'. Use one of: {', '.join(PRODUCT_ENUM)}")
        return product

    def _cause(self, product: str, cause: str) -> dict[str, Any]:
        causes = self.coverage["products"][self._product(product)]["causes"]
        if cause not in causes:
            raise ToolError(f"Unknown cause '{cause}' for {product}. Use one of: {', '.join(causes)}")
        return causes[cause]
