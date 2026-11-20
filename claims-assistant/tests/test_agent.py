"""The agent loop and guardrails, driven by a scripted fake model that misbehaves on purpose."""

import json
from datetime import date

from claims_assistant.llm import LLMResponse, ToolCall
from claims_assistant.models import ClaimRequest, NextStep
from claims_assistant.service import ClaimsService

CLAIM = ClaimRequest(
    policy_number="MOT-2026-000001",
    product="motor",
    description="On 21.09.2026 I crashed into a fence after a few beers. Repair: 7 400 PLN. PESEL 44051401359.",
)


class ScriptedLLM:
    """Returns the given responses in order and records every conversation it was sent."""

    name = "scripted"

    def __init__(self, *responses: LLMResponse):
        self.responses = list(responses)
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools):
        self.seen.append(json.loads(json.dumps(messages)))
        return self.responses.pop(0)


def calls(*items) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=[ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(items)])


def final(**fields) -> LLMResponse:
    return LLMResponse(content=json.dumps(fields), model="scripted-model")


SEARCH = ("search_policy", {"query": "collision", "product": "motor"})
COVERAGE = ("check_coverage", {"product": "motor", "cause": "collision", "circumstances": ["alcohol"]})


def test_offline_happy_path_writes_trace_and_audit(service, settings):
    assessment, trace = service.assess(CLAIM)
    assert assessment.category == "motor_collision"
    assert assessment.next_step == NextStep.review_possible_exclusion
    assert [c.clause for c in assessment.citations] == ["MOT-2.1", "MOT-3.1"]
    assert (settings.trace_dir / f"{trace.trace_id}.json").exists()

    record = json.loads(settings.audit_log.read_text("utf-8").splitlines()[-1])
    assert record["next_step"] == "review_possible_exclusion"
    assert record["decided_by"] == "pending human review"
    assert "44051401359" not in settings.audit_log.read_text("utf-8")


def test_personal_data_never_reaches_the_model(settings):
    llm = ScriptedLLM(final(next_step="request_information", answer="x", citations=[]))
    ClaimsService(settings, llm=llm).assess(CLAIM)
    sent = json.dumps(llm.seen)
    assert "44051401359" not in sent and "MOT-2026-000001" not in sent
    assert "[PESEL]" in sent


def test_invented_citations_are_dropped(settings):
    llm = ScriptedLLM(
        calls(SEARCH),
        final(
            category="motor_collision",
            next_step="assign_adjuster",
            answer="See [MOT-9.9].",
            citations=["MOT-9.9", "MOT-2.1"],
        ),
    )
    assessment, _ = ClaimsService(settings, llm=llm).assess(CLAIM)
    assert [c.clause for c in assessment.citations] == ["MOT-2.1"]
    assert "dropped_unretrieved_citation" in assessment.guardrail_flags


def test_exclusion_overrides_the_models_next_step(settings):
    llm = ScriptedLLM(
        calls(SEARCH, COVERAGE),
        final(category="motor_collision", next_step="fast_track", answer="Pay it.", citations=["MOT-2.1"]),
    )
    assessment, _ = ClaimsService(settings, llm=llm).assess(CLAIM)
    assert assessment.next_step == NextStep.review_possible_exclusion
    assert "exclusion_overrides_next_step" in assessment.guardrail_flags


def test_tool_errors_go_back_to_the_model(settings):
    bad = ("check_coverage", {"product": "motor", "cause": "meteor", "circumstances": []})
    llm = ScriptedLLM(calls(bad), final(next_step="request_information", answer="x", citations=[]))
    _, trace = ClaimsService(settings, llm=llm).assess(CLAIM)
    assert trace.steps[0].tool_calls[0]["error"].startswith("Unknown cause")
    tool_message = llm.seen[1][-1]
    assert tool_message["role"] == "tool" and "Unknown cause" in tool_message["content"]


