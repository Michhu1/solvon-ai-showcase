"""REST API: synchronous assessment, plus an asynchronous job variant for slow upstream systems."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from .config import Settings
from .models import Assessment, ClaimRequest
from .prompts import PROMPT_VERSION
from .service import ClaimsService


class Job(BaseModel):
    job_id: str
    status: Literal["queued", "done", "failed"]
    result: Assessment | None = None
    error: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "service"):  # tests inject their own
        app.state.service = ClaimsService(Settings.from_env())
    app.state.jobs = {}
    yield


app = FastAPI(
    title="Claims triage assistant",
    version="1.0.0",
    description="Decision support for claims handlers: category, extracted fields, next step and cited policy terms.",
    lifespan=lifespan,
)


@app.get("/health")
def health(request: Request) -> dict:
    service: ClaimsService = request.app.state.service
    return {
        "status": "ok",
        "llm": service.settings.llm_backend,
        "retriever": service.settings.retriever,
        "prompt_version": PROMPT_VERSION,
    }


@app.post("/claims/assess", response_model=Assessment)
def assess(claim: ClaimRequest, request: Request) -> Assessment:
    # A plain `def`: FastAPI runs it in a worker thread, so blocking LLM calls do not stall the event loop.
    assessment, _ = request.app.state.service.assess(claim)
    return assessment


@app.post("/claims", response_model=Job, status_code=status.HTTP_202_ACCEPTED)
def submit(claim: ClaimRequest, request: Request, background: BackgroundTasks) -> Job:
    """Accepts the claim and returns at once; poll GET /claims/jobs/{job_id}.

    In production the job would go to a message queue (e.g. Azure Service Bus) and a worker; the API shape
    stays the same.
    """
    job = Job(job_id=uuid.uuid4().hex[:12], status="queued")
    request.app.state.jobs[job.job_id] = job
    background.add_task(_run_job, request.app, job.job_id, claim)
    return job


@app.get("/claims/jobs/{job_id}", response_model=Job)
def job_status(job_id: str, request: Request) -> Job:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job


def _run_job(app: FastAPI, job_id: str, claim: ClaimRequest) -> None:
    try:
        assessment, _ = app.state.service.assess(claim)
        app.state.jobs[job_id] = Job(job_id=job_id, status="done", result=assessment)
    except Exception as error:  # report, never crash the worker
        app.state.jobs[job_id] = Job(job_id=job_id, status="failed", error=type(error).__name__)
