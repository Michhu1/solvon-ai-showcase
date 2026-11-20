import json

import pytest
from fastapi.testclient import TestClient

from claims_assistant.api import app
from evals.run_eval import DATASET, THRESHOLDS, evaluate_case, summarize

CLAIM = {
    "policy_number": "HOM-2026-000415",
    "product": "home",
    "description": "A pipe burst under the kitchen sink on 03.11.2026. Damaged floor: 4 200 PLN.",
}


@pytest.fixture
def client(service):
    app.state.service = service
    with TestClient(app) as test_client:
        yield test_client
    del app.state.service


def test_health(client):
    assert client.get("/health").json() == {
        "status": "ok",
        "llm": "offline",
        "retriever": "bm25",
        "prompt_version": "triage-v3",
    }


def test_assess(client):
    response = client.post("/claims/assess", json=CLAIM)
    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "home_water"
    assert body["next_step"] == "fast_track"
    assert body["estimated_payout"] == 3900
    assert body["citations"][0]["clause"] == "HOM-2.2"
    assert "claims handler" in body["disclaimer"]


def test_validation_error_for_a_bad_request(client):
    assert client.post("/claims/assess", json={"policy_number": "x", "description": "short"}).status_code == 422


def test_async_job(client):
    accepted = client.post("/claims", json=CLAIM)
    assert accepted.status_code == 202
    job = client.get(f"/claims/jobs/{accepted.json()['job_id']}").json()
    assert job["status"] == "done"  # TestClient runs background tasks before returning
    assert job["result"]["category"] == "home_water"
    assert client.get("/claims/jobs/nope").status_code == 404


def test_offline_eval_meets_the_quality_gate(service):
    cases = [json.loads(line) for line in DATASET.read_text("utf-8").splitlines() if line.strip()]
    metrics = summarize([evaluate_case(service, case) for case in cases])
    thresholds = {k: v for k, v in json.loads(THRESHOLDS.read_text("utf-8")).items() if not k.startswith("_")}
    for name, minimum in thresholds.items():
        assert metrics[name] >= minimum, name


def test_pii_leak_metric_catches_a_leak(service, monkeypatch):
    case = {
        "id": "leak",
        "request": {
            "policy_number": "MOT-2026-000001",
            "product": "motor",
            "description": "Hit a pole. PESEL 44051401359.",
        },
        "expected": {
            "category": "motor_collision",
            "incident_date": None,
            "claimed_amount": None,
            "next_step": "request_information",
            "clause": "MOT-2.1",
            "pii": ["44051401359"],
        },
    }
    assert evaluate_case(service, case)["pii_leak_free"] is True
    monkeypatch.setattr("claims_assistant.agent.mask_pii", lambda text: (text, []))  # simulate a masking bug
    assert evaluate_case(service, case)["pii_leak_free"] is False
