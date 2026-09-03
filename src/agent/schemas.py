from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class QueryIntent(str, Enum):
    EXACT_LOOKUP = "exact_lookup"
    SEMANTIC_QA = "semantic_qa"
    COMPARE = "compare"
    AGGREGATE = "aggregate"
    LEGAL_STATUS = "legal_status"


class QueryPlan(BaseModel):
    intent: QueryIntent = QueryIntent.SEMANTIC_QA
    sub_queries: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    required_evidence: list[str] = Field(default_factory=list)
    document_refs: list[str] = Field(default_factory=list)
    max_attempts: int = Field(default=2, ge=1, le=3)


class Evidence(BaseModel):
    evidence_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0


class ClaimAssessment(BaseModel):
    claim: str
    status: str
    evidence_ids: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    answer: str
    confidence: str = "thap"
    claims: list[ClaimAssessment] = Field(default_factory=list)


class AgentResult(BaseModel):
    query: str
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    confidence: str = "thap"
    attempts: int = 1
    plan: QueryPlan
    relations: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None
