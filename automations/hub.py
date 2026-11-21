"""Claims Operations Hub - workflow definitions for Solvon (n8n-compatible workflow JSON).

Everything is generated from Python so the whole system is reviewable as code and reproducible:
`python automations/deploy.py` creates the data tables and workflows on a Solvon instance and publishes them.
"""

from __future__ import annotations

import uuid
from typing import Any

# --------------------------------------------------------------------------------------------- helpers


class Flow:
    """Small builder for n8n-compatible workflows: nodes, connections, sticky-note sections."""

    def __init__(self, name: str):
        self.name = name
        self.nodes: list[dict[str, Any]] = []
        self.connections: dict[str, dict[str, list[list[dict[str, Any]]]]] = {}

    def add(self, name: str, kind: str, version: float, params: dict, x: int, y: int, **extra) -> str:
        self.nodes.append(
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "type": f"n8n-nodes-base.{kind}",
                "typeVersion": version,
                "position": [x, y],
                "parameters": params,
                **extra,
            }
        )
        return name

    def link(self, a: str, b: str, out: int = 0, inp: int = 0) -> None:
        outputs = self.connections.setdefault(a, {}).setdefault("main", [])
        while len(outputs) <= out:
            outputs.append([])
        outputs[out].append({"node": b, "type": "main", "index": inp})

    def chain(self, *names: str) -> None:
        for a, b in zip(names, names[1:], strict=False):
            self.link(a, b)

    def section(self, title: str, text: str, x: int, y: int, w: int, h: int, color: int) -> None:
        self.add(
            f"Section · {title}",
            "stickyNote",
            1,
            {"content": f"## {title}\n{text}", "width": w, "height": h, "color": color},
            x,
            y,
        )

    def to_dict(self, settings: dict | None = None) -> dict[str, Any]:
        return {
            "name": self.name,
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": {"executionOrder": "v1", "timezone": "Europe/Warsaw", **(settings or {})},
        }


def cond(left: str, kind: str, op: str, right: Any = None) -> dict:
    c = {"id": str(uuid.uuid4()), "leftValue": left, "operator": {"type": kind, "operation": op}}
    if right is None:
        c["operator"]["singleValue"] = True
        c["rightValue"] = ""
    else:
        c["rightValue"] = right
    return c


def conditions(*items: dict, combinator: str = "and") -> dict:
    return {
        "options": {"caseSensitive": False, "leftValue": "", "typeValidation": "loose", "version": 2},
        "conditions": list(items),
        "combinator": combinator,
    }


def if_params(*items: dict, combinator: str = "and") -> dict:
    return {"conditions": conditions(*items, combinator=combinator), "looseTypeValidation": True, "options": {}}


def switch_params(rules: list[tuple[str, dict]]) -> dict:
    return {
        "rules": {
            "values": [{"conditions": conditions(c), "renameOutput": True, "outputKey": key} for key, c in rules]
        },
        "options": {},
    }


def set_params(fields: list[tuple[str, str, str]], keep: bool = True) -> dict:
    return {
        "mode": "manual",
        "assignments": {
            "assignments": [{"id": str(uuid.uuid4()), "name": n, "value": v, "type": t} for n, v, t in fields]
        },
        "includeOtherFields": keep,
        "options": {},
    }


def table_ref(table_id: str) -> dict:
    return {"__rl": True, "value": table_id, "mode": "id"}


def dt_insert(table_id: str, values: dict[str, str]) -> dict:
    return {
        "operation": "insert",
        "dataTableId": table_ref(table_id),
        "columns": {"mappingMode": "defineBelow", "value": values, "matchingColumns": [], "schema": []},
        "options": {},
    }


def dt_filter(*conds: tuple) -> dict:
    out = []
    for c in conds:
        item = {"keyName": c[0], "condition": c[1]}
        if len(c) > 2:
            item["keyValue"] = c[2]
        out.append(item)
    return {"matchType": "allConditions", "filters": {"conditions": out}}


def dt_update(table_id: str, filt: dict, values: dict[str, str]) -> dict:
    return {
        "operation": "update",
        "dataTableId": table_ref(table_id),
        **filt,
        "columns": {"mappingMode": "defineBelow", "value": values, "matchingColumns": [], "schema": []},
        "options": {},
    }


def dt_get(table_id: str, filt: dict | None = None) -> dict:
    return {"operation": "get", "dataTableId": table_ref(table_id), **(filt or {}), "returnAll": True, "options": {}}


def dt_op(table_id: str, operation: str, filt: dict) -> dict:
    return {"operation": operation, "dataTableId": table_ref(table_id), **filt, "options": {}}


def run_sub(workflow_id: str) -> dict:
    return {
        "workflowId": {"__rl": True, "value": workflow_id, "mode": "id"},
        "workflowInputs": {
            "mappingMode": "defineBelow",
            "value": {},
            "matchingColumns": [],
            "schema": [],
            "attemptToConvertTypes": False,
            "convertFieldsToString": True,
        },
        "mode": "once",
        "options": {"waitForSubWorkflow": True},
    }


def respond(code: int, body: str) -> dict:
    return {"respondWith": "json", "responseBody": body, "options": {"responseCode": code}}


def outbox(t: dict, channel: str, recipient: str, subject: str, body: str) -> dict:
    return dt_insert(
        t["hub_outbox"],
        {
            "channel": channel,
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "created_at": "={{ $now.toISO() }}",
        },
    )


# --------------------------------------------------------------------------------------------- tables

