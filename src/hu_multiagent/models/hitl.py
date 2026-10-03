"""Human-In-The-Loop: niveles, evaluación y decisiones humanas."""

from __future__ import annotations

from enum import Enum, IntEnum

from pydantic import BaseModel, Field

from .common import Severity, now_iso


class HitlLevel(IntEnum):
    AUTOMATIC = 0          # continúa solo
    REVIEW = 1             # continúa, marcado para revisión posterior
    APPROVAL_REQUIRED = 2  # se detiene hasta recibir una decisión humana


class HitlAction(str, Enum):
    AUTO_CONTINUE = "AUTO_CONTINUE"
    CONTINUE_WITH_REVIEW = "CONTINUE_WITH_REVIEW"
    STOP_FOR_HUMAN = "STOP_FOR_HUMAN"


class HitlReason(BaseModel):
    code: str = Field(description="Ej.: SECURITY_CHANGE, LOW_CONFIDENCE, AGENT_CONFLICT.")
    description: str
    agent: str
    severity: Severity
    critical: bool = Field(False, description="Crítico = requiere aprobación aunque la confianza sea alta.")


class HitlEvaluation(BaseModel):
    story_id: str
    action: HitlAction
    level: HitlLevel
    confidence: float
    reasons: list[HitlReason] = Field(default_factory=list)
    reversible: bool = True


class HumanDecisionType(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"
    NEEDS_MORE_INFORMATION = "NEEDS_MORE_INFORMATION"


class HitlDecisionRequest(BaseModel):
    """Lo que el sistema devuelve cuando se detiene (status WAITING_FOR_HUMAN)."""

    status: str = "WAITING_FOR_HUMAN"
    project_id: str
    story_id: str
    decision_id: str
    reason: str
    agent: str
    recommendation: str
    alternatives: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    risk: str
    confidence: float
    reasons: list[HitlReason] = Field(default_factory=list)
    blocking_questions: list[str] = Field(default_factory=list)
    options: list[HumanDecisionType] = Field(default_factory=lambda: list(HumanDecisionType))
    created_at: str = Field(default_factory=now_iso)
    run_id: str = ""


class HumanDecision(BaseModel):
    decision_id: str
    decision: HumanDecisionType
    comment: str = ""
    modifications: dict = Field(default_factory=dict)
    decided_by: str = ""
    decided_at: str = Field(default_factory=now_iso)


class DecisionRecord(BaseModel):
    request: HitlDecisionRequest
    resolution: HumanDecision | None = None
    history: list[HumanDecision] = Field(default_factory=list)
    superseded_by_run: str = Field("", description="Se invalidó porque la historia cambió en la fuente.")

    @property
    def pending(self) -> bool:
        if self.superseded_by_run:
            return False
        return self.resolution is None or self.resolution.decision == HumanDecisionType.NEEDS_MORE_INFORMATION
