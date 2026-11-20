"""The agent loop, guardrails and per-step tracing."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .llm import LLMClient
from .models import Assessment, Citation, ClaimRequest, ExtractedFields, NextStep
from .pii import mask_pii
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT
from .tools import TOOL_SPECS, Toolbox, ToolError

_CLAUSE_REF = re.compile(r"\[([A-Z]{3}-\d+\.\d+)\]")
FINAL_KEYS = {"category", "incident_date", "claimed_amount", "damage_type", "next_step", "answer", "citations"}


@dataclass
class StepTrace:
    index: int
    duration_ms: int
    prompt_tokens: int
    completion_tokens: int
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RunTrace:
    trace_id: str
    started_at: str
    llm: str
    retriever: str
    prompt_version: str
    masked_input: dict[str, Any]
    pii_found: list[str]
    model: str = ""
    steps: list[StepTrace] = field(default_factory=list)
    duration_ms: int = 0
    outcome: dict[str, Any] = field(default_factory=dict)

    @property
    def tokens(self) -> dict[str, int]:
        return {
            "prompt": sum(s.prompt_tokens for s in self.steps),
            "completion": sum(s.completion_tokens for s in self.steps),
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"tokens": self.tokens}


class ClaimsAgent:
    def __init__(self, llm: LLMClient, toolbox_factory, max_steps: int = 6, trace_dir: Path | None = None):
        self.llm = llm
        self.toolbox_factory = toolbox_factory  # a fresh Toolbox per run keeps "what was retrieved" per claim
        self.max_steps = max_steps
        self.trace_dir = trace_dir

    def assess(self, claim: ClaimRequest) -> tuple[Assessment, RunTrace]:
        started = time.perf_counter()
        toolbox: Toolbox = self.toolbox_factory()
        masked_description, pii_found = mask_pii(claim.description)
        masked_claim = claim.model_dump(mode="json") | {
            "description": masked_description,
            "policy_number": "[POLICY_NO]",
        }
        trace = RunTrace(
            trace_id=uuid.uuid4().hex[:12],
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            llm=self.llm.name,
            retriever=toolbox.retriever.name,
            prompt_version=PROMPT_VERSION,
            masked_input=masked_claim,
            pii_found=pii_found,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(masked_claim)},
        ]

        draft: dict[str, Any] | None = None
        flags: list[str] = []
        corrected = False
        for index in range(1, self.max_steps + 1):
            step_start = time.perf_counter()
            try:
                response = self.llm.chat(messages, TOOL_SPECS)
            except Exception as error:  # timeout, rate limit, content filter: hand the claim to a human
                flags.append(f"llm_error:{type(error).__name__}")
                break
            trace.model = response.model
            step = StepTrace(index, 0, response.prompt_tokens, response.completion_tokens, response.content)

            if response.tool_calls:
                messages.append(_assistant_message(response.content, response.tool_calls))
                for call in response.tool_calls:
                    call_start = time.perf_counter()
                    try:
                        result, error = toolbox.call(call.name, call.arguments), None
                    except ToolError as exc:
                        result, error = {"error": str(exc)}, str(exc)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
                    step.tool_calls.append(
                        {
                            "name": call.name,
                            "arguments": call.arguments,
                            "result": result,
                            "error": error,
                            "duration_ms": _ms(call_start),
                        }
                    )
                step.duration_ms = _ms(step_start)
                trace.steps.append(step)
                continue

            step.duration_ms = _ms(step_start)
            trace.steps.append(step)
            draft = _parse_final(response.content)
            if draft is not None or corrected:
                break
            # One corrective turn per run: tell the model exactly what was wrong, then let the loop continue.
            corrected = True
            messages.append({"role": "assistant", "content": response.content or ""})
            messages.append({"role": "user", "content": f"Reply with one JSON object with keys: {sorted(FINAL_KEYS)}"})
            flags.append("invalid_json_retry")

        if draft is None:
            if not any(f.startswith("llm_error") for f in flags):
                flags.append("no_valid_answer" if corrected else "max_steps_reached")
            draft = {"next_step": "request_information", "answer": "The assistant could not finish; handle manually."}

        assessment = apply_guardrails(draft, toolbox, claim, trace.trace_id, flags)
        trace.duration_ms = _ms(started)
        trace.outcome = assessment.model_dump(mode="json")
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            (self.trace_dir / f"{trace.trace_id}.json").write_text(json.dumps(trace.to_dict(), indent=2), "utf-8")
        return assessment, trace


def apply_guardrails(
    draft: dict[str, Any], toolbox: Toolbox, claim: ClaimRequest, trace_id: str, flags: list[str]
) -> Assessment:
    """Deterministic checks on the model's draft.

    The model proposes; these rules decide what is allowed. The model may always choose a *more* cautious step,
    never a less cautious one than the coverage facts support.
    """
    flags = list(flags)

    # 1. Citations must point at clauses the tools actually returned - no invented sources.
    cited = [c for c in draft.get("citations") or [] if isinstance(c, str)]
    grounded = [c for c in dict.fromkeys(cited) if c in toolbox.retrieved]
    if len(grounded) < len(cited):
        flags.append("dropped_unretrieved_citation")
    if not grounded:
        flags.append("no_grounded_citation")
    answer = str(draft.get("answer", ""))
    unknown = [c for c in _CLAUSE_REF.findall(answer) if c not in toolbox.retrieved]
    if unknown:
        flags.append("unretrieved_clause_in_answer")
        answer = _CLAUSE_REF.sub(lambda m: m[0] if m[1] in toolbox.retrieved else "[unverified]", answer)

    # 2. Payout is recomputed from the claimed amount, never taken from the model.
    amount = claim.claimed_amount if claim.claimed_amount is not None else _float(draft.get("claimed_amount"))
    incident = claim.incident_date or _date(draft.get("incident_date"))
    coverage = toolbox.last_coverage
    payout = None
    if coverage and coverage["covered"] and amount is not None:
        estimate = toolbox.estimate_payout(coverage["product"], coverage["cause"], amount)
        payout = estimate["estimated_payout"]

    # 3. The next step the facts allow at most, compared with what the model asked for.
    if coverage is None:
        flags.append("coverage_not_checked")
        allowed = NextStep.request_information
    elif coverage["possible_exclusion"]:
        allowed = NextStep.review_possible_exclusion
    elif amount is None or incident is None:
        allowed = NextStep.request_information
    elif coverage["always_adjuster"] or payout is None or payout > toolbox.coverage["fast_track_limit"]:
        allowed = NextStep.assign_adjuster
    else:
        allowed = NextStep.fast_track
    step = _enum(NextStep, draft.get("next_step")) or NextStep.request_information
    if allowed == NextStep.review_possible_exclusion and step != allowed:
        flags.append("exclusion_overrides_next_step")
        step = allowed
    elif step == NextStep.fast_track and allowed != NextStep.fast_track:
        flags.append("fast_track_not_allowed")
        step = allowed

    return Assessment(
        trace_id=trace_id,
        category=draft.get("category"),
        fields=ExtractedFields(incident_date=incident, claimed_amount=amount, damage_type=draft.get("damage_type")),
        next_step=step,
        estimated_payout=payout,
        answer=answer,
        citations=[
            Citation(clause=c, section=toolbox.retrieved[c].section, quote=toolbox.retrieved[c].text) for c in grounded
        ],
        guardrail_flags=flags,
    )


def _parse_final(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) and "next_step" in value else None


def _assistant_message(content: str | None, calls) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in calls
        ],
    }


def _enum(enum, value):
    try:
        return enum(value)
    except ValueError:
        return None


def _float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _date(value):
    try:
        return ExtractedFields(incident_date=value, claimed_amount=None, damage_type=None).incident_date
    except ValidationError:
        return None


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