TABLES: dict[str, list[tuple[str, str]]] = {
    "hub_claims": [
        ("claim_id", "string"),
        ("policy_ref", "string"),
        ("product", "string"),
        ("category", "string"),
        ("next_step", "string"),
        ("status", "string"),
        ("priority", "string"),
        ("risk_score", "number"),
        ("amount", "number"),
        ("payout", "number"),
        ("incident_date", "date"),
        ("received_at", "date"),
        ("sla_due", "date"),
        ("assignee", "string"),
        ("channel", "string"),
        ("customer_email", "string"),
    ],
    "hub_adjusters": [
        ("name", "string"),
        ("region", "string"),
        ("skill", "string"),
        ("open_tasks", "number"),
        ("active", "boolean"),
    ],
    "hub_tasks": [
        ("claim_id", "string"),
        ("type", "string"),
        ("assignee", "string"),
        ("due_at", "date"),
        ("status", "string"),
        ("escalated", "boolean"),
    ],
    "hub_decisions": [("claim_id", "string"), ("decision", "string"), ("note", "string"), ("decided_at", "date")],
    "hub_kpi": [
        ("report_date", "date"),
        ("claims", "number"),
        ("fast_track", "number"),
        ("adjuster", "number"),
        ("info_requested", "number"),
        ("exclusion_review", "number"),
        ("total_claimed", "number"),
        ("total_payout", "number"),
    ],
    "hub_outbox": [
        ("channel", "string"),
        ("recipient", "string"),
        ("subject", "string"),
        ("body", "string"),
        ("created_at", "date"),
    ],
    "hub_incidents": [
        ("workflow", "string"),
        ("node", "string"),
        ("message", "string"),
        ("execution_id", "string"),
        ("at", "date"),
    ],
}

ADJUSTERS = [
    {"name": "Ola (motor, North)", "region": "north", "skill": "motor", "open_tasks": 2, "active": True},
    {"name": "Piotr (motor, South)", "region": "south", "skill": "motor", "open_tasks": 0, "active": True},
    {"name": "Kasia (home, North)", "region": "north", "skill": "home", "open_tasks": 1, "active": True},
    {"name": "Marek (home, South)", "region": "south", "skill": "home", "open_tasks": 3, "active": True},
    {"name": "Ewa (travel, all)", "region": "all", "skill": "travel", "open_tasks": 0, "active": True},
    {"name": "Tomek (travel, all)", "region": "all", "skill": "travel", "open_tasks": 4, "active": False},
]

# --------------------------------------------------------------------------------------------- workflows


def error_handler(t: dict) -> Flow:
    f = Flow("Claims Hub · 7 · Global error handler")
    f.section(
        "Incident capture",
        "Every Claims Hub workflow points here as its **error workflow**.\nFailures are logged and on-call is alerted.",
        40,
        120,
        960,
        420,
        3,
    )
    f.add("On workflow error", "errorTrigger", 1, {}, 100, 300)
    f.add(
        "Describe incident",
        "set",
        3.4,
        set_params(
            [
                ("workflow", "={{ $json.workflow.name }}", "string"),
                ("node", "={{ $json.execution.lastNodeExecuted }}", "string"),
                ("message", "={{ $json.execution.error.message }}", "string"),
                ("execution_id", "={{ $json.execution.id }}", "string"),
            ],
            keep=False,
        ),
        320,
        300,
    )
    f.add(
        "Log incident",
        "dataTable",
        1.1,
        dt_insert(
            t["hub_incidents"],
            {
                "workflow": "={{ $json.workflow }}",
                "node": "={{ $json.node }}",
                "message": "={{ $json.message }}",
                "execution_id": "={{ $json.execution_id }}",
                "at": "={{ $now.toISO() }}",
            },
        ),
        540,
        300,
    )
    f.add(
        "Alert on-call",
        "dataTable",
        1.1,
        outbox(
            t,
            "pager",
            "on-call@claims.example",
            "=Incident in {{ $('Describe incident').item.json.workflow }}",
            "={{ $('Describe incident').item.json.node }}: {{ $('Describe incident').item.json.message }}",
        ),
        760,
        300,
    )
    f.chain("On workflow error", "Describe incident", "Log incident", "Alert on-call")
    return f


def risk_engine(t: dict) -> Flow:
    f = Flow("Claims Hub · 3 · Risk & SLA engine")
    f.section("Input", "Called by the intake flows with one claim.", 40, 120, 300, 380, 7)
    f.section("Risk scoring", "Rule-based score: amount band, risky causes, late reporting.", 360, 120, 680, 380, 6)
    f.section("Priority & SLA", "Priority decides the SLA; due date = received + SLA hours.", 1060, 120, 1120, 680, 5)
    f.add("When called by intake", "executeWorkflowTrigger", 1.3, {"inputSource": "passthrough"}, 100, 300)
    f.add(
        "Risk factors",
        "set",
        3.4,
        set_params(
            [
                ("amount_points", "={{ $json.amount > 20000 ? 40 : ($json.amount > 5000 ? 20 : 5) }}", "number"),
                (
                    "cause_points",
                    "={{ /theft|stolen|fire|burglar|break-in/i.test($json.description) ? 25 : 0 }}",
                    "number",
                ),
                (
                    "late_points",
                    "={{ $json.incident_date && DateTime.fromISO($json.received_at).diff(DateTime.fromISO($json.incident_date), 'days').days > 14 ? 15 : 0 }}",
                    "number",
                ),
            ]
        ),
        420,
        300,
    )
    f.add(
        "Risk score",
        "set",
        3.4,
        set_params(
            [
                ("risk_score", "={{ $json.amount_points + $json.cause_points + $json.late_points }}", "number"),
            ]
        ),
        640,
        300,
    )
    f.add(
        "Priority",
        "switch",
        3.5,
        switch_params(
            [
                ("High", cond("={{ $json.risk_score }}", "number", "gte", 50)),
                ("Medium", cond("={{ $json.risk_score }}", "number", "gte", 25)),
                ("Low", cond("={{ $json.risk_score }}", "number", "lt", 25)),
            ]
        ),
        1100,
        300,
    )
    f.add("SLA 24h", "set", 3.4, set_params([("priority", "high", "string"), ("sla_hours", "24", "number")]), 1340, 160)
    f.add(
        "SLA 72h", "set", 3.4, set_params([("priority", "medium", "string"), ("sla_hours", "72", "number")]), 1340, 360
    )
    f.add(
        "SLA 120h", "set", 3.4, set_params([("priority", "low", "string"), ("sla_hours", "120", "number")]), 1340, 560
    )
    f.add("Join priorities", "merge", 3.2, {"mode": "append", "numberInputs": 3}, 1580, 360)
    f.add(
        "Compute due date",
        "dateTime",
        2,
        {
            "operation": "addToDate",
            "magnitude": "={{ $json.received_at }}",
            "timeUnit": "hours",
            "duration": "={{ $json.sla_hours }}",
            "outputFieldName": "sla_due",
            "options": {"includeInputFields": True},
        },
        1800,
        360,
    )
    f.add(
        "Engine output",
        "set",
        3.4,
        set_params([("sla_due", "={{ DateTime.fromISO($json.sla_due).toISO() }}", "string")]),
        2020,
        360,
    )
    f.chain("When called by intake", "Risk factors", "Risk score", "Priority")
    for i, n in enumerate(["SLA 24h", "SLA 72h", "SLA 120h"]):
        f.link("Priority", n, out=i)
        f.link(n, "Join priorities", inp=i)
    f.chain("Join priorities", "Compute due date", "Engine output")
    return f