def test_invalid_json_gets_one_corrective_turn(settings):
    llm = ScriptedLLM(
        LLMResponse(content="Sure! Here is my answer."),
        final(category="motor_collision", next_step="request_information", answer="x", citations=[]),
    )
    assessment, trace = ClaimsService(settings, llm=llm).assess(CLAIM)
    assert len(trace.steps) == 2
    assert "invalid_json_retry" in assessment.guardrail_flags
    assert llm.seen[1][-1]["role"] == "user"


def test_step_limit_hands_the_claim_to_a_human(settings):
    from dataclasses import replace

    llm = ScriptedLLM(*[calls(SEARCH) for _ in range(3)])
    assessment, trace = ClaimsService(replace(settings, max_steps=3), llm=llm).assess(CLAIM)
    assert len(trace.steps) == 3
    assert "max_steps_reached" in assessment.guardrail_flags
    assert assessment.next_step != NextStep.fast_track


def test_trace_records_tokens_and_tool_calls(service):
    _, trace = service.assess(CLAIM)
    data = trace.to_dict()
    assert data["tokens"]["prompt"] > 0 and data["tokens"]["completion"] > 0
    assert [c["name"] for c in data["steps"][0]["tool_calls"]] == ["search_policy", "check_coverage"]
    assert data["prompt_version"] and data["pii_found"] == ["PESEL"]


def test_fast_track_needs_a_coverage_check(settings):
    llm = ScriptedLLM(final(category="motor_glass", next_step="fast_track", answer="Pay.", citations=[]))
    claim = CLAIM.model_copy(update={"incident_date": date(2026, 10, 3), "claimed_amount": 900})
    assessment, _ = ClaimsService(settings, llm=llm).assess(claim)
    assert assessment.next_step == NextStep.request_information
    assert {"coverage_not_checked", "fast_track_not_allowed"} <= set(assessment.guardrail_flags)


def test_fast_track_blocked_for_theft_and_large_amounts(settings):
    theft = ("check_coverage", {"product": "motor", "cause": "theft", "circumstances": []})
    llm = ScriptedLLM(calls(theft), final(category="motor_theft", next_step="fast_track", answer="x", citations=[]))
    claim = CLAIM.model_copy(update={"incident_date": date(2026, 11, 5), "claimed_amount": 42000})
    assessment, _ = ClaimsService(settings, llm=llm).assess(claim)
    assert assessment.next_step == NextStep.assign_adjuster
    assert "fast_track_not_allowed" in assessment.guardrail_flags


def test_payout_is_recomputed_from_the_claimed_amount(settings):
    glass = ("check_coverage", {"product": "motor", "cause": "glass", "circumstances": []})
    wrong = ("estimate_payout", {"product": "motor", "cause": "glass", "claimed_amount": 99})
    llm = ScriptedLLM(calls(glass), calls(wrong), final(next_step="fast_track", answer="x", citations=["MOT-2.3"]))
    claim = CLAIM.model_copy(update={"incident_date": date(2026, 10, 3), "claimed_amount": 1900})
    assessment, _ = ClaimsService(settings, llm=llm).assess(claim)
    assert assessment.estimated_payout == 1900
    assert assessment.next_step == NextStep.fast_track


def test_unknown_clause_ids_in_the_answer_are_neutralised(settings):
    llm = ScriptedLLM(
        calls(SEARCH), final(next_step="assign_adjuster", answer="See [MOT-2.1] and [MOT-9.9].", citations=[])
    )
    assessment, _ = ClaimsService(settings, llm=llm).assess(CLAIM)
    assert assessment.answer == "See [MOT-2.1] and [unverified]."
    assert "unretrieved_clause_in_answer" in assessment.guardrail_flags


def test_llm_errors_hand_the_claim_to_a_human(settings):
    class Failing:
        name = "failing"

        def chat(self, messages, tools):
            raise TimeoutError("upstream timeout")

    assessment, trace = ClaimsService(settings, llm=Failing()).assess(CLAIM)
    assert assessment.next_step == NextStep.request_information
    assert "llm_error:TimeoutError" in assessment.guardrail_flags
    assert trace.steps == []
