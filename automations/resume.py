"""Answer the waiting forms like a handler and a customer would (human-in-the-loop part of the demo).

The form page posts multipart `field-0..field-N` to the `submitUrl` in its config; this does the same.

    python automations/resume.py
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

import httpx

from deploy import load_env

HERE = Path(__file__).resolve().parent

ANSWERS = {
    "review_possible_exclusion": [
        ["Reject", "Driver admitted drinking before driving; exclusion MOT-3.1 applies. Customer informed by phone."],
        ["Send to adjuster", "Damp may be partly sudden leak - adjuster to inspect the bathroom before deciding."],
        ["Approve", "Condition was stable and unrelated to the treatment; exclusion TRV-3.1 does not apply."],
    ],
    "request_information": [["2026-11-14", "3200", "Smoke damage limited to the kitchen; photos sent by email."]],
}


def submit_url(page: str, base: str) -> str:
    match = re.search(r'submitUrl\\?"\s*:\s*\\?"([^"\\]+)', page)
    if not match:
        raise RuntimeError("submitUrl not found on the form page")
    url = html.unescape(match.group(1)).replace("\\u0026", "&").replace("\\/", "/")
    return url if url.startswith("http") else base + url


def desk_review_links(env: dict[str, str]) -> dict[str, str]:
    """Review links are never returned to the API caller; the claims desk gets them through the internal outbox."""
    ids = json.loads((HERE / "out" / "ids.json").read_text("utf-8"))
    api = httpx.Client(base_url=f"{env['SOLVON_URL']}/api/v1", headers={"X-N8N-API-KEY": env["SOLVON_API_KEY"]})
    rows = api.get(f"/data-tables/{ids['tables']['hub_outbox']}/rows", params={"limit": 250}).json()
    links = {}
    for row in rows.get("data", rows):
        if row["channel"] == "internal" and row["subject"].startswith("Review needed: claim "):
            links[row["subject"].rsplit(" ", 1)[-1]] = row["body"]
    return links


def main() -> int:
    env = load_env()
    results = json.loads((HERE / "out" / "traffic.json").read_text("utf-8"))
    desk = desk_review_links(env)
    used = {key: 0 for key in ANSWERS}
    http = httpx.Client(timeout=60)
    for row in [] if "--web-only" in sys.argv else results:
        body = row["body"] or {}
        link = body.get("form") or desk.get(row["claim_id"])
        if not link or row["next_step"] not in ANSWERS:
            continue
        values = ANSWERS[row["next_step"]][used[row["next_step"]] % len(ANSWERS[row["next_step"]])]
        used[row["next_step"]] += 1
        page = http.get(link)
        base = re.match(r"https?://[^/]+", link).group(0)
        target = submit_url(page.text, base)
        files = {f"field-{i}": (None, v) for i, v in enumerate(values)}
        r = http.post(target, files=files)
        print(row["claim_id"], row["next_step"], values[0], "->", r.status_code, r.text[:100].replace("\n", " "))

    # Customers using the public form (workflow 2).
    base = load_env()["SOLVON_URL"]
    for values in [
        [
            "MOT-2026-009900",
            "motor",
            "Hail dented the bonnet while the car was parked outside.",
            "2026-11-19",
            "4300",
            "web.customer1@example.com",
        ],
        [
            "HOM-2026-009901",
            "home",
            "A pipe in the bathroom wall burst overnight.",
            "",
            "",
            "web.customer2@example.com",
        ],
    ]:
        page = http.get(f"{base}/form/claims-hub/report")
        target = submit_url(page.text, base)
        r = http.post(target, files={f"field-{i}": (None, v) for i, v in enumerate(values)})
        print("web form", values[1], "->", r.status_code, r.text[:140].replace("\n", " "))
        # A browser opens the thank-you page right away; that completes the execution.
        if r.status_code == 200 and "formWaitingUrl" in r.text:
            waiting = httpx.URL(r.json()["formWaitingUrl"]).copy_with(host=httpx.URL(base).host)
            status = waiting.copy_with(path=waiting.path.rstrip("/") + "/execution-status")
            for _ in range(5):
                if http.get(status).status_code == 200:
                    break
            page = http.get(waiting)
            print("   thank-you page ->", page.status_code, "Thank you" in page.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