def assign_adjuster(t: dict) -> Flow:
    f = Flow("Claims Hub · 4 · Assign adjuster (round robin)")
    f.section("Input", "One claim that needs a human adjuster.", 40, 120, 300, 520, 7)
    f.section(
        "Pick the least busy adjuster",
        "Active adjusters with the right skill, fewest open tasks first.",
        360,
        120,
        900,
        520,
        4,
    )
    f.section(
        "Book the workload",
        "Increase the adjuster's open tasks; attach the assignee to the claim.",
        1280,
        120,
        700,
        520,
        2,
    )
    f.add("When claim needs adjuster", "executeWorkflowTrigger", 1.3, {"inputSource": "passthrough"}, 100, 360)
    f.add(
        "Load adjusters",
        "dataTable",
        1.1,
        dt_get(t["hub_adjusters"], dt_filter(("active", "isTrue"), ("skill", "eq", "={{ $json.product }}"))),
        420,
        240,
        executeOnce=True,
    )
    f.add(
        "Least busy first",
        "sort",
        1,
        {"sortFieldsUi": {"sortField": [{"fieldName": "open_tasks", "order": "ascending"}]}, "options": {}},
        640,
        240,
    )
    f.add("Pick one", "limit", 1, {"maxItems": 1, "keep": "firstItems"}, 860, 240)
    f.add("Assignee", "set", 3.4, set_params([("assignee", "={{ $json.name }}", "string")], keep=False), 1080, 240)
    f.add(
        "Book workload",
        "dataTable",
        1.1,
        dt_update(
            t["hub_adjusters"],
            dt_filter(("name", "eq", "={{ $('Pick one').item.json.name }}")),
            {"open_tasks": "={{ $('Pick one').item.json.open_tasks + 1 }}"},
        ),
        1400,
        260,
    )
    f.add(
        "Attach assignee to claim",
        "merge",
        3.2,
        {"mode": "combine", "combineBy": "combineByPosition", "options": {}},
        1640,
        460,
    )
    f.chain("When claim needs adjuster", "Load adjusters", "Least busy first", "Pick one", "Assignee")
    f.link("Pick one", "Book workload")
    f.link("When claim needs adjuster", "Attach assignee to claim", inp=0)
    f.link("Assignee", "Attach assignee to claim", inp=1)
    return f


CLAIM_COLUMNS = {
    "claim_id": "={{ $json.claim_id }}",
    "policy_ref": "={{ $json.policy_ref }}",
    "product": "={{ $json.product }}",
    "category": "={{ $json.category }}",
    "next_step": "={{ $json.next_step }}",
    "status": "={{ $json.status }}",
    "priority": "={{ $json.priority }}",
    "risk_score": "={{ $json.risk_score }}",
    "amount": "={{ $json.amount }}",
    "payout": "={{ $json.payout }}",
    "incident_date": "={{ $json.incident_date || null }}",
    "received_at": "={{ $json.received_at }}",
    "sla_due": "={{ $json.sla_due }}",
    "assignee": "={{ $json.assignee || '' }}",
    "channel": "={{ $json.channel }}",
    "customer_email": "={{ $json.customer_email || '' }}",
}


