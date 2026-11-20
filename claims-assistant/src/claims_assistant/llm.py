"""LLM clients behind one small interface: Azure OpenAI, or a deterministic offline stand-in."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


class ContentFilteredError(RuntimeError):
    """The provider's safety filter blocked the output; the agent hands the claim to a human."""


class LLMClient(Protocol):
    name: str

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse: ...


# --- Azure OpenAI ----------------------------------------------------------------------------


class AzureOpenAIClient:
    """Chat Completions with tool calling on an Azure OpenAI deployment (LLM_BACKEND=azure_openai)."""

    name = "azure_openai"

    def __init__(self, endpoint: str, deployment: str, api_version: str, api_key: str | None = None):
        from openai import AzureOpenAI

        if api_key:
            self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version, timeout=30)
        else:  # keyless: Microsoft Entra ID (managed identity in Azure, `az login` locally)
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            token = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
            self._client = AzureOpenAI(
                azure_endpoint=endpoint, azure_ad_token_provider=token, api_version=api_version, timeout=30
            )
        self.deployment = deployment

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        response = self._client.chat.completions.create(
            model=self.deployment,
            messages=messages,
            response_format={"type": "json_object"},
            **({"tools": tools, "tool_choice": "auto"} if tools else {}),
        )
        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            raise ContentFilteredError("Azure content filter stopped the response")
        message = choice.message
        calls = [
            ToolCall(c.id, c.function.name, _parse_arguments(c.function.arguments))
            for c in (message.tool_calls or [])
            if c.type == "function"
        ]
        usage = response.usage
        return LLMResponse(
            content=message.content,
            tool_calls=calls,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            model=response.model,
        )


def _parse_arguments(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"__invalid_json__": raw}  # surfaces as a tool error the model can fix
    return value if isinstance(value, dict) else {"__invalid_json__": raw}


# --- offline stand-in ------------------------------------------------------------------------

_CAUSES = {
    "motor": {
        "theft": ["stolen", "theft", "stole"],
        "glass": ["windscreen", "windshield", "window", "glass", "chip", "crack"],
        "natural": ["hail", "flood", "storm", "tree", "branch", "lightning"],
        "collision": ["collid", "crash", "hit", "bumper", "accident", "revers", "rear-ended", "scrap"],
    },
    "home": {
        "fire": ["fire", "smoke", "burn", "explosion"],
        "burglary": ["burglar", "break-in", "broke in", "broken into", "stolen", "forced"],
        "flood": ["flood", "river", "heavy rain", "storm"],
        "water": ["pipe", "leak", "burst", "water", "neighbour", "ceiling"],
    },
    "travel": {
        "cancellation": ["cancel"],
        "baggage": ["luggage", "baggage", "suitcase"],
        "medical": ["hospital", "doctor", "clinic", "medical", "injur", "fever", "treatment"],
    },
}
_QUERIES = {
    "collision": "collision with a vehicle or object",
    "theft": "theft of the vehicle",
    "glass": "windscreen and window glass damage",
    "natural": "hail storm flood falling tree",
    "fire": "fire smoke explosion",
    "burglary": "burglary forced entry stolen property",
    "flood": "flood heavy rain storm",
    "water": "water damage burst pipe leak",
    "medical": "medical expenses abroad hospital",
    "baggage": "baggage lost by airline",
    "cancellation": "trip cancellation illness",
}
_PRODUCTS = {
    "motor": ["car", "vehicle", "drive", "parking", "windscreen"],
    "home": ["flat", "apartment", "house", "home", "kitchen", "ceiling"],
    "travel": ["trip", "abroad", "flight", "holiday", "hotel", "airline"],
}
_CIRCUMSTANCES = {
    "alcohol": ["alcohol", "drunk", "after drinking", "beers", "wine"],
    "gradual_leak": ["for months", "for weeks", "slowly", "gradual", "damp", "mould", "mold"],
    "no_forced_entry": ["no signs of forced", "unlocked", "left open", "without forcing", "no forced entry"],
    "pre_existing_condition": ["chronic", "pre-existing", "already diagnosed", "existing condition"],
    "no_licence": ["no licence", "without a licence", "no driving licence"],
}
_MONTHS = {date(2000, m, 1).strftime("%B").lower(): m for m in range(1, 13)}  # "january" -> 1 (C locale)


def _best(text: str, table: dict[str, list[str]]) -> str | None:
    scores = {key: sum(text.count(word) for word in words) for key, words in table.items()}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else None


