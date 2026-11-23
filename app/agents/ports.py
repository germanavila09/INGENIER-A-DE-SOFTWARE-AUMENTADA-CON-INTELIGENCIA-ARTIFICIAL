"""Contrato entre el backend y cualquier proveedor de IA (ADK, stub, ...).

Los proveedores devuelven DTOs validados por Pydantic. Nunca escriben en la base:
quien persiste es `AgentRunner`, que aplica las reglas de HITL y de estados.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.agents.document_tools import DocumentTools


class AgentNotConfigured(Exception):
    """El proveedor configurado no puede usarse (falta clave, paquete o proveedor 'none')."""


class GeneratedStory(BaseModel):
    epic: str | None = Field(None, max_length=255, description="Épica a la que pertenece la HU")
    title: str = Field(min_length=3, max_length=255)
    role: str | None = Field(None, description="Rol: 'como <rol>'")
    need: str | None = Field(None, description="Necesidad: 'quiero <necesidad>'")
    benefit: str | None = Field(None, description="Beneficio: 'para <beneficio>'")
    acceptance_criteria: str | None = Field(None, max_length=20000, description="Criterios en markdown: checklist '- [ ]' o Gherkin")
    type: Literal["HU_IA", "HU_TRADICIONAL"] | None = None
    type_justification: str | None = None
    source_section: str | None = Field(None, description="Página, sección o proceso del documento de origen")
    source_document: str | None = Field(None, max_length=255, description="Documento de origen (generación desde varios documentos)")


class GenerationOutput(BaseModel):
    stories: list[GeneratedStory] = Field(default_factory=list, max_length=200)


class RefinementOutput(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=255)
    role: str | None = None
    need: str | None = None
    benefit: str | None = None
    acceptance_criteria: str | None = Field(None, max_length=20000)
    notes: str | None = None


class GenerationInput(BaseModel):
    project_key: str
    project_name: str
    source_name: str
    text: str
    instructions: str | None = None


class DocumentGenerationInput(BaseModel):
    """Generación desde todos los documentos del proyecto: el agente decide qué leer con sus herramientas."""

    project_key: str
    project_name: str
    instructions: str | None = None


class RefinementInput(BaseModel):
    story: dict
    instructions: str | None = None


class FpaConcept(BaseModel):
    """Paso «Concept» (ConceptAct): qué entidades y transacciones describe la HU, con evidencia."""

    name: str = Field(min_length=1, max_length=200)
    kind: Literal["entidad", "transaccion"]
    evidence: str | None = Field(None, max_length=500, description="Cita o paráfrasis de la HU que lo respalda")


class FpaFunction(BaseModel):
    """Paso «Act»: una función IFPUG con sus conteos. El backend calcula complejidad y puntos."""

    name: str = Field(min_length=1, max_length=200)
    type: Literal["ILF", "EIF", "EI", "EO", "EQ"]
    det: int = Field(ge=1)
    ret: int | None = Field(None, ge=1, description="Solo ILF/EIF")
    ftr: int | None = Field(None, ge=0, description="Solo EI/EO/EQ")
    reused: bool = False
    rationale: str | None = Field(None, max_length=500)


class ImpactedFeature(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    how: str | None = Field(None, max_length=300)


class ConstraintHit(BaseModel):
    constraint_id: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(None, max_length=500)


class ContextReport(BaseModel):
    """Informe del agente de contexto: cómo se relaciona la HU con el grafo aprobado del proyecto."""

    impacted_features: list[ImpactedFeature] = Field(default_factory=list, max_length=100)
    existing_entities: list[str] = Field(default_factory=list, max_length=100, description="ids de entidades o funcionalidades existentes que la HU reusa")
    triggered_constraints: list[ConstraintHit] = Field(default_factory=list, max_length=100)
    violations: list[ConstraintHit] = Field(default_factory=list, max_length=100)
    summary: str | None = Field(None, max_length=2000)


class SizingInput(BaseModel):
    story: dict
    registry: list[str] = Field(default_factory=list, description="ILF/EIF ya contados por HU anteriores")
    instructions: str | None = None
    context_graph: dict | None = Field(None, description="Grafo de contexto aprobado: {version, nodes, edges}")


class SizingOutput(BaseModel):
    concepts: list[FpaConcept] = Field(default_factory=list, max_length=200)
    functions: list[FpaFunction] = Field(default_factory=list, max_length=100)
    rationale: str | None = Field(None, max_length=2000)
    context: ContextReport | None = None

    def components(self) -> list[dict]:
        """Componentes para services/fpa.py (sin campos vacíos)."""
        return [f.model_dump(exclude_none=True) for f in self.functions]


class GraphNode(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["feature", "entity", "constraint"]
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    status: Literal["existente", "planificado"] | None = None
    category: str | None = None
    severity: Literal["baja", "media", "alta", "critica"] | None = None
    rule: str | None = Field(None, max_length=1000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    source: str | None = Field(None, max_length=300)


class GraphEdge(BaseModel):
    source: str
    target: str
    type: Literal["depends_on", "uses", "applies_to"]


class ContextGraphOutput(BaseModel):
    """Grafo propuesto por el agente. services/context_graph.py lo valida antes de guardarlo como borrador."""

    nodes: list[GraphNode] = Field(default_factory=list, max_length=300)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=1000)
    notes: str | None = Field(None, max_length=2000)

    def as_graph(self) -> tuple[list[dict], list[dict]]:
        return [n.model_dump(exclude_none=True) for n in self.nodes], [e.model_dump() for e in self.edges]


class ContextExtractionInput(BaseModel):
    project_key: str
    project_name: str
    instructions: str | None = None


class Usage(BaseModel):
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class AgentProvider(Protocol):
    name: str
    model: str | None

    def generate_stories(self, data: GenerationInput) -> tuple[GenerationOutput, Usage]: ...

    def refine_story(self, data: RefinementInput) -> tuple[RefinementOutput, Usage]: ...

    def size_story(self, data: SizingInput) -> tuple[SizingOutput, Usage]: ...

    def extract_context(
        self, data: ContextExtractionInput, tools: "DocumentTools"
    ) -> tuple[ContextGraphOutput, Usage]: ...

    def generate_from_documents(
        self, data: DocumentGenerationInput, tools: "DocumentTools"
    ) -> tuple[GenerationOutput, Usage]: ...

    def review_story(self, data: "ReviewInput") -> tuple["ReviewOutput", Usage]: ...


# ---------- revisión multiagente de una HU (HITL) ----------
# Cuatro agentes revisan la HU: analista → (arquitectura ∥ QA ∥ estimación). Ninguno decide el nivel HITL:
# lo calcula services/hitl.py con reglas en código. Estos DTO validan la salida del modelo.

ReviewStatus = Literal["APPROVED", "APPROVED_WITH_OBSERVATIONS", "NEEDS_CHANGES", "CRITICAL_RISK"]
CriticalChange = Literal[
    "ARCHITECTURE_CHANGE", "COMPONENT_REMOVAL", "DATA_MIGRATION", "PUBLIC_API_CHANGE", "SECURITY_MODEL_CHANGE",
    "SIGNIFICANT_COST_INCREASE", "IRREVERSIBLE_CHANGE", "DATA_DELETION", "CROSS_PROJECT_IMPACT",
]


class ReviewQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    blocking: bool = False


class InvestScore(BaseModel):
    independent: int = Field(ge=0, le=5)
    negotiable: int = Field(ge=0, le=5)
    valuable: int = Field(ge=0, le=5)
    estimable: int = Field(ge=0, le=5)
    small: int = Field(ge=0, le=5)
    testable: int = Field(ge=0, le=5)


class ReviewAnalysis(BaseModel):
    summary: str = Field(max_length=3000)
    invest: InvestScore
    ambiguities: list[str] = Field(default_factory=list, max_length=30)
    questions: list[ReviewQuestion] = Field(default_factory=list, max_length=30)
    risks: list[str] = Field(default_factory=list, max_length=30)
    suggested_changes: list[str] = Field(default_factory=list, max_length=30)
    improved_title: str | None = Field(None, max_length=255)
    improved_role: str | None = None
    improved_need: str | None = None
    improved_benefit: str | None = None
    improved_criteria: list[str] = Field(default_factory=list, max_length=40)
    requires_hitl: bool = False
    hitl_reason: str | None = None
    confidence: float = Field(ge=0, le=1)


class ReviewArchitecture(BaseModel):
    summary: str = Field(max_length=3000)
    impacts: list[str] = Field(default_factory=list, max_length=30)
    critical_changes: list[CriticalChange] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list, max_length=30)
    status: ReviewStatus
    requires_hitl: bool = False
    hitl_reason: str | None = None
    confidence: float = Field(ge=0, le=1)


class ReviewTestCase(BaseModel):
    title: str = Field(max_length=300)
    type: Literal["POSITIVE", "NEGATIVE", "EDGE_CASE", "INTEGRATION", "DATA_VALIDATION", "SECURITY"]
    criterion: str | None = None
    given: str
    when: str
    then: str


class ReviewQA(BaseModel):
    summary: str = Field(max_length=3000)
    test_cases: list[ReviewTestCase] = Field(default_factory=list, max_length=40)
    untestable_criteria: list[str] = Field(default_factory=list, max_length=40)
    coverage_gaps: list[str] = Field(default_factory=list, max_length=30)
    status: ReviewStatus
    confidence: float = Field(ge=0, le=1)


class ReviewEstimation(BaseModel):
    """Un campo por factor (Gemini no admite diccionarios libres). Los puntos los calcula services/estimation.py."""

    story_type: Literal["HU_IA", "HU_TRADICIONAL"]
    type_justification: str
    datos: int | None = None
    modelo: int | None = None
    evaluacion: int | None = None
    riesgo: int | None = None
    integracion: int | None = None
    complejidad: int | None = None
    esfuerzo: int | None = None
    incertidumbre: int | None = None
    rationale: str
    confidence: float = Field(ge=0, le=1)


class ReviewInput(BaseModel):
    project_key: str
    project_name: str
    project_description: str | None = None
    story: dict
    other_stories: list[str] = Field(default_factory=list)
    context: str | None = None
    human_notes: list[str] = Field(default_factory=list)


class ReviewOutput(BaseModel):
    """Salida de los cuatro agentes. None = ese agente no produjo una salida válida (el HITL lo detiene)."""

    analysis: ReviewAnalysis | None = None
    architecture: ReviewArchitecture | None = None
    qa: ReviewQA | None = None
    estimation: ReviewEstimation | None = None
    errors: dict[str, str] = Field(default_factory=dict)
