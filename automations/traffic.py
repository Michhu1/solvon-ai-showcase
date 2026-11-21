"""Send realistic traffic into the Claims Operations Hub.

Each claim is first triaged by the claims assistant (offline mode, no API key) and its assessment is posted to
the hub's webhook - the same hand-off a production deployment would do. A few bad requests exercise the
422 / 409 / error-workflow paths.

    python automations/traffic.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "claims-assistant" / "src"))

from claims_assistant.config import Settings  # noqa: E402
from claims_assistant.models import ClaimRequest  # noqa: E402
from claims_assistant.pii import mask_pii  # noqa: E402
from claims_assistant.service import ClaimsService  # noqa: E402

from deploy import load_env  # noqa: E402

DATASET = HERE.parent / "claims-assistant" / "data" / "eval" / "claims.jsonl"
EXTRA = [
    {
        "policy_number": "MOT-2026-007700",
        "product": "motor",
        "description": "Someone scratched the whole left side of my parked car and drove away on 2026-11-18. Body shop quote: 2 600 PLN.",
    },
    {
        "policy_number": "HOM-2026-001234",
        "product": "home",
        "description": "Storm winds tore part of the roof off on 16.11.2026. The roofer estimates 18 500 PLN.",
    },
    {
        "policy_number": "TRV-2026-005566",
        "product": "travel",
        "description": "Food poisoning in Egypt on 2026-11-10, two days in a clinic, bills of 4 100 PLN.",
    },
    {
        "policy_number": "MOT-2026-008811",
        "product": "motor",
        "description": "On 19.11.2026 a branch fell on the windscreen during a storm. Replacement: 2 400 PLN.",
    },
    {
        "policy_number": "HOM-2026-004455",
        "product": "home",
        "description": "The washing machine hose burst on 2026-11-17 and water leaked to the neighbour below. Damage about 7 900 PLN.",
    },
    {
        "policy_number": "MOT-2026-003322",
        "product": "motor",
        "description": "My car was stolen from a supermarket car park on 2026-11-15, police report filed, value 61 000 PLN.",
    },
]


def main() -> int:
    env = load_env()
    base = env["SOLVON_URL"]
    work = Path(tempfile.mkdtemp(prefix="hub-traffic-"))
    service = ClaimsService(replace(Settings(), trace_dir=None, audit_log=work / "audit.jsonl"))
    claims = [json.loads(line)["request"] for line in DATASET.read_text("utf-8").splitlines() if line.strip()] + EXTRA
    http = httpx.Client(base_url=base, timeout=60, headers={"X-Hub-Token": env["HUB_TOKEN"]})
    results = []
    for i, raw in enumerate(claims, 1):
        claim = ClaimRequest.model_validate(raw)
        assessment, trace = service.assess(claim)
        payload = {
            "claim_id": f"CLM-{2026_11_00 + i}",
            "policy_ref": "[POLICY_NO]",
            "product": assessment.category.split("_")[0] if assessment.category else claim.product,
            "description": mask_pii(claim.description)[0],
            "channel": "ai_triage",
            "customer_email": f"customer{i}@example.com",
            "assessment": assessment.model_dump(mode="json"),
        }
        r = http.post("/webhook/claims-hub/intake", json=payload)
        results.append(
            {
                "claim_id": payload["claim_id"],
                "next_step": assessment.next_step.value,
                "status": r.status_code,
                "body": r.json() if r.content else None,
            }
        )
        print(f"{payload['claim_id']} {assessment.next_step.value:<26} -> {r.status_code} {r.text[:120]}")

    print("\n-- edge cases --")
    r = httpx.post(f"{base}/webhook/claims-hub/intake", json={"claim_id": "CLM-NOAUTH"}, timeout=60)
    print("no token ->", r.status_code, r.text[:160])
    neg = {
        "claim_id": "CLM-NEG-1",
        "product": "motor",
        "description": "negative",
        "assessment": {"next_step": "fast_track", "estimated_payout": 900, "fields": {"claimed_amount": -500}},
    }
    r = http.post("/webhook/claims-hub/intake", json=neg)
    print("negative amount / payout > amount ->", r.status_code, r.text[:160])
    bad = {"claim_id": "", "product": "boat", "assessment": {"next_step": "pay_now"}}
    r = http.post("/webhook/claims-hub/intake", json=bad)
    print("invalid payload ->", r.status_code, r.text[:160])
    dup = {
        "claim_id": results[0]["claim_id"],
        "product": "motor",
        "description": "resend",
        "assessment": {"next_step": "fast_track", "fields": {"claimed_amount": 100}},
    }
    r = http.post("/webhook/claims-hub/intake", json=dup)
    print("duplicate ->", r.status_code, r.text[:160])
    huge = {
        "claim_id": "CLM-HUGE-1",
        "product": "home",
        "description": "Palace burned down",
        "assessment": {"next_step": "assign_adjuster", "fields": {"claimed_amount": 9_000_000}},
    }
    r = http.post("/webhook/claims-hub/intake", json=huge)
    print("implausible amount ->", r.status_code, r.text[:160])
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "traffic.json").write_text(json.dumps(results, indent=2), "utf-8")

    print("\n-- backlog: three tasks already past their SLA, then run the scheduled flows once --")
    ids = json.loads((HERE / "out" / "ids.json").read_text("utf-8"))
    api = httpx.Client(base_url=f"{base}/api/v1", headers={"X-N8N-API-KEY": env["SOLVON_API_KEY"]}, timeout=60)
    overdue = [
        {
            "claim_id": f"CLM-2026103{n}",
            "type": "inspection",
            "assignee": who,
            "status": "open",
            "escalated": False,
            "due_at": (datetime.now(UTC) - timedelta(hours=h)).isoformat(),
        }
        for n, who, h in [(1, "Marek (home, South)", 30), (2, "Ola (motor, North)", 6), (3, "Ewa (travel, all)", 52)]
    ]
    api.post(
        f"/data-tables/{ids['tables']['hub_tasks']}/rows", json={"data": overdue, "returnType": "count"}
    ).raise_for_status()
    session = httpx.Client(base_url=base, timeout=120)
    session.post(
        "/rest/login", json={"email": env["SOLVON_EMAIL"], "password": env["SOLVON_PASSWORD"]}
    ).raise_for_status()
    for key in ("watchdog", "kpi"):
        r = session.post(f"/rest/workflows/{ids['workflows'][key]}/run", json={})
        print(f"run {key} ->", r.status_code)
        time.sleep(3)

    print("\n-- waiting for a human --")
    for row in results:
        if row["status"] == 202:
            where = (
                "customer form in the response"
                if (row["body"] or {}).get("form")
                else "review link sent to the claims desk"
            )
            print(row["claim_id"], row["next_step"], "->", where)
    return 0


if __name__ == "__main__":
    sys.exit(main())