def intake(t: dict, wf: dict) -> Flow:
    f = Flow("Claims Hub · 1 · Intake, routing & human review")
    o = t | {"outbox": t["hub_outbox"]}
    # sections (x, y, w, h)
    f.section(
        "1 · Intake",
        "`POST /webhook/claims-hub/intake`\nAI triage result from the claims assistant.",
        0,
        0,
        400,
        1460,
        7,
    )
    f.section(
        "2 · Validate",
        "Schema check, sanity check, duplicate check.\nBad input -> 422, duplicate -> 409, impossible amount -> error workflow.",
        420,
        0,
        900,
        1460,
        3,
    )
    f.section(
        "3 · Enrich", "Sub-workflow **Risk & SLA engine**: risk score, priority, due date.", 1340, 0, 700, 1460, 6
    )
    f.section("4 · Router", "One lane per recommended next step.", 2060, 0, 300, 1460, 1)
    f.section(
        "Lane A · Fast track",
        "Small, low-risk, covered claims are queued for payout.\nAnything else falls back to an adjuster.",
        2380,
        0,
        1180,
        300,
        4,
    )
    f.section(
        "Lane B · Adjuster",
        "Sub-workflow **Assign adjuster** (round robin), task + notification.",
        2380,
        320,
        1440,
        300,
        5,
    )
    f.section(
        "Lane C · Missing information",
        "Respond now, then **wait** until the customer completes a form.",
        2380,
        640,
        1620,
        300,
        2,
    )
    f.section(
        "Lane D · Possible exclusion -> human decision",
        "The AI never rejects. A handler decides in a form; the execution waits.",
        2380,
        960,
        2280,
        480,
        3,
    )
    f.section("5 · Close", "Collect lanes A+B, log, count time saved, respond 201.", 3840, 0, 820, 620, 4)

    f.add(
        "Claim from AI triage",
        "webhook",
        2.2,
        {"httpMethod": "POST", "path": "claims-hub/intake", "responseMode": "responseNode", "options": {}},
        120,
        560,
    )
    f.add(
        "Normalize payload",
        "set",
        3.4,
        set_params(
            [
                ("claim_id", "={{ String($json.body.claim_id || '').trim() }}", "string"),
                ("policy_ref", "={{ $json.body.policy_ref || '[POLICY_NO]' }}", "string"),
                ("product", "={{ String($json.body.product || '').toLowerCase() }}", "string"),
                ("description", "={{ $json.body.description || '' }}", "string"),
                ("channel", "={{ $json.body.channel || 'api' }}", "string"),
                ("customer_email", "={{ $json.body.customer_email || '' }}", "string"),
                ("category", "={{ $json.body.assessment.category }}", "string"),
                ("next_step", "={{ $json.body.assessment.next_step }}", "string"),
                ("amount", "={{ Number($json.body.assessment.fields.claimed_amount ?? 0) }}", "number"),
                ("payout", "={{ Number($json.body.assessment.estimated_payout ?? 0) }}", "number"),
                ("incident_date", "={{ $json.body.assessment.fields.incident_date || '' }}", "string"),
                ("citations", "={{ ($json.body.assessment.citations || []).map(c => c.clause).join(', ') }}", "string"),
                ("received_at", "={{ $now.toISO() }}", "string"),
            ],
            keep=False,
        ),
        480,
        560,
    )
    f.add(
        "Payload valid?",
        "if",
        2.4,
        if_params(
            cond("={{ $json.claim_id }}", "string", "notEmpty"),
            cond("={{ $json.product }}", "string", "regex", "^(motor|home|travel)$"),
            cond(
                "={{ $json.next_step }}",
                "string",
                "regex",
                "^(fast_track|assign_adjuster|request_information|review_possible_exclusion)$",
            ),
        ),
        700,
        560,
    )
    f.add(
        "422 body",
        "set",
        3.4,
        set_params(
            [
                ("error", "Invalid claim payload", "string"),
                ("hint", "claim_id, product (motor|home|travel) and assessment.next_step are required", "string"),
            ],
            keep=False,
        ),
        920,
        1100,
    )
    f.add("Respond 422", "respondToWebhook", 1.5, respond(422, "={{ JSON.stringify($json) }}"), 1140, 1100)
    f.add("Amount plausible?", "if", 2.4, if_params(cond("={{ $json.amount }}", "number", "lte", 500000)), 920, 560)
    f.add(
        "Reject impossible amount",
        "stopAndError",
        1,
        {"errorMessage": "=Claim {{ $json.claim_id }}: amount {{ $json.amount }} PLN is implausible"},
        1140,
        820,
    )
    f.add(
        "Is duplicate?",
        "dataTable",
        1.1,
        dt_op(t["hub_claims"], "rowExists", dt_filter(("claim_id", "eq", "={{ $json.claim_id }}"))),
        1140,
        300,
    )
    f.add(
        "Respond 409",
        "respondToWebhook",
        1.5,
        respond(409, '={ "error": "Duplicate claim", "claim_id": "{{ $json.claim_id }}" }'),
        1140,
        120,
    )
    f.add(
        "Is new?",
        "dataTable",
        1.1,
        dt_op(t["hub_claims"], "rowNotExists", dt_filter(("claim_id", "eq", "={{ $json.claim_id }}"))),
        1140,
        560,
    )
    f.add("Risk & SLA engine", "executeWorkflow", 1.2, run_sub(wf["risk"]), 1420, 560)
    f.add(
        "Readable dates",
        "dateTime",
        2,
        {
            "operation": "formatDate",
            "date": "={{ $json.sla_due }}",
            "format": "custom",
            "customFormat": "yyyy-MM-dd HH:mm",
            "outputFieldName": "sla_due_text",
            "options": {"includeInputFields": True},
        },
        1640,
        560,
    )
    f.add(
        "Route by next step",
        "switch",
        3.5,
        switch_params(
            [
                ("Fast track", cond("={{ $json.next_step }}", "string", "equals", "fast_track")),
                ("Adjuster", cond("={{ $json.next_step }}", "string", "equals", "assign_adjuster")),
                ("Missing info", cond("={{ $json.next_step }}", "string", "equals", "request_information")),
                ("Exclusion review", cond("={{ $json.next_step }}", "string", "equals", "review_possible_exclusion")),
            ]
        ),
        2160,
        560,
    )
    f.chain("Claim from AI triage", "Normalize payload", "Payload valid?")
    f.link("Payload valid?", "Amount plausible?", 0)
    f.link("Payload valid?", "422 body", 1)
    f.link("422 body", "Respond 422")
    f.link("Amount plausible?", "Is duplicate?", 0)
    f.link("Amount plausible?", "Is new?", 0)
    f.link("Amount plausible?", "Reject impossible amount", 1)
    f.link("Is duplicate?", "Respond 409")
    f.chain("Is new?", "Risk & SLA engine", "Readable dates", "Route by next step")

    # Lane A
    f.add(
        "Within auto limit?",
        "if",
        2.4,
        if_params(
            cond("={{ $json.payout }}", "number", "lte", 5000), cond("={{ $json.risk_score }}", "number", "lt", 50)
        ),
        2440,
        140,
    )
    f.add("Mark approved", "set", 3.4, set_params([("status", "approved_pending_payment", "string")]), 2660, 80)
    f.add("Queue payout", "dataTable", 1.1, dt_insert(t["hub_claims"], CLAIM_COLUMNS), 2880, 80)
    f.add(
        "Tell customer: approved",
        "dataTable",
        1.1,
        outbox(
            o,
            "email",
            "={{ $('Mark approved').item.json.customer_email || 'customer@example.com' }}",
            "=Claim {{ $('Mark approved').item.json.claim_id }} approved",
            "=Estimated payout {{ $('Mark approved').item.json.payout }} PLN, paid within 3 days.",
        ),
        3100,
        80,
    )
    f.add(
        "Fast-track result",
        "set",
        3.4,
        set_params(
            [
                ("claim_id", "={{ $('Mark approved').item.json.claim_id }}", "string"),
                ("status", "approved_pending_payment", "string"),
                ("payout", "={{ $('Mark approved').item.json.payout }}", "number"),
            ],
            keep=False,
        ),
        3320,
        80,
    )
    # Lane B
    f.add("Assign adjuster", "executeWorkflow", 1.2, run_sub(wf["assign"]), 2660, 460)
    f.add("Mark assigned", "set", 3.4, set_params([("status", "with_adjuster", "string")]), 2880, 460)
    f.add("Save claim", "dataTable", 1.1, dt_insert(t["hub_claims"], CLAIM_COLUMNS), 3100, 460)
    f.add(
        "Create task",
        "dataTable",
        1.1,
        dt_insert(
            t["hub_tasks"],
            {
                "claim_id": "={{ $('Mark assigned').item.json.claim_id }}",
                "type": "inspection",
                "assignee": "={{ $('Mark assigned').item.json.assignee }}",
                "due_at": "={{ $('Mark assigned').item.json.sla_due }}",
                "status": "open",
                "escalated": "={{ false }}",
            },
        ),
        3320,
        460,
    )
    f.add(
        "Notify adjuster",
        "dataTable",
        1.1,
        outbox(
            o,
            "chat",
            "={{ $('Mark assigned').item.json.assignee }}",
            "=New claim {{ $('Mark assigned').item.json.claim_id }} ({{ $('Mark assigned').item.json.priority }})",
            "={{ $('Mark assigned').item.json.category }}, due {{ $('Mark assigned').item.json.sla_due_text }}. Clauses: {{ $('Mark assigned').item.json.citations }}",
        ),
        3540,
        460,
    )
    f.add(
        "Adjuster result",
        "set",
        3.4,
        set_params(
            [
                ("claim_id", "={{ $('Mark assigned').item.json.claim_id }}", "string"),
                ("status", "with_adjuster", "string"),
                ("assignee", "={{ $('Mark assigned').item.json.assignee }}", "string"),
            ],
            keep=False,
        ),
        3760,
        460,
    )
    # close
    f.add("Collect lanes", "merge", 3.2, {"mode": "append", "numberInputs": 2}, 3980, 260)
    f.add(
        "Log claim id",
        "executionData",
        1.1,
        {"dataToSave": {"values": [{"key": "claim_id", "value": "={{ $json.claim_id }}"}]}},
        4200,
        260,
    )
    f.add("Count time saved", "timeSaved", 1, {"mode": "perItem", "minutesSaved": 12}, 4420, 260)
    f.add(
        "Respond 201",
        "respondToWebhook",
        1.5,
        respond(
            201,
            "={{ JSON.stringify({ ok: true, claim_id: $json.claim_id, status: $json.status, tracking: 'https://claims.example/track/' + $json.claim_id }) }}",
        ),
        4420,
        460,
    )
    f.link("Route by next step", "Within auto limit?", 0)
    f.link("Within auto limit?", "Mark approved", 0)
    f.link("Within auto limit?", "Assign adjuster", 1)
    f.chain("Mark approved", "Queue payout", "Tell customer: approved", "Fast-track result")
    f.link("Route by next step", "Assign adjuster", 1)
    f.chain("Assign adjuster", "Mark assigned", "Save claim", "Create task", "Notify adjuster", "Adjuster result")
    f.link("Fast-track result", "Collect lanes", inp=0)
    f.link("Adjuster result", "Collect lanes", inp=1)
    f.chain("Collect lanes", "Log claim id", "Count time saved")
    f.link("Log claim id", "Respond 201")

    # Lane C
    f.add("Mark waiting", "set", 3.4, set_params([("status", "waiting_for_customer", "string")]), 2440, 780)
    f.add("Save waiting claim", "dataTable", 1.1, dt_insert(t["hub_claims"], CLAIM_COLUMNS), 2660, 780)
    f.add(
        "Respond 202 + form link",
        "respondToWebhook",
        1.5,
        respond(
            202,
            "={{ JSON.stringify({ ok: true, claim_id: $('Mark waiting').item.json.claim_id, status: 'waiting_for_customer', form: $execution.resumeFormUrl }) }}",
        ),
        2880,
        780,
    )
    f.add(
        "Ask customer for details",
        "wait",
        1.1,
        {
            "resume": "form",
            "formTitle": "We need a few more details",
            "formDescription": "Your claim is registered. Please confirm the date and the amount so we can continue.",
            "formFields": {
                "values": [
                    {"fieldLabel": "Incident date", "fieldType": "date", "requiredField": True},
                    {"fieldLabel": "Claimed amount (PLN)", "fieldType": "number", "requiredField": True},
                    {"fieldLabel": "Anything else we should know?", "fieldType": "textarea"},
                ]
            },
            "options": {},
        },
        3100,
        780,
    )
    f.add(
        "Merge answers",
        "set",
        3.4,
        set_params(
            [
                ("claim_id", "={{ $('Mark waiting').item.json.claim_id }}", "string"),
                ("amount", "={{ Number($json['Claimed amount (PLN)']) }}", "number"),
                ("incident_date", "={{ $json['Incident date'] }}", "string"),
                ("status", "ready_for_triage", "string"),
            ],
            keep=False,
        ),
        3320,
        780,
    )
    f.add(
        "Update claim",
        "dataTable",
        1.1,
        dt_update(
            t["hub_claims"],
            dt_filter(("claim_id", "eq", "={{ $json.claim_id }}")),
            {
                "amount": "={{ $json.amount }}",
                "incident_date": "={{ $json.incident_date }}",
                "status": "={{ $json.status }}",
            },
        ),
        3540,
        780,
    )
    f.add(
        "Notify handler: details in",
        "dataTable",
        1.1,
        outbox(
            o,
            "chat",
            "claims-desk",
            "=Claim {{ $('Merge answers').item.json.claim_id }}: customer replied",
            "=Amount {{ $('Merge answers').item.json.amount }} PLN, date {{ $('Merge answers').item.json.incident_date }}. Ready for triage.",
        ),
        3760,
        780,
    )
    f.link("Route by next step", "Mark waiting", 2)
    f.chain(
        "Mark waiting",
        "Save waiting claim",
        "Respond 202 + form link",
        "Ask customer for details",
        "Merge answers",
        "Update claim",
        "Notify handler: details in",
    )

    # Lane D
    f.add("Mark for review", "set", 3.4, set_params([("status", "pending_handler_review", "string")]), 2440, 1180)
    f.add("Save claim for review", "dataTable", 1.1, dt_insert(t["hub_claims"], CLAIM_COLUMNS), 2660, 1180)
    f.add(
        "Respond 202 review",
        "respondToWebhook",
        1.5,
        respond(
            202,
            "={{ JSON.stringify({ ok: true, claim_id: $('Mark for review').item.json.claim_id, status: 'pending_handler_review', review_form: $execution.resumeFormUrl }) }}",
        ),
        2880,
        1180,
    )
    f.add(
        "Handler decision",
        "wait",
        1.1,
        {
            "resume": "form",
            "formTitle": "Claim review: possible exclusion",
            "formDescription": "=The assistant flagged a possible exclusion. Clauses: {{ $('Mark for review').item.json.citations }}",
            "formFields": {
                "values": [
                    {
                        "fieldLabel": "Decision",
                        "fieldType": "dropdown",
                        "requiredField": True,
                        "fieldOptions": {
                            "values": [{"option": "Approve"}, {"option": "Reject"}, {"option": "Send to adjuster"}]
                        },
                    },
                    {"fieldLabel": "Note for the file", "fieldType": "textarea", "requiredField": True},
                ]
            },
            "options": {},
        },
        3100,
        1180,
    )
    f.add(
        "Which decision?",
        "switch",
        3.5,
        switch_params(
            [
                ("Approve", cond("={{ $json.Decision }}", "string", "equals", "Approve")),
                ("Reject", cond("={{ $json.Decision }}", "string", "equals", "Reject")),
                ("Adjuster", cond("={{ $json.Decision }}", "string", "equals", "Send to adjuster")),
            ]
        ),
        3320,
        1180,
    )
    f.add("Approved by handler", "set", 3.4, set_params([("status", "approved_by_handler", "string")]), 3540, 1020)
    f.add("Rejected by handler", "set", 3.4, set_params([("status", "rejected_by_handler", "string")]), 3540, 1180)
    f.add("Escalated to adjuster", "set", 3.4, set_params([("status", "with_adjuster", "string")]), 3540, 1340)
    f.add("Join decisions", "merge", 3.2, {"mode": "append", "numberInputs": 3}, 3760, 1180)
    f.add(
        "Record decision",
        "dataTable",
        1.1,
        dt_insert(
            t["hub_decisions"],
            {
                "claim_id": "={{ $('Mark for review').item.json.claim_id }}",
                "decision": "={{ $json.Decision }}",
                "note": "={{ $json['Note for the file'] }}",
                "decided_at": "={{ $now.toISO() }}",
            },
        ),
        3980,
        1180,
    )
    f.add(
        "Update claim status",
        "dataTable",
        1.1,
        dt_update(
            t["hub_claims"],
            dt_filter(("claim_id", "eq", "={{ $('Mark for review').item.json.claim_id }}")),
            {"status": "={{ $('Join decisions').item.json.status }}"},
        ),
        4200,
        1180,
    )
    f.add(
        "Tell customer: decision",
        "dataTable",
        1.1,
        outbox(
            o,
            "email",
            "={{ $('Mark for review').item.json.customer_email || 'customer@example.com' }}",
            "=Update on claim {{ $('Mark for review').item.json.claim_id }}",
            "=Status: {{ $('Join decisions').item.json.status }}",
        ),
        4420,
        1180,
    )
    f.link("Route by next step", "Mark for review", 3)
    f.chain("Mark for review", "Save claim for review", "Respond 202 review", "Handler decision", "Which decision?")
    for i, n in enumerate(["Approved by handler", "Rejected by handler", "Escalated to adjuster"]):
        f.link("Which decision?", n, out=i)
        f.link(n, "Join decisions", inp=i)
    f.chain("Join decisions", "Record decision", "Update claim status", "Tell customer: decision")
    return f


