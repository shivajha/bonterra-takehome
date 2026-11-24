"""Pydantic output models -> published as MCP output schemas (spec §5.1)."""
from typing import Optional

from pydantic import BaseModel, Field


class Identity(BaseModel):
    staff_id: str
    name: str
    role: str
    nonprofit_id: str
    nonprofit_name: str


class Participant(BaseModel):
    id: str
    name: str


class ParticipantList(BaseModel):
    participants: list[Participant]


class FundStatus(BaseModel):
    fund_id: str
    name: str
    allocation: str = Field(description="USD, e.g. '10000.00'")
    committed: str
    paid: str
    available: str = Field(description="allocation - committed - paid")
    allocation_cents: int
    committed_cents: int
    paid_cents: int
    available_cents: int


class FundList(BaseModel):
    funds: list[FundStatus]


class StatusCounts(BaseModel):
    PENDING: int
    APPROVED: int
    REJECTED: int
    PAID: int


class FundSummary(FundStatus):
    counts_by_status: StatusCounts


class FundSummaryList(BaseModel):
    funds: list[FundSummary]


class Request(BaseModel):
    id: str
    participant_id: str
    fund_id: str
    category: str
    purpose: str
    amount: str
    amount_cents: int
    status: str = Field(description="PENDING | APPROVED | REJECTED | PAID")
    created_by: str
    created_at: str
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    decision_reason: Optional[str] = None
    paid_by: Optional[str] = None
    paid_at: Optional[str] = None


class RequestResult(BaseModel):
    request: Request


class CreateResult(BaseModel):
    request: Request
    replayed: bool = Field(description="true if this was a safe retry returning the original request")


class RequestWithFund(BaseModel):
    request: Request
    fund: FundStatus


class RequestList(BaseModel):
    requests: list[Request]