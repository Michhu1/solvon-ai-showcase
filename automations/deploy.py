"""Deploy the Claims Operations Hub to a Solvon instance through its public REST API.

    python automations/deploy.py            # reads automations/.env.local (SOLVON_URL, SOLVON_API_KEY)

Idempotent: removes earlier "Claims Hub" workflows and hub_* tables of the same account first.
Exports the published workflows to automations/workflows/*.json.
"""

from __future__ import annotations

import json
import re
import secrets
import sys
import time
from pathlib import Path

import httpx

import hub

HERE = Path(__file__).resolve().parent


def load_env() -> dict[str, str]:
    env = {}
    for line in (HERE / ".env.local").read_text("utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class Api:
    """Thin client; spaces calls out to stay under the 60 requests/minute key limit."""

    def __init__(self, base: str, key: str):
        self.http = httpx.Client(base_url=f"{base}/api/v1", headers={"X-N8N-API-KEY": key}, timeout=60)

    def __call__(self, method: str, path: str, **kw):
        time.sleep(1.05)
        r = self.http.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:600]}")
        return r.json() if r.content else None


def items(payload) -> list:
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def cleanup(api: Api) -> None:
    for wf in items(api("GET", "/workflows", params={"limit": 250})):
        if wf["name"].startswith("Claims Hub"):
            if wf.get("active"):
                api("POST", f"/workflows/{wf['id']}/deactivate")
            try:
                api("DELETE", f"/workflows/{wf['id']}")
            except RuntimeError:
                api("POST", f"/workflows/{wf['id']}/archive")
                api("DELETE", f"/workflows/{wf['id']}")
    for table in items(api("GET", "/data-tables", params={"limit": 100})):
        if table["name"].startswith("hub_"):
            api("DELETE", f"/data-tables/{table['id']}")


def create_tables(api: Api) -> dict[str, str]:
    ids = {}
    for name, columns in hub.TABLES.items():
        created = api(
            "POST", "/data-tables", json={"name": name, "columns": [{"name": n, "type": t} for n, t in columns]}
        )
        ids[name] = created["id"]
    api("POST", f"/data-tables/{ids['hub_adjusters']}/rows", json={"data": hub.ADJUSTERS, "returnType": "count"})
    return ids


def create(api: Api, flow: hub.Flow, settings: dict | None = None) -> str:
    created = api("POST", "/workflows", json=flow.to_dict(settings))
    api("POST", f"/workflows/{created['id']}/publish")
    print(f"  published {flow.name} ({len(flow.nodes)} nodes) -> {created['id']}")
    return created["id"]


def export(api: Api, workflow_ids: dict[str, str]) -> None:
    out = HERE / "workflows"
    out.mkdir(exist_ok=True)
    for path in out.glob("*.json"):
        path.unlink()
    for wid in workflow_ids.values():
        wf = api("GET", f"/workflows/{wid}")
        keep = {k: wf[k] for k in ("name", "nodes", "connections", "settings") if k in wf}
        slug = re.sub(r"[^a-z0-9]+", "-", wf["name"].lower()).strip("-")
        (out / f"{slug}.json").write_text(json.dumps(keep, indent=2, ensure_ascii=False) + "\n", "utf-8")


def ensure_hub_token(api: Api, env: dict[str, str]) -> None:
    """The intake webhook checks X-Hub-Token against the project variable HUB_TOKEN; keep both in sync."""
    token = env.get("HUB_TOKEN") or secrets.token_urlsafe(24)
    if "HUB_TOKEN" not in env:
        with (HERE / ".env.local").open("a", encoding="utf-8") as f:
            f.write(f"HUB_TOKEN={token}\n")
        env["HUB_TOKEN"] = token
    existing = [v for v in items(api("GET", "/variables")) if v["key"] == "HUB_TOKEN"]
    if existing:
        api("PUT", f"/variables/{existing[0]['id']}", json={"key": "HUB_TOKEN", "value": token})
    else:
        api(
            "POST",
            "/variables",
            json={"key": "HUB_TOKEN", "value": token, "type": "string", "projectId": env["SOLVON_PROJECT_ID"]},
        )


def update_in_place(api: Api) -> None:
    """Re-render every workflow with the existing ids and PUT it (keeps executions, Insights and table data)."""
    ids = json.loads((HERE / "out" / "ids.json").read_text("utf-8"))
    tables, wf = ids["tables"], ids["workflows"]
    on_error = {"errorWorkflow": wf["errors"], "saveDataSuccessExecution": "all", "saveExecutionProgress": True}
    flows = {
        "errors": (hub.error_handler(tables), {}),
        "risk": (hub.risk_engine(tables), on_error),
        "assign": (hub.assign_adjuster(tables), on_error),
        "intake": (hub.intake(tables, wf), on_error | {"timeSavedPerExecution": 12}),
        "form": (hub.customer_form(tables, wf), on_error),
        "watchdog": (hub.watchdog(tables), on_error),
        "kpi": (hub.kpi(tables), on_error),
    }
    for key, (flow, settings) in flows.items():
        api("PUT", f"/workflows/{wf[key]}", json=flow.to_dict(settings))
        print(f"  updated {flow.name}")
    export(api, wf)


def main() -> int:
    env = load_env()
    api = Api(env["SOLVON_URL"], env["SOLVON_API_KEY"])
    ensure_hub_token(api, env)
    if "--update" in sys.argv:
        update_in_place(api)
        return 0
    print("cleaning up earlier deploys...")
    cleanup(api)
    print("creating data tables...")
    tables = create_tables(api)
    print("creating workflows...")
    wf: dict[str, str] = {}
    wf["errors"] = create(api, hub.error_handler(tables))
    on_error = {"errorWorkflow": wf["errors"], "saveDataSuccessExecution": "all", "saveExecutionProgress": True}
    wf["risk"] = create(api, hub.risk_engine(tables), on_error)
    wf["assign"] = create(api, hub.assign_adjuster(tables), on_error)
    wf["intake"] = create(api, hub.intake(tables, wf), on_error | {"timeSavedPerExecution": 12})
    wf["form"] = create(api, hub.customer_form(tables, wf), on_error)
    wf["watchdog"] = create(api, hub.watchdog(tables), on_error)
    wf["kpi"] = create(api, hub.kpi(tables), on_error)
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "ids.json").write_text(json.dumps({"tables": tables, "workflows": wf}, indent=2), "utf-8")
    export(api, wf)
    print("done:", json.dumps(wf))
    return 0


if __name__ == "__main__":
    sys.exit(main())
