"""Máquina de estados de proyectos e historias (persistida por StateService)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .common import now_iso
from .hitl import DecisionRecord


class WorkflowState(str, Enum):
    DISCOVERED = "DISCOVERED"
    INGESTING = "INGESTING"
    CONTEXT_BUILDING = "CONTEXT_BUILDING"
    ANALYZING = "ANALYZING"
    VALIDATING = "VALIDATING"
    ARCHITECTURE_REVIEW = "ARCHITECTURE_REVIEW"
    DEPENDENCY_ANALYSIS = "DEPENDENCY_ANALYSIS"   # fase 2
    ESTIMATING = "ESTIMATING"                     # fase 2
    TEST_GENERATION = "TEST_GENERATION"           # fase 2 (en el MVP corre dentro de ARCHITECTURE_REVIEW)
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    READY_FOR_IMPLEMENTATION = "READY_FOR_IMPLEMENTATION"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


S = WorkflowState

# Transiciones permitidas de una HISTORIA. Cualquier otra se rechaza.
STORY_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    S.DISCOVERED: {S.ANALYZING, S.ERROR},
    S.ANALYZING: {S.ARCHITECTURE_REVIEW, S.ERROR},
    S.ARCHITECTURE_REVIEW: {S.VALIDATING, S.DEPENDENCY_ANALYSIS, S.ESTIMATING, S.TEST_GENERATION, S.ERROR},
    S.DEPENDENCY_ANALYSIS: {S.ESTIMATING, S.TEST_GENERATION, S.VALIDATING, S.ERROR},
    S.ESTIMATING: {S.TEST_GENERATION, S.VALIDATING, S.ERROR},
    S.TEST_GENERATION: {S.VALIDATING, S.ERROR},
    S.VALIDATING: {S.WAITING_FOR_HUMAN, S.READY_FOR_IMPLEMENTATION, S.ERROR},
    S.WAITING_FOR_HUMAN: {S.APPROVED, S.REJECTED, S.ANALYZING, S.ERROR},
    S.APPROVED: {S.READY_FOR_IMPLEMENTATION, S.ERROR},
    # Si el documento fuente cambia, una historia terminada puede volver a analizarse.
    S.READY_FOR_IMPLEMENTATION: {S.COMPLETED, S.ANALYZING},
    S.REJECTED: {S.ANALYZING},
    S.COMPLETED: {S.ANALYZING},
    S.ERROR: {S.ANALYZING, S.DISCOVERED},
}

# Transiciones permitidas de un PROYECTO.
PROJECT_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    S.DISCOVERED: {S.INGESTING, S.ERROR},
    S.INGESTING: {S.CONTEXT_BUILDING, S.ERROR},
    S.CONTEXT_BUILDING: {S.ANALYZING, S.COMPLETED, S.WAITING_FOR_HUMAN, S.ERROR},
    S.ANALYZING: {S.WAITING_FOR_HUMAN, S.COMPLETED, S.INGESTING, S.ERROR},
    S.WAITING_FOR_HUMAN: {S.ANALYZING, S.INGESTING, S.COMPLETED, S.ERROR},
    S.COMPLETED: {S.INGESTING, S.ANALYZING},
    S.ERROR: {S.INGESTING, S.DISCOVERED},
}

STORY_TERMINAL = {S.READY_FOR_IMPLEMENTATION, S.REJECTED, S.COMPLETED}
STORY_IN_PROGRESS = {S.ANALYZING, S.ARCHITECTURE_REVIEW, S.DEPENDENCY_ANALYSIS, S.ESTIMATING,
                     S.TEST_GENERATION, S.VALIDATING, S.APPROVED}


class InvalidTransition(Exception):
    pass


def check_transition(table: dict, current: WorkflowState, target: WorkflowState, what: str) -> None:
    if current == target:
        return
    if target not in table.get(current, set()):
        raise InvalidTransition(f"{what}: transición no permitida {current.value} → {target.value}")


class Transition(BaseModel):
    from_state: WorkflowState
    to_state: WorkflowState
    at: str = Field(default_factory=now_iso)
    actor: str = Field(description="Agente, servicio o 'human:<usuario>'.")
    reason: str = ""
    run_id: str = ""


class StoryRecord(BaseModel):
    story_id: str
    title: str = ""
    state: WorkflowState = S.DISCOVERED
    hitl_level: int = 0
    review_pending: bool = Field(False, description="HITL nivel 1: continuar, revisar después.")
    pending_decision_id: str = ""
    analysis_iterations: int = 0
    source: str = ""
    source_signature: str = ""
    approved_by_human: bool = False
    origin: str = "document"
    source_changed: bool = False
    original_version_signature: str = ""
    pending_reanalysis: bool = Field(False, description="MODIFIED por un humano: falta reanalizar.")
    reanalysis_count: int = 0
    human_modifications: dict = Field(default_factory=dict)
    human_feedback: str = ""
    last_run_id: str = ""
    quality_score: float | None = None
    confidence: float | None = None
    last_error: str = ""
    history: list[Transition] = Field(default_factory=list)


class RunSummary(BaseModel):
    run_id: str
    started_at: str
    finished_at: str = ""
    trigger: str = ""
    processed: list[str] = Field(default_factory=list)
    ready: list[str] = Field(default_factory=list)
    review: list[str] = Field(default_factory=list)
    waiting: list[str] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
    skipped: list[str] = Field(default_factory=list)
    generation: str = Field("", description="generadas / regeneradas / reutilizadas si se usó el generador.")


class ProjectState(BaseModel):
    project_id: str
    project_name: str = ""
    root_path: str = ""
    state: WorkflowState = S.DISCOVERED
    manifest_version: int = 0
    last_scan: str = ""
    stories: dict[str, StoryRecord] = Field(default_factory=dict)
    decisions: dict[str, DecisionRecord] = Field(default_factory=dict)
    runs: list[RunSummary] = Field(default_factory=list)
    history: list[Transition] = Field(default_factory=list)
    last_error: str = ""

    def pending_decisions(self) -> list[DecisionRecord]:
        return [d for d in self.decisions.values() if d.pending]
