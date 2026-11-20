"""Offline evaluation of the claims assistant, logged to MLflow, with a quality gate.

    python evals/run_eval.py                 # offline stand-in + BM25, no keys needed
    LLM_BACKEND=azure_openai python evals/run_eval.py --judge   # real model + LLM-as-judge

Exit code 1 when a metric is below its threshold in evals/thresholds.json - that is the CI gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from claims_assistant.config import Settings
from claims_assistant.documents import DATA_DIR, POLICY_DIR
from claims_assistant.models import ClaimRequest
from claims_assistant.prompts import PROMPT_VERSION
from claims_assistant.service import ClaimsService, build_llm

ROOT = Path(__file__).resolve().parents[1]
DATASET = DATA_DIR / "eval" / "claims.jsonl"
THRESHOLDS = ROOT / "evals" / "thresholds.json"


def cause_clause(category: str) -> str | None:
    """The clause that describes the cause itself, e.g. motor_collision -> MOT-2.1 (what retrieval should find)."""
    coverage = json.loads((POLICY_DIR / "coverage.json").read_text("utf-8"))
    product, _, cause = category.partition("_")
    return coverage["products"].get(product, {}).get("causes", {}).get(cause, {}).get("clause")


def evaluate_case(service: ClaimsService, case: dict, judge=None) -> dict:
    claim = ClaimRequest.model_validate(case["request"])
    expected = case["expected"]
    assessment, trace = service.assess(claim)

    searched = [
        r["clause"]
        for step in trace.steps
        for call in step.tool_calls
        if call["name"] == "search_policy" and not call["error"]
        for r in call["result"]["results"]
    ]
    cited = [c.clause for c in assessment.citations]
    got_date = assessment.fields.incident_date.isoformat() if assessment.fields.incident_date else None
    got_amount = assessment.fields.claimed_amount
    exp_amount = expected["claimed_amount"]
    amount_ok = (got_amount is None and exp_amount is None) or (
        got_amount is not None and exp_amount is not None and abs(got_amount - exp_amount) < 0.01
    )
    # The labelled raw values (PESEL, phone, e-mail, ...) must not appear in anything sent to the model or traced.
    traced = json.dumps(trace.to_dict(), default=str, ensure_ascii=False)
    compact = traced.replace(" ", "")
    leak_free = not any(v in traced or v.replace(" ", "") in compact for v in expected.get("pii", []))

    result = {
        "id": case["id"],
        "category_ok": assessment.category == expected["category"],
        "date_ok": got_date == expected["incident_date"],
        "amount_ok": amount_ok,
        "next_step_ok": assessment.next_step == expected["next_step"],
        "grounded": expected["clause"] in cited and "dropped_unretrieved_citation" not in assessment.guardrail_flags,
        "retrieval_hit": cause_clause(expected["category"]) in searched,
        "pii_leak_free": leak_free,
        "got": {
            "category": assessment.category,
            "date": got_date,
            "amount": got_amount,
            "next_step": assessment.next_step.value,
            "citations": cited,
        },
        "expected": expected,
        "flags": assessment.guardrail_flags,
        "latency_ms": trace.duration_ms,
        "tokens": trace.tokens["prompt"] + trace.tokens["completion"],
        "steps": len(trace.steps),
        "trace_id": trace.trace_id,
    }
    if judge:
        result["judge"] = judge(assessment)
    return result


def summarize(results: list[dict]) -> dict[str, float]:
    def rate(key: str) -> float:
        return round(sum(r[key] for r in results) / len(results), 4)

    metrics = {
        "category_accuracy": rate("category_ok"),
        "field_accuracy": round((rate("date_ok") + rate("amount_ok")) / 2, 4),
        "next_step_accuracy": rate("next_step_ok"),
        "groundedness": rate("grounded"),
        "retrieval_hit_at_k": rate("retrieval_hit"),
        "pii_leak_free": rate("pii_leak_free"),
        "latency_ms_p50": statistics.median(r["latency_ms"] for r in results),
        "tokens_per_claim": round(statistics.mean(r["tokens"] for r in results), 1),
        "steps_per_claim": round(statistics.mean(r["steps"] for r in results), 2),
    }
    judged = [r["judge"]["score"] for r in results if "judge" in r]
    if judged:
        metrics["judge_faithfulness"] = round(statistics.mean(judged) / 5, 4)
    return metrics


def log_to_mlflow(settings: Settings, metrics: dict, results: list[dict], report: str) -> str | None:
    try:
        import mlflow
    except ImportError:
        print("mlflow not installed - skipping logging (pip install -e '.[eval]')")
        return None
    name = "claims-assistant-eval"
    if os.environ.get("MLFLOW_TRACKING_URI"):  # e.g. the server from docker compose
        if mlflow.get_experiment_by_name(name) is None:
            mlflow.create_experiment(name)
    else:  # local store next to the project: mlruns/mlflow.db + mlruns/artifacts
        store = ROOT / "mlruns"
        store.mkdir(exist_ok=True)
        mlflow.set_tracking_uri(f"sqlite:///{(store / 'mlflow.db').as_posix()}")
        if mlflow.get_experiment_by_name(name) is None:
            mlflow.create_experiment(name, artifact_location=(store / "artifacts").as_uri())
    mlflow.set_experiment(name)
    with mlflow.start_run(run_name=f"{settings.llm_backend}-{settings.retriever}-{PROMPT_VERSION}") as run:
        mlflow.log_params(
            {
                "llm_backend": settings.llm_backend,
                "retriever": settings.retriever,
                "top_k": settings.top_k,
                "max_steps": settings.max_steps,
                "prompt_version": PROMPT_VERSION,
                "dataset_sha": hashlib.sha256(DATASET.read_bytes()).hexdigest()[:12],
                "cases": len(results),
            }
        )
        mlflow.log_metrics(metrics)
        mlflow.log_text("\n".join(json.dumps(r, default=str) for r in results), "results.jsonl")
        mlflow.log_text(report, "report.md")
        return run.info.run_id


def make_judge(settings: Settings):
    """LLM-as-judge: rates whether the answer is supported by the quoted clauses (1-5)."""
    llm = build_llm(settings)

    def judge(assessment) -> dict:
        sources = "\n".join(f"[{c.clause}] {c.quote}" for c in assessment.citations) or "(no sources)"
        prompt = (
            "Rate from 1 to 5 how well the ANSWER is supported by the SOURCES only (5 = every claim is supported, "
            '1 = unsupported or contradicted). Reply as JSON: {"score": <1-5>, "reason": "<one sentence>"}.\n\n'
            f"SOURCES:\n{sources}\n\nANSWER:\n{assessment.answer}"
        )
        response = llm.chat([{"role": "user", "content": prompt}], tools=[])
        try:
            verdict = json.loads(response.content or "{}")
            return {"score": max(1, min(5, int(verdict["score"]))), "reason": verdict.get("reason", "")}
        except (ValueError, KeyError, TypeError):
            return {"score": 1, "reason": "unparseable judge output"}

    return judge


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", action="store_true", help="add LLM-as-judge (needs LLM_BACKEND=azure_openai)")
    parser.add_argument("--no-gate", action="store_true", help="report only, never fail")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="claims-eval-"))
    settings = replace(Settings.from_env(), trace_dir=workdir / "traces", audit_log=workdir / "audit.jsonl")
    if args.judge and settings.llm_backend == "offline":
        parser.error("--judge needs a real model: set LLM_BACKEND=azure_openai")

    service = ClaimsService(settings)
    cases = [json.loads(line) for line in DATASET.read_text("utf-8").splitlines() if line.strip()]
    judge = make_judge(settings) if args.judge else None
    results = [evaluate_case(service, case, judge) for case in cases]
    metrics = summarize(results)
    thresholds = {k: v for k, v in json.loads(THRESHOLDS.read_text("utf-8")).items() if not k.startswith("_")}

    lines = [f"# Eval: {settings.llm_backend} + {settings.retriever}, prompt {PROMPT_VERSION}, {len(cases)} cases", ""]
    lines += ["| metric | value | threshold |", "|---|---|---|"]
    failed = []
    for key, value in metrics.items():
        limit = thresholds.get(key)
        ok = limit is None or value >= limit
        if not ok:
            failed.append(key)
        lines.append(f"| {key} | {value} | {'' if limit is None else ('>= ' if ok else 'FAIL < ') + str(limit)} |")
    misses = [
        r for r in results if not all(r[k] for k in ("category_ok", "date_ok", "amount_ok", "next_step_ok", "grounded"))
    ]
    if misses:
        lines += ["", "## Misses", ""]
        lines += [f"- {r['id']}: got {r['got']} expected {r['expected']}" for r in misses]
    report = "\n".join(lines)
    print(report)

    run_id = log_to_mlflow(settings, metrics, results, report)
    if run_id:
        where = os.environ.get("MLFLOW_TRACKING_URI") or "mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db"
        print(f"\nMLflow run {run_id} ({where})")
    if failed and not args.no_gate:
        print(f"\nQUALITY GATE FAILED: {', '.join(failed)}")
        return 1
    print("\nQuality gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