def customer_form(t: dict, wf: dict) -> Flow:
    f = Flow("Claims Hub · 2 · Customer claim form")
    o = t | {"outbox": t["hub_outbox"]}
    f.section(
        "Public form", "`/form/claims-hub/report` - customers report a claim without logging in.", 0, 0, 420, 640, 7
    )
    f.section("Shape & triage", "Rules decide the first next step; API claims arrive already triaged by the AI assistant.", 440, 0, 900, 640, 6)
    f.section(
        "Store, notify, confirm", "Saved like API claims; the customer sees a confirmation page.", 1360, 0, 1120, 640, 4
    )
    f.add(
        "Claim form",
        "formTrigger",
        2.6,
        {
            "formTitle": "Report a claim",
            "formDescription": "Tell us what happened. We reply within one business day.",
            "formFields": {
                "values": [
                    {"fieldLabel": "Policy number", "requiredField": True},
                    {
                        "fieldLabel": "Product",
                        "fieldType": "dropdown",
                        "requiredField": True,
                        "fieldOptions": {"values": [{"option": "motor"}, {"option": "home"}, {"option": "travel"}]},
                    },
                    {"fieldLabel": "What happened?", "fieldType": "textarea", "requiredField": True},
                    {"fieldLabel": "Incident date", "fieldType": "date"},
                    {"fieldLabel": "Claimed amount (PLN)", "fieldType": "number"},
                    {"fieldLabel": "Email", "fieldType": "email", "requiredField": True},
                ]
            },
            "responseMode": "lastNode",
            "options": {"path": "claims-hub/report"},
        },
        120,
        300,
    )
    f.add(
        "Map form to claim",
        "set",
        3.4,
        set_params(
            [
                ("claim_id", "={{ 'WEB-' + $execution.id }}", "string"),
                ("policy_ref", "[POLICY_NO]", "string"),
                ("product", "={{ $json.Product }}", "string"),
                ("description", "={{ $json['What happened?'] }}", "string"),
                ("amount", "={{ Number($json['Claimed amount (PLN)'] || 0) }}", "number"),
                ("payout", "0", "number"),
                ("incident_date", "={{ $json['Incident date'] || '' }}", "string"),
                ("customer_email", "={{ $json.Email }}", "string"),
                ("channel", "web_form", "string"),
                ("category", "={{ $json.Product + '_unclassified' }}", "string"),
                ("received_at", "={{ $now.toISO() }}", "string"),
            ],
            keep=False,
        ),
        500,
        300,
    )
    f.add(
        "Complete enough?",
        "if",
        2.4,
        if_params(
            cond("={{ $json.amount }}", "number", "gt", 0), cond("={{ $json.incident_date }}", "string", "notEmpty")
        ),
        720,
        300,
    )
    f.add("Needs adjuster", "set", 3.4, set_params([("next_step", "assign_adjuster", "string")]), 940, 180)
    f.add("Needs information", "set", 3.4, set_params([("next_step", "request_information", "string")]), 940, 420)
    f.add("Join", "merge", 3.2, {"mode": "append", "numberInputs": 2}, 1160, 300)
    f.add("Risk & SLA engine", "executeWorkflow", 1.2, run_sub(wf["risk"]), 1420, 300)
    f.add("Mark received", "set", 3.4, set_params([("status", "received_web", "string")]), 1640, 300)
    f.add("Save claim", "dataTable", 1.1, dt_insert(t["hub_claims"], CLAIM_COLUMNS), 1860, 300)
    f.add(
        "Confirmation email",
        "dataTable",
        1.1,
        outbox(
            o,
            "email",
            "={{ $('Mark received').item.json.customer_email }}",
            "=We received claim {{ $('Mark received').item.json.claim_id }}",
            "=Priority {{ $('Mark received').item.json.priority }}, next step: {{ $('Mark received').item.json.next_step }}.",
        ),
        2080,
        300,
    )
    f.add(
        "Thank-you page",
        "form",
        2.5,
        {
            "operation": "completion",
            "completionTitle": "Thank you!",
            "completionMessage": "=Your claim number is {{ $('Mark received').item.json.claim_id }}. We sent a confirmation to {{ $('Mark received').item.json.customer_email }}.",
            "options": {},
        },
        2300,
        300,
    )
    f.chain("Claim form", "Map form to claim", "Complete enough?")
    f.link("Complete enough?", "Needs adjuster", 0)
    f.link("Complete enough?", "Needs information", 1)
    f.link("Needs adjuster", "Join", inp=0)
    f.link("Needs information", "Join", inp=1)
    f.chain("Join", "Risk & SLA engine", "Mark received", "Save claim", "Confirmation email", "Thank-you page")
    return f


