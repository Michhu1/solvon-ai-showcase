"""Request and response schemas of the REST API."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class Product(StrEnum):
    motor = "motor"
    home = "home"
    travel = "travel"


class NextStep(StrEnum):
    fast_track = "fast_track"  # covered, small amount: a handler can approve quickly
    assign_adjuster = "assign_adjuster"  # covered, but large or always inspected (theft, burglary)
    request_information = "request_information"  # date or amount missing
    review_possible_exclusion = "review_possible_exclusion"  # an exclusion may apply; a human decides


class ClaimRequest(BaseModel):
    policy_number: str = Field(examples=["MOT-2026-004512"])
    product: Product | None = Field(default=None, description="If omitted, inferred from the description.")
    description: str = Field(min_length=10, max_length=4000)
    incident_date: date | None = None
    claimed_amount: float | None = Field(default=None, ge=0, description="PLN")


class ExtractedFields(BaseModel):
    incident_date: date | None
    claimed_amount: float | None
    damage_type: str | None


class Citation(BaseModel):
    clause: str
    section: str
    quote: str


class Assessment(BaseModel):
    trace_id: str
    category: str | None = Field(description="<product>_<cause>, e.g. motor_collision")
    fields: ExtractedFields
    next_step: NextStep
    estimated_payout: float | None = None
    answer: str
    citations: list[Citation]
    guardrail_flags: list[str] = []
    disclaimer: str = "Decision support only. A claims handler makes and owns the decision."
