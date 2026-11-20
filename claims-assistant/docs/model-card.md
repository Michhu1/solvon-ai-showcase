# Model card — Claims triage assistant

| | |
|---|---|
| Version | 1.0.0, prompt `triage-v3` |
| Owner | Claims operations (business owner) + AI engineering (technical owner) |
| Type | LLM agent with retrieval (RAG) and deterministic tools; decision support only |
| Models | Azure OpenAI chat deployment (configurable, default `gpt-4.1-mini`); offline rule-based stand-in for tests and CI |
| Status | Demo on synthetic data. Not validated on real claims. |

## Intended use

Helps a claims handler with the first look at a new property, motor or travel claim:

- suggests a **category** (product and cause of damage),
- extracts the **incident date** and **claimed amount**,
- quotes the **policy clauses** that apply, with clause ids,
- recommends a **next step**: `fast_track`, `assign_adjuster`, `request_information` or
  `review_possible_exclusion`, with an estimated payout where the terms allow it.

**Out of scope:** approving, rejecting or paying claims; fraud scoring; any assessment of the customer as a person;
pricing or underwriting; life and health insurance risk assessment. The output is a recommendation; the handler
decides and can override every field.

## How it works

1. Personal data (PESEL, policy number, e-mail, phone, IBAN) is masked before anything is sent to the model.
2. The model calls `search_policy` (BM25 locally, Azure AI Search in Azure), `check_coverage` and
   `estimate_payout`. Coverage rules and payout arithmetic are deterministic code over structured policy data.
3. The model returns JSON; guardrails then (a) drop citations and neutralise clause ids in the answer that the
   tools did not return, (b) recompute the payout from the claimed amount, (c) force `review_possible_exclusion`
   when an exclusion may apply, (d) allow `fast_track` only when coverage was checked, the date and amount are
   known, the cause is not always inspected and the payout is within the fast-track limit.
4. Every run is traced (steps, tool calls, tokens, timing) and written to an append-only audit log without
   direct identifiers.

## Data

- Policy terms: three synthetic documents (`data/policies/`), 20 clauses, plus a structured coverage table.
- Evaluation set: 14 synthetic claims (`data/eval/claims.jsonl`) covering 10 of the 11 causes, three exclusions,
  missing fields, an inferred product, personal data in the text and one deliberately ambiguous date.
- No real customer data is used anywhere in this repository.

## Evaluation

`make eval` runs the set, logs parameters, metrics and per-case results to MLflow and enforces the thresholds in
`evals/thresholds.json` (also in CI).

| Metric | Meaning |
|---|---|
| `category_accuracy` | product and cause match |
| `field_accuracy` | mean of incident-date and amount exact match |
| `next_step_accuracy` | recommended step matches |
| `groundedness` | the labelled clause is cited and every citation came from a tool result |
| `retrieval_hit_at_k` | `search_policy` returned the clause for the cause in its top-k |
| `pii_leak_free` | none of the labelled raw values appears in what was sent to the model or traced |
| `judge_faithfulness` | optional LLM-as-judge: is the answer supported by the quoted clauses (real model only) |

Results with the offline stand-in (keyword rules written for this set) show that the pipeline works; they say
nothing about the quality of a real model or about generalisation. Before any use, the same set (extended with real, anonymised and labelled claims) must be run against the
chosen Azure OpenAI deployment and reviewed by the business owner.

## Known limitations

- Small synthetic eval set; metrics have wide uncertainty.
- The BM25 stemmer is minimal; Azure AI Search with semantic ranking is the production path.
- Relative dates ("yesterday") are not resolved; the date stays empty and the step becomes `request_information`.
- Amounts are assumed to be in PLN.
- PII masking is pattern-based; names and addresses are not detected (would need an NER model, e.g. Azure AI Language PII).

## Monitoring in production (proposed)

Track per prompt/model version: share of each next step, guardrail-flag rate, handler override rate,
groundedness on a weekly labelled sample, latency, tokens and cost per claim. Alert when the override rate or
flag rate moves outside its usual band; re-run the eval before every prompt or model change.
