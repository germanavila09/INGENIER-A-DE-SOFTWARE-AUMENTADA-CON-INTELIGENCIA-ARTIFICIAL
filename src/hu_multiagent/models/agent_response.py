"""Contrato común de respuesta entre agentes.

Los agentes LLM producen su *Output específico (validado por Pydantic) y el código
lo envuelve aquí, de modo que cualquier agente o sistema externo consume siempre
la misma forma.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .common import Finding, Question, Statement


class AgentStatus(str, Enum):
    OK = "OK"
    APPROVED = "APPROVED"
    APPROVED_WITH_OBSERVATIONS = "APPROVED_WITH_OBSERVATIONS"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    CRITICAL_RISK = "CRITICAL_RISK"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class AgentResponse(BaseModel):
    agent: str
    project_id: str
    story_id: str = ""
    status: AgentStatus
    confidence: float = Field(ge=0, le=1)
    requires_hitl: bool = False
    hitl_level: int = Field(0, ge=0, le=2)
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[Statement] = Field(default_factory=list)
    risks: list[Finding] = Field(default_factory=list)
    dependencies: list[Statement] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    next_agent: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AggregatedReview(BaseModel):
    """Salida de REVIEW_AGGREGATOR_AGENT: todas las revisiones de una historia combinadas."""

    project_id: str
    story_id: str
    responses: list[AgentResponse] = Field(default_factory=list)
    conflicts: list[Finding] = Field(default_factory=list)
    overall_status: AgentStatus
    confidence: float = Field(ge=0, le=1, description="Mínimo de las confianzas (criterio conservador).")
    critical_changes: list[str] = Field(default_factory=list)
    blocking_questions: list[Question] = Field(default_factory=list)
    criteria_count: int = 0
    untestable_count: int = 0
    suggested_changes_count: int = 0
    agent_hitl_requests: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    missing_agents: list[str] = Field(default_factory=list)
