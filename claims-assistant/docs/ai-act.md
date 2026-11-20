# EU AI Act — risk assessment and controls

> Working notes for an engineering team, not legal advice. The final classification belongs to the
> compliance and legal function.

## System

A decision-support tool for claims handlers (see [model-card.md](model-card.md)). It recommends a category,
extracted fields, cited policy terms and a next step. It does not decide on a claim and does not assess the
customer as a person.

## Classification

| Question | Answer for this system |
|---|---|
| Prohibited practice (Art. 5)? | No. No manipulation, social scoring or biometric categorisation. |
| High-risk under Annex III, point 5(c)? | 5(c) covers *risk assessment and pricing* for natural persons in **life and health** insurance. This system triages property, motor and travel claims and does neither, so it is **not covered**. |
| High-risk under Annex III, point 5(a)? | 5(a) covers eligibility for *essential public assistance benefits*, used by or for public authorities. A private insurer's claims are not public benefits, so it is **not covered** — but re-check if the system is ever used for statutory or public schemes. |
| Profiling of natural persons? | No. It reads the claim, not the customer's history or traits. If that changed, re-assess. |
| Transparency (Art. 50)? | Handlers see that the output is AI-generated (`disclaimer` field in every response). Customers do not interact with it. |
| AI literacy (Art. 4)? | **Required before use:** train handlers on what it does, its limits and how to override it. |

**Result:** not high-risk as designed. **Re-assess** before using it for health or life products, for
automated decisions without a human, or with customer profiling — each of those could move it into the high-risk
category, and fully automated decisions on individuals also raise GDPR Art. 22 questions.

## Controls

The obligations of high-risk systems are good engineering practice, so the design follows them voluntarily.
"Built" means it is in this repository; "Required" means it belongs to the integration or the rollout.

| Area (cf. AI Act articles for high-risk systems) | Status |
|---|---|
| Risk management (Art. 9) | **Built:** the AI never rejects; a possible exclusion always routes to `review_possible_exclusion`; `fast_track` is only allowed when coverage, amount, date and limits support it. |
| Data governance (Art. 10) | **Built:** synthetic, versioned policy and eval data; dataset hash logged with every eval run. **Required:** labelled, anonymised real claims before any production use. |
| Technical documentation (Art. 11) | **Built:** this file, the model card, the README and the versioned prompt. |
| Record-keeping (Art. 12) | **Built:** a trace per run (steps, tools, tokens, timing) and an append-only audit log (trace id, input fingerprint, model, prompt version, recommendation, citations, guardrail flags) without direct identifiers. Note: masked free text can still contain names, and a hash of personal data is still personal data under GDPR — retention and access rules apply. |
| Transparency to users (Art. 13) | **Built:** every answer cites the clauses it relies on, with quotes from the source text; disclaimer in every response. |
| Human oversight (Art. 14) | **Built:** recommendations only; audit records start as "pending human review". **Required:** the claims system shows the sources, lets the handler override every field and records the final decision against the trace id. |
| Accuracy and robustness (Art. 15) | **Built:** eval with thresholds as a CI gate; guardrails against invented citations and next steps the coverage facts do not support; claim text is treated as data. Not built: dedicated prompt-injection detection (e.g. Azure AI Content Safety Prompt Shields). |
| Privacy (GDPR) | **Built:** PESEL, policy numbers, e-mail, phone and IBAN/NRB are masked before the model call; keyless access via Entra ID / workload identity. **Required:** a DPIA (GDPR Art. 35), an Azure OpenAI deployment in an EU region / EU data zone, and NER-based masking of names and addresses. |
