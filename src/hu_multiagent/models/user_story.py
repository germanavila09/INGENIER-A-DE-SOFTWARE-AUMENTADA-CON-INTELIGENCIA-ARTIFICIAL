"""Historia de usuario y salidas estructuradas de los agentes LLM.

Las clases *Output son el `output_schema` que Gemini debe respetar; por eso no
tienen diccionarios libres. El código las envuelve después en AgentResponse.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .common import Finding, Question, Statement, StatementType


class UserStory(BaseModel):
    """Historia tal como viene del documento fuente (versión original)."""

    story_id: str
    project_id: str
    title: str = ""
    epic: str = ""
    role: str = ""
    need: str = ""
    benefit: str = ""
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    estimate: float | None = Field(None, description="Story points existentes, si los hay.")
    status: str = ""
    approved: bool = Field(False, description="True si la historia ya fue aprobada por humanos.")
    source: str = ""
    source_format: str = ""
    signature: str = Field("", description="Firma del contenido de la historia (detecta cambios).")
    origin: str = Field("document", description="document = escrita en un documento; generated = propuesta por IA.")
    priority: str = Field("", description="Alta, Media o Baja; vacío si no está definida.")
    priority_reason: str = Field("", description="Por qué se sugiere esa prioridad (si la propuso la IA).")
    evidence: list[Statement] = Field(default_factory=list, description="Citas que respaldan una historia generada.")
    generation_confidence: float | None = None

    def narrative(self) -> str:
        if self.role or self.need:
            return f"Como {self.role} quiero {self.need} para {self.benefit}".strip()
        return self.description


# ----------------------------------------------------------- story_analyst
class InvestScore(BaseModel):
    independent: int = Field(description="0-5")
    negotiable: int = Field(description="0-5")
    valuable: int = Field(description="0-5")
    estimable: int = Field(description="0-5")
    small: int = Field(description="0-5")
    testable: int = Field(description="0-5")

    def clamp(self) -> "InvestScore":
        return InvestScore(**{k: max(0, min(5, int(v))) for k, v in self.model_dump().items()})

    @property
    def quality_score(self) -> float:
        """0-100, calculado en código (no lo decide el LLM)."""
        c = self.clamp()
        return round(sum(c.model_dump().values()) / 30 * 100, 1)


class SuggestedChange(BaseModel):
    field: str = Field(description="title, narrative, acceptance_criteria, business_rules…")
    before: str
    after: str
    reason: str


class ImprovedStory(BaseModel):
    title: str
    narrative: str = Field(description="Como <rol> quiero <necesidad> para <beneficio>.")
    acceptance_criteria: list[str]


class StoryAnalysisOutput(BaseModel):
    story_id: str
    title: str
    epic: str = ""
    role: str = ""
    need: str = ""
    benefit: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    business_rules: list[Statement] = Field(default_factory=list)
    dependencies: list[Statement] = Field(default_factory=list)
    constraints: list[Statement] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    technical_requirements: list[Statement] = Field(default_factory=list)
    data_requirements: list[Statement] = Field(default_factory=list)
    security_requirements: list[Statement] = Field(default_factory=list)
    integration_requirements: list[Statement] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    invest_score: InvestScore
    ambiguities: list[Statement] = Field(default_factory=list)
    missing_information: list[Question] = Field(default_factory=list)
    risks: list[Finding] = Field(default_factory=list)
    suggested_changes: list[SuggestedChange] = Field(default_factory=list)
    improved_story: ImprovedStory
    requires_hitl: bool = False
    hitl_reason: str = ""
    confidence: float = Field(description="0-1: certeza del análisis con la información disponible.")
    summary: str


# ------------------------------------------------------------ architecture
class ReviewStatus(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_WITH_OBSERVATIONS = "APPROVED_WITH_OBSERVATIONS"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    CRITICAL_RISK = "CRITICAL_RISK"


class ArchitectureArea(str, Enum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    API = "api"
    DATABASE = "database"
    EVENTS = "events"
    CLOUD = "cloud"
    SECURITY = "security"
    AUTHENTICATION = "authentication"
    OBSERVABILITY = "observability"
    INFRASTRUCTURE = "infrastructure"
    AI_ML = "ai_ml"


class ChangeType(str, Enum):
    """Cambios que siempre exigen aprobación humana (HITL nivel 2)."""

    ARCHITECTURE_CHANGE = "ARCHITECTURE_CHANGE"
    COMPONENT_REMOVAL = "COMPONENT_REMOVAL"
    DATA_MIGRATION = "DATA_MIGRATION"
    PUBLIC_API_CHANGE = "PUBLIC_API_CHANGE"
    SECURITY_MODEL_CHANGE = "SECURITY_MODEL_CHANGE"
    SIGNIFICANT_COST_INCREASE = "SIGNIFICANT_COST_INCREASE"
    IRREVERSIBLE_CHANGE = "IRREVERSIBLE_CHANGE"
    DATA_DELETION = "DATA_DELETION"
    CROSS_PROJECT_IMPACT = "CROSS_PROJECT_IMPACT"


class ArchitectureImpact(BaseModel):
    area: ArchitectureArea
    description: str
    type: StatementType
    source: str = ""


class ArchitectureReviewOutput(BaseModel):
    story_id: str
    summary: str
    impacts: list[ArchitectureImpact] = Field(default_factory=list)
    critical_changes: list[ChangeType] = Field(default_factory=list)
    recommendations: list[Statement] = Field(default_factory=list)
    risks: list[Finding] = Field(default_factory=list)
    status: ReviewStatus
    requires_hitl: bool = False
    hitl_reason: str = ""
    confidence: float


# ---------------------------------------------------------------------- QA
class QATestType(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    EDGE_CASE = "EDGE_CASE"
    INTEGRATION = "INTEGRATION"
    DATA_VALIDATION = "DATA_VALIDATION"
    SECURITY = "SECURITY"


class QATestCase(BaseModel):
    test_id: str
    title: str
    type: QATestType
    criterion: str = Field(description="Criterio de aceptación que cubre.")
    given: str
    when: str
    then: str


class QATestOutput(BaseModel):
    story_id: str
    summary: str
    test_cases: list[QATestCase] = Field(default_factory=list)
    untestable_criteria: list[Statement] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    status: ReviewStatus
    confidence: float


# -------------------------------------------------------- story_generator
class GeneratedStory(BaseModel):
    story_id: str = Field(description="HU-IA-001, HU-IA-002…")
    title: str
    epic: str = ""
    role: str
    need: str
    benefit: str
    acceptance_criteria: list[str]
    business_rules: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    evidence: list[Statement] = Field(description="Citas breves de los documentos que respaldan la historia.")
    open_questions: list[Question] = Field(default_factory=list)
    priority: str = Field(description="Alta, Media o Baja, según urgencia, valor o dependencia que muestren los documentos.")
    priority_reason: str = Field(description="Justificación breve de la prioridad, basada en la evidencia.")
    confidence: float = Field(description="0-1: qué tan respaldada está la historia por los documentos.")


class StoryGenerationOutput(BaseModel):
    project_summary: str
    epics: list[str] = Field(default_factory=list)
    stories: list[GeneratedStory] = Field(default_factory=list)
    decisions_found: list[Statement] = Field(default_factory=list, description="Decisiones tomadas en los documentos.")
    open_questions: list[Question] = Field(default_factory=list, description="Temas en discusión o información faltante.")
    confidence: float