def extract_date(text: str) -> date | None:
    """First date in ISO, dd.mm.yyyy or '5 November 2026' form; None if absent or impossible (31.02)."""
    patterns = [
        (r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
        (r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", lambda m: (int(m[3]), int(m[2]), int(m[1]))),
        (r"\b(\d{1,2}) (" + "|".join(_MONTHS) + r") (\d{4})\b", lambda m: (int(m[3]), _MONTHS[m[2]], int(m[1]))),
    ]
    for pattern, parts in patterns:
        if m := re.search(pattern, text.lower()):
            try:
                return date(*parts(m))
            except ValueError:
                return None
    return None


def extract_amount(text: str) -> float | None:
    """Amount in PLN: '3 850 PLN', '3,850 zł', '3.850 PLN' (Polish thousands), 'PLN 1200.50'."""
    number = r"(\d{1,3}(?:[ ,.]\d{3})+|\d+)(?:[.,](\d{1,2}))?"
    m = re.search(number + r"\s*(?:pln|zł|zl)\b", text.lower()) or re.search(r"\bpln\s*" + number, text.lower())
    if not m:
        return None
    whole = re.sub(r"[ ,.]", "", m[1])
    return float(f"{whole}.{m[2]}") if m[2] else float(whole)


class OfflineLLM:
    """A deterministic, rule-based stand-in for the LLM (the default, LLM_BACKEND=offline).

    It is not a language model. It speaks the same tool-calling protocol as AzureOpenAIClient - it reads the
    conversation, asks for tools and returns the same JSON - so the API, the agent loop, retrieval, guardrails,
    tracing and the evaluation all run end to end without keys, network or cost.
    """

    name = "offline"

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        claim = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
        text = claim["description"].lower()
        product = claim.get("product") or _best(text, _PRODUCTS) or "home"
        cause = _best(text, _CAUSES[product]) or next(iter(_CAUSES[product]))
        circumstances = [c for c, words in _CIRCUMSTANCES.items() if any(w in text for w in words)]
        amount = claim.get("claimed_amount") or extract_amount(claim["description"])
        incident = claim.get("incident_date") or (d.isoformat() if (d := extract_date(claim["description"])) else None)
        results = _tool_results(messages)

        if "check_coverage" not in results:
            # Like a real model: search with the customer's own words, plus the name of the cause.
            query = f"{_QUERIES[cause]}. {claim['description'][:300]}"
            return self._calls(
                messages,
                [
                    ("search_policy", {"query": query, "product": product}),
                    ("check_coverage", {"product": product, "cause": cause, "circumstances": circumstances}),
                ],
            )
        coverage = results["check_coverage"]
        if coverage.get("covered") and amount is not None and "estimate_payout" not in results:
            return self._calls(
                messages, [("estimate_payout", {"product": product, "cause": cause, "claimed_amount": amount})]
            )

        payout = results.get("estimate_payout", {}).get("estimated_payout")
        exclusion = coverage.get("possible_exclusion")
        clause = coverage.get("clause")
        citations = [c for c in [clause, exclusion and exclusion["clause"]] if c]

        if exclusion:
            step = "review_possible_exclusion"
            reasons = ", ".join(exclusion["circumstances"])
            answer = f"The cause is listed under [{clause}], but an exclusion may apply ({reasons})."
        elif incident is None or amount is None:
            step = "request_information"
            answer = f"The damage matches [{clause}]. The incident date or claimed amount is missing."
        elif coverage.get("always_adjuster") or (payout or 0) > results["estimate_payout"]["fast_track_limit"]:
            step = "assign_adjuster"
            answer = f"Covered under [{clause}]; estimated payout {payout:,.0f} PLN. This claim needs an adjuster."
        else:
            step = "fast_track"
            answer = f"Covered under [{clause}]; estimated payout {payout:,.0f} PLN after the deductible."
        final = {
            "category": f"{product}_{cause}",
            "incident_date": incident,
            "claimed_amount": amount,
            "damage_type": cause,
            "next_step": step,
            "answer": answer,
            "citations": citations,
        }
        return self._reply(messages, content=json.dumps(final))

    def _calls(self, messages: list[dict[str, Any]], calls: list[tuple[str, dict[str, Any]]]) -> LLMResponse:
        tool_calls = [ToolCall(f"call_{uuid.uuid4().hex[:12]}", name, args) for name, args in calls]
        return self._reply(messages, content=None, tool_calls=tool_calls)

    def _reply(self, messages, content: str | None, tool_calls: list[ToolCall] | None = None) -> LLMResponse:
        # Rough token estimate (~4 characters per token) so traces and eval cost metrics stay meaningful.
        out = content or json.dumps([c.arguments for c in tool_calls or []])
        return LLMResponse(
            content=content,
            tool_calls=tool_calls or [],
            prompt_tokens=len(json.dumps(messages)) // 4,
            completion_tokens=len(out) // 4,
            model="offline-rules",
        )


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest result per tool name, found by matching tool messages to the assistant's tool calls."""
    names = {
        c["id"]: c["function"]["name"] for m in messages if m["role"] == "assistant" for c in m.get("tool_calls", [])
    }
    return {names[m["tool_call_id"]]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}