def watchdog(t: dict) -> Flow:
    f = Flow("Claims Hub · 5 · SLA watchdog")
    o = t | {"outbox": t["hub_outbox"]}
    f.section("Every hour", "Find open adjuster tasks past their due time.", 0, 0, 900, 520, 7)
    f.section("Escalate in batches", "Mark escalated and page the team lead, 5 tasks per batch.", 920, 0, 1120, 520, 3)
    f.section("Digest", "One summary message per run.", 2060, 0, 700, 520, 5)
    f.add(
        "Every hour", "scheduleTrigger", 1.4, {"rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}}, 120, 260
    )
    f.add(
        "Open tasks",
        "dataTable",
        1.1,
        dt_get(t["hub_tasks"], dt_filter(("status", "eq", "open"), ("escalated", "isFalse"))),
        340,
        260,
    )
    f.add(
        "Overdue only",
        "filter",
        2.4,
        if_params(cond("={{ $json.due_at }}", "dateTime", "before", "={{ $now.toISO() }}")),
        560,
        260,
    )
    f.add("Batches of 5", "splitInBatches", 3, {"batchSize": 5, "options": {}}, 980, 260)
    f.add(
        "Mark escalated",
        "dataTable",
        1.1,
        dt_update(t["hub_tasks"], dt_filter(("claim_id", "eq", "={{ $json.claim_id }}")), {"escalated": "={{ true }}"}),
        1200,
        400,
    )
    f.add(
        "Page team lead",
        "dataTable",
        1.1,
        outbox(
            o,
            "pager",
            "team-lead",
            "=SLA breach: claim {{ $('Batches of 5').item.json.claim_id }}",
            "=Assignee {{ $('Batches of 5').item.json.assignee }}, due {{ $('Batches of 5').item.json.due_at }}",
        ),
        1420,
        400,
    )
    f.add("Next batch", "noOp", 1, {}, 1640, 400)
    f.add(
        "Count breaches",
        "summarize",
        1.1,
        {"fieldsToSummarize": {"values": [{"aggregation": "count", "field": "claim_id"}]}, "options": {}},
        2120,
        260,
    )
    f.add(
        "Send digest",
        "dataTable",
        1.1,
        outbox(
            o,
            "chat",
            "claims-desk",
            "=SLA watchdog: {{ $json.count_claim_id }} escalations",
            "=Run at {{ $now.toFormat('yyyy-MM-dd HH:mm') }}",
        ),
        2340,
        260,
    )
    f.chain("Every hour", "Open tasks", "Overdue only", "Batches of 5")
    f.link("Batches of 5", "Count breaches", out=0)
    f.link("Batches of 5", "Mark escalated", out=1)
    f.chain("Mark escalated", "Page team lead", "Next batch")
    f.link("Next batch", "Batches of 5")
    f.link("Count breaches", "Send digest")
    return f


def kpi(t: dict) -> Flow:
    f = Flow("Claims Hub · 6 · Daily KPI digest")
    o = t | {"outbox": t["hub_outbox"]}
    f.section("07:00 every day", "Claims received in the last 24 hours.", 0, 0, 680, 620, 7)
    f.section("Aggregate", "Per-lane counts and totals in parallel.", 700, 0, 920, 620, 6)
    f.section("Publish", "Store the KPI row, send the digest, count time saved.", 1640, 0, 1120, 620, 4)
    f.add(
        "Every day 07:00",
        "scheduleTrigger",
        1.4,
        {"rule": {"interval": [{"field": "cronExpression", "expression": "0 7 * * *"}]}},
        120,
        300,
    )
    f.add("All claims", "dataTable", 1.1, dt_get(t["hub_claims"]), 340, 300)
    f.add(
        "Last 24 hours",
        "filter",
        2.4,
        if_params(cond("={{ $json.received_at }}", "dateTime", "after", "={{ $now.minus({ days: 1 }).toISO() }}")),
        560,
        300,
    )
    f.add(
        "Per lane",
        "summarize",
        1.1,
        {
            "fieldsToSummarize": {"values": [{"aggregation": "count", "field": "claim_id"}]},
            "fieldsToSplitBy": "next_step",
            "options": {},
        },
        780,
        180,
    )
    f.add(
        "Totals",
        "summarize",
        1.1,
        {
            "fieldsToSummarize": {
                "values": [
                    {"aggregation": "count", "field": "claim_id"},
                    {"aggregation": "sum", "field": "amount"},
                    {"aggregation": "sum", "field": "payout"},
                ]
            },
            "options": {},
        },
        780,
        420,
    )
    f.add("Wait for both", "merge", 3.2, {"mode": "append", "numberInputs": 2}, 1000, 300)
    f.add(
        "One item",
        "aggregate",
        1,
        {"aggregate": "aggregateAllItemData", "destinationFieldName": "rows", "options": {}},
        1220,
        300,
    )
    lane = "={{ ($('Per lane').all().find(i => i.json.next_step === '%s') || { json: { count_claim_id: 0 } }).json.count_claim_id }}"
    f.add(
        "Build report",
        "set",
        3.4,
        set_params(
            [
                ("claims", "={{ $('Totals').first().json.count_claim_id }}", "number"),
                ("fast_track", lane % "fast_track", "number"),
                ("adjuster", lane % "assign_adjuster", "number"),
                ("info_requested", lane % "request_information", "number"),
                ("exclusion_review", lane % "review_possible_exclusion", "number"),
                ("total_claimed", "={{ $('Totals').first().json.sum_amount }}", "number"),
                ("total_payout", "={{ $('Totals').first().json.sum_payout }}", "number"),
            ],
            keep=False,
        ),
        1440,
        300,
    )
    f.add(
        "Store KPI row",
        "dataTable",
        1.1,
        dt_insert(
            t["hub_kpi"],
            {
                "report_date": "={{ $today.toISO() }}",
                "claims": "={{ $json.claims }}",
                "fast_track": "={{ $json.fast_track }}",
                "adjuster": "={{ $json.adjuster }}",
                "info_requested": "={{ $json.info_requested }}",
                "exclusion_review": "={{ $json.exclusion_review }}",
                "total_claimed": "={{ $json.total_claimed }}",
                "total_payout": "={{ $json.total_payout }}",
            },
        ),
        1700,
        300,
    )
    f.add(
        "Send digest",
        "dataTable",
        1.1,
        outbox(
            o,
            "email",
            "management@claims.example",
            "=Claims KPI {{ $today.toFormat('yyyy-MM-dd') }}",
            "={{ $('Build report').item.json.claims }} claims | fast track {{ $('Build report').item.json.fast_track }} | adjuster {{ $('Build report').item.json.adjuster }} | info {{ $('Build report').item.json.info_requested }} | review {{ $('Build report').item.json.exclusion_review }} | payout {{ $('Build report').item.json.total_payout }} PLN",
        ),
        1920,
        300,
    )
    f.add("Count time saved", "timeSaved", 1, {"mode": "once", "minutesSaved": 30}, 2140, 300)
    f.chain("Every day 07:00", "All claims", "Last 24 hours")
    f.link("Last 24 hours", "Per lane")
    f.link("Last 24 hours", "Totals")
    f.link("Per lane", "Wait for both", inp=0)
    f.link("Totals", "Wait for both", inp=1)
    f.chain("Wait for both", "One item", "Build report", "Store KPI row", "Send digest", "Count time saved")
    return f
