"""Proveedor Google ADK con Gemini. ADK se importa aquí, así que el backend arranca aunque falle.

Cada llamada crea un LlmAgent con output_schema (salida JSON validada por Pydantic) y una
sesión efímera en memoria. No se guarda estado entre llamadas. El agente solo devuelve datos;
quien escribe en la base es AgentRunner.

La generación desde documentos usa dos agentes: el generador tiene como herramienta
(AgentTool) al agente de documentos, que lista y lee la carpeta o el bucket del proyecto
con herramientas de solo lectura (app/agents/document_tools.py).
"""

from __future__ import annotations

import asyncio
import inspect
import os
import uuid

from typing import Literal

from pydantic import BaseModel, Field

from app.agents.ports import (
    AgentNotConfigured,
    ContextExtractionInput,
    ContextGraphOutput,
    ContextReport,
    DocumentGenerationInput,
    GenerationInput,
    GenerationOutput,
    RefinementInput,
    RefinementOutput,
    ReviewAnalysis,
    ReviewArchitecture,
    ReviewEstimation,
    ReviewInput,
    ReviewOutput,
    ReviewQA,
    SizingInput,
    SizingOutput,
    Usage,
)
from app.agents.prompts import (
    CONTEXT_ENRICHER_INSTRUCTION,
    CONTEXT_EXTRACTOR_INSTRUCTION,
    DOCUMENT_AGENT_INSTRUCTION,
    FPA_ACT_INSTRUCTION,
    FPA_CONCEPT_INSTRUCTION,
    GENERATOR_FROM_DOCS_INSTRUCTION,
    GENERATOR_INSTRUCTION,
    REFINER_INSTRUCTION,
    REVIEW_ANALYST_INSTRUCTION,
    REVIEW_ARCHITECTURE_INSTRUCTION,
    REVIEW_ESTIMATION_INSTRUCTION,
    REVIEW_QA_INSTRUCTION,
)
from app.services import context_graph


# Esquemas que ve el modelo. Gemini rechaza restricciones como minLength/maxLength/maxItems
# (400 INVALID_ARGUMENT), así que aquí van sin longitudes. Las reglas de negocio se aplican
# después, validando la respuesta con los DTO estrictos de ports.py.


class _LlmStory(BaseModel):
    epic: str | None = Field(None, description="Épica a la que pertenece la HU")
    title: str = Field(description="Título corto de la HU")
    role: str | None = Field(None, description="Rol: 'como <rol>'")
    need: str | None = Field(None, description="Necesidad: 'quiero <necesidad>'")
    benefit: str | None = Field(None, description="Beneficio: 'para <beneficio>'")
    acceptance_criteria: str | None = Field(None, description="Criterios en markdown: checklist '- [ ]' o Gherkin")
    type: Literal["HU_IA", "HU_TRADICIONAL"] | None = Field(None, description="Tipo de HU")
    type_justification: str | None = Field(None, description="Justificación del tipo")
    source_section: str | None = Field(None, description="Página, sección o actividad del documento de origen")
    source_document: str | None = Field(None, description="Nombre del documento de origen (solo al leer varios documentos)")


class _LlmGenerationOutput(BaseModel):
    stories: list[_LlmStory] = Field(default_factory=list, description="HU propuestas")


class _LlmRefinementOutput(BaseModel):
    title: str | None = Field(None, description="Título refinado o el original")
    role: str | None = None
    need: str | None = None
    benefit: str | None = None
    acceptance_criteria: str | None = Field(None, description="Criterios en markdown (checklist)")
    notes: str | None = Field(None, description="Qué se cambió y por qué, en una o dos frases")


class _LlmConcept(BaseModel):
    name: str = Field(description="Nombre de la entidad o transacción")
    kind: Literal["entidad", "transaccion"] = Field(description="entidad o transaccion")
    evidence: str | None = Field(None, description="Frase de la HU que lo respalda")


class _LlmConcepts(BaseModel):
    concepts: list[_LlmConcept] = Field(default_factory=list, description="Conceptos de la HU")


class _LlmFunction(BaseModel):
    name: str = Field(description="Nombre de la función")
    type: Literal["ILF", "EIF", "EI", "EO", "EQ"] = Field(description="Tipo IFPUG")
    det: int = Field(description="Campos reconocibles por el usuario")
    ret: int | None = Field(None, description="Solo ILF/EIF: subgrupos de datos")
    ftr: int | None = Field(None, description="Solo EI/EO/EQ: archivos leídos o mantenidos")
    reused: bool = Field(False, description="true si la ILF/EIF ya está en el registro del proyecto")
    rationale: str | None = Field(None, description="Por qué este tipo y estos conteos, en una frase")


class _LlmFunctions(BaseModel):
    functions: list[_LlmFunction] = Field(default_factory=list, description="Funciones IFPUG de la HU")
    rationale: str | None = Field(None, description="Decisiones dudosas, en una o dos frases")


class _LlmGraphNode(BaseModel):
    id: str = Field(description="id estable: minúsculas, dígitos y guiones")
    kind: Literal["feature", "entity", "constraint"] = Field(description="Tipo de nodo")
    name: str = Field(description="Nombre")
    description: str | None = None
    status: Literal["existente", "planificado"] | None = Field(None, description="Solo feature y entity")
    category: str | None = Field(None, description="Solo constraint: seguridad, privacidad, rendimiento, ...")
    severity: Literal["baja", "media", "alta", "critica"] | None = Field(None, description="Solo constraint")
    rule: str | None = Field(None, description="Solo constraint: regla verificable")
    keywords: list[str] = Field(default_factory=list, description="Solo constraint: palabras que la delatan")
    source: str | None = Field(None, description="Documento y sección de donde sale")


class _LlmGraphEdge(BaseModel):
    source: str = Field(description="id del nodo origen")
    target: str = Field(description="id del nodo destino")
    type: Literal["depends_on", "uses", "applies_to"]


class _LlmGraph(BaseModel):
    nodes: list[_LlmGraphNode] = Field(default_factory=list)
    edges: list[_LlmGraphEdge] = Field(default_factory=list)
    notes: str | None = Field(None, description="Suposiciones y dudas")


class _LlmFeatureHit(BaseModel):
    node_id: str
    how: str | None = None


class _LlmConstraintHit(BaseModel):
    constraint_id: str
    reason: str | None = None


class _LlmContextReport(BaseModel):
    impacted_features: list[_LlmFeatureHit] = Field(default_factory=list)
    existing_entities: list[str] = Field(default_factory=list, description="ids existentes que la HU reusa")
    triggered_constraints: list[_LlmConstraintHit] = Field(default_factory=list)
    violations: list[_LlmConstraintHit] = Field(default_factory=list)
    summary: str | None = None


_REVIEW_STATUS = Literal["APPROVED", "APPROVED_WITH_OBSERVATIONS", "NEEDS_CHANGES", "CRITICAL_RISK"]


class _LlmQuestion(BaseModel):
    question: str
    blocking: bool = Field(False, description="true solo si sin la respuesta no se puede implementar o probar")


class _LlmInvest(BaseModel):
    independent: int = Field(description="0-5")
    negotiable: int = Field(description="0-5")
    valuable: int = Field(description="0-5")
    estimable: int = Field(description="0-5")
    small: int = Field(description="0-5")
    testable: int = Field(description="0-5")


class _LlmAnalysis(BaseModel):
    summary: str = Field(description="2 o 3 frases")
    invest: _LlmInvest
    ambiguities: list[str] = Field(default_factory=list)
    questions: list[_LlmQuestion] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    suggested_changes: list[str] = Field(default_factory=list, description="«campo: cambio (razón)»")
    improved_title: str | None = None
    improved_role: str | None = None
    improved_need: str | None = None
    improved_benefit: str | None = None
    improved_criteria: list[str] = Field(default_factory=list, description="Criterios verificables, uno por elemento")
    requires_hitl: bool = False
    hitl_reason: str | None = None
    confidence: float = Field(description="0 a 1")


class _LlmArchitecture(BaseModel):
    summary: str
    impacts: list[str] = Field(default_factory=list)
    critical_changes: list[Literal[
        "ARCHITECTURE_CHANGE", "COMPONENT_REMOVAL", "DATA_MIGRATION", "PUBLIC_API_CHANGE", "SECURITY_MODEL_CHANGE",
        "SIGNIFICANT_COST_INCREASE", "IRREVERSIBLE_CHANGE", "DATA_DELETION", "CROSS_PROJECT_IMPACT",
    ]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    status: _REVIEW_STATUS
    requires_hitl: bool = False
    hitl_reason: str | None = None
    confidence: float = Field(description="0 a 1")


class _LlmTestCase(BaseModel):
    title: str
    type: Literal["POSITIVE", "NEGATIVE", "EDGE_CASE", "INTEGRATION", "DATA_VALIDATION", "SECURITY"]
    criterion: str | None = None
    given: str
    when: str
    then: str


class _LlmQA(BaseModel):
    summary: str
    test_cases: list[_LlmTestCase] = Field(default_factory=list)
    untestable_criteria: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    status: _REVIEW_STATUS
    confidence: float = Field(description="0 a 1")


class _LlmEstimation(BaseModel):
    story_type: Literal["HU_IA", "HU_TRADICIONAL"]
    type_justification: str
    datos: int | None = Field(None, description="HU_IA, 1-5")
    modelo: int | None = Field(None, description="HU_IA, 1-5")
    evaluacion: int | None = Field(None, description="HU_IA, 1-5")
    riesgo: int | None = Field(None, description="HU_IA, 1-5")
    integracion: int | None = Field(None, description="HU_IA, 1-5")
    complejidad: int | None = Field(None, description="HU_TRADICIONAL, 1-5")
    esfuerzo: int | None = Field(None, description="HU_TRADICIONAL, 1-5")
    incertidumbre: int | None = Field(None, description="HU_TRADICIONAL, 1-5")
    rationale: str
    confidence: float = Field(description="0 a 1")


def review_user_text(data: ReviewInput) -> str:
    """Mensaje que reciben los cuatro agentes de la revisión (también lo usa el stub en las pruebas)."""
    s = data.story
    partes = [
        f"Proyecto: {data.project_key} — {data.project_name}",
        f"Descripción del proyecto: {data.project_description}" if data.project_description else "",
        f"\nHU {s.get('code')} (épica: {s.get('epic') or 'sin épica'}, tipo actual: {s.get('type') or 'sin tipo'}, "
        f"estado: {s.get('status')}, origen: {s.get('origin')})",
        f"Título: {s.get('title')}",
        f"Rol: {s.get('role') or ''}",
        f"Necesidad: {s.get('need') or ''}",
        f"Beneficio: {s.get('benefit') or ''}",
        f"Criterios (markdown):\n<criterios>\n{s.get('acceptance_criteria') or ''}\n</criterios>",
        f"Fuente: {s.get('source_ref')}" if s.get("source_ref") else "",
    ]
    if data.other_stories:
        partes.append("\nOtras HU del proyecto (para dependencias y solapes):\n" + "\n".join(f"- {x}" for x in data.other_stories))
    if data.context:
        partes.append(f"\n<contexto_del_proyecto>\n{data.context}\n</contexto_del_proyecto>")
    if data.human_notes:
        partes.append("\nNotas y respuestas del equipo (prioridad alta):\n" + "\n".join(f"- {x}" for x in data.human_notes))
    return "\n".join(p for p in partes if p)


def _check_adc() -> None:
    """Vertex AI usa Application Default Credentials: si no hay, el proveedor queda «no configurado»."""
    try:
        import google.auth

        google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    except Exception as exc:  # noqa: BLE001 - DefaultCredentialsError u otro problema de credenciales
        raise AgentNotConfigured(
            "Sin credenciales de GCP para Vertex AI: ejecuta `gcloud auth application-default login` "
            f"o define GOOGLE_APPLICATION_CREDENTIALS ({type(exc).__name__})"
        ) from exc


class AdkProvider:
    name = "adk"

    def __init__(self, model: str, api_key: str | None, llm=None, vertexai: bool = False,
                 project: str | None = None, location: str | None = None):
        """`llm` permite inyectar un BaseLlm (pruebas sin red). En producción se usa el nombre de Gemini.

        Dos formas de llegar a Gemini:
        - Gemini API (Google AI Studio): `api_key`.
        - Vertex AI (`vertexai=True`): proyecto y región de GCP, con Application Default Credentials
          (`gcloud auth application-default login` o una cuenta de servicio). No usa clave.
        """
        try:
            from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
            from google.adk.apps import App
            from google.adk.plugins.base_plugin import BasePlugin
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
            from google.adk.tools.agent_tool import AgentTool
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise AgentNotConfigured("google-adk no está instalado (revisa requirements.txt)") from exc
        # google-genai lee estas variables del entorno del proceso, no del archivo .env de pydantic.
        if vertexai:
            if not project:
                raise AgentNotConfigured("Vertex AI necesita GOOGLE_CLOUD_PROJECT (proyecto de GCP con la API de Vertex AI)")
            if llm is None:
                _check_adc()
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
            os.environ["GOOGLE_CLOUD_PROJECT"] = project
            os.environ["GOOGLE_CLOUD_LOCATION"] = location or "us-central1"
            self.backend = f"Vertex AI · {project} · {location or 'us-central1'}"
        else:
            if not api_key:
                raise AgentNotConfigured(
                    "Falta GOOGLE_API_KEY (Gemini API) o GOOGLE_GENAI_USE_VERTEXAI=TRUE con GOOGLE_CLOUD_PROJECT (Vertex AI)")
            os.environ.setdefault("GOOGLE_API_KEY", api_key)
            os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
            self.backend = "Gemini API"
        self._model_ref = llm if llm is not None else model
        self.model = model if isinstance(model, str) else getattr(model, "model", None)
        self._LlmAgent = LlmAgent
        self._SequentialAgent = SequentialAgent
        self._ParallelAgent = ParallelAgent
        self._App = App
        self._BasePlugin = BasePlugin
        self._AgentTool = AgentTool
        self._Runner = Runner
        self._sessions_cls = InMemorySessionService
        self._types = types

    def _usage_plugin(self, usage: Usage):
        """Cuenta tokens de TODAS las llamadas al modelo, también las del agente que corre como AgentTool.

        AgentTool ejecuta su agente en un Runner propio cuyos eventos no llegan aquí, pero hereda
        los plugins del Runner padre: por eso se cuenta en un plugin y no leyendo los eventos.
        """

        class _UsagePlugin(self._BasePlugin):
            async def after_model_callback(self, *, callback_context, llm_response):
                meta = llm_response.usage_metadata
                if meta is not None:
                    usage.tokens_in += meta.prompt_token_count or 0
                    usage.tokens_out += meta.candidates_token_count or 0
                return None

        return _UsagePlugin(name="spb_usage")

    def _call(
        self, instruction: str, llm_schema: type[BaseModel], schema: type[BaseModel], user_text: str, tools=None
    ):
        agent = self._LlmAgent(
            name="spb_agent",
            model=self._model_ref,
            instruction=instruction,
            output_schema=llm_schema,
            tools=tools or [],
        )
        final_text, _state, usage = self._execute(agent, user_text)
        # Validación estricta del lado del backend (longitudes, enums, límites).
        return schema.model_validate_json(final_text), usage

    def _execute(self, agent, user_text: str) -> tuple[str, dict, Usage]:
        """Ejecuta un agente (simple o compuesto) en una sesión efímera. Devuelve la última respuesta final,
        el estado de la sesión (output_key de los subagentes) y el uso de tokens de todas las llamadas."""
        usage = Usage(model=self.model)
        sessions = self._sessions_cls()
        app = self._App(name="spb", root_agent=agent, plugins=[self._usage_plugin(usage)])
        runner = self._Runner(app=app, session_service=sessions)

        async def _run():
            session_id = uuid.uuid4().hex
            created = sessions.create_session(app_name="spb", user_id="spb", session_id=session_id)
            if inspect.isawaitable(created):
                await created
            message = self._types.Content(role="user", parts=[self._types.Part(text=user_text)])
            final_text = None
            async for event in runner.run_async(user_id="spb", session_id=session_id, new_message=message):
                if event.is_final_response() and event.content and event.content.parts:
                    final_text = "".join(part.text or "" for part in event.content.parts)
            if not final_text:
                raise RuntimeError("El modelo no devolvió una respuesta final")
            session = sessions.get_session(app_name="spb", user_id="spb", session_id=session_id)
            if inspect.isawaitable(session):
                session = await session
            return final_text, dict(session.state) if session else {}, usage

        return asyncio.run(_run())

    def generate_stories(self, data: GenerationInput) -> tuple[GenerationOutput, Usage]:
        user_text = (
            f"Proyecto: {data.project_key} — {data.project_name}\n"
            f"Fuente: {data.source_name}\n"
            + (f"Instrucciones adicionales: {data.instructions}\n" if data.instructions else "")
            + f"\n<documento>\n{data.text}\n</documento>"
        )
        return self._call(GENERATOR_INSTRUCTION, _LlmGenerationOutput, GenerationOutput, user_text)

    def generate_from_documents(self, data: DocumentGenerationInput, tools) -> tuple[GenerationOutput, Usage]:
        document_agent = self._LlmAgent(
            name="agente_documentos",
            model=self._model_ref,
            description=(
                "Agente de documentos del proyecto: lista y lee los documentos de su carpeta o bucket "
                "(PDF, DOCX, BPMN, MD, TXT) y responde con su contenido citando documento y sección. "
                "Pídele qué documentos hay o qué dice un documento."
            ),
            instruction=DOCUMENT_AGENT_INSTRUCTION,
            tools=tools.functions,
        )
        user_text = (
            f"Proyecto: {data.project_key} — {data.project_name}\n"
            + (f"Instrucciones adicionales: {data.instructions}\n" if data.instructions else "")
            + "Genera las HU a partir de los documentos del proyecto. Consulta a agente_documentos."
        )
        return self._call(
            GENERATOR_FROM_DOCS_INSTRUCTION, _LlmGenerationOutput, GenerationOutput, user_text,
            tools=[self._AgentTool(agent=document_agent)],
        )

    def refine_story(self, data: RefinementInput) -> tuple[RefinementOutput, Usage]:
        story = data.story
        user_text = (
            f"HU {story.get('code')} (épica: {story.get('epic') or 'sin épica'}, tipo: {story.get('type') or 'sin tipo'})\n"
            f"Título: {story.get('title')}\n"
            f"Rol: {story.get('role') or ''}\n"
            f"Necesidad: {story.get('need') or ''}\n"
            f"Beneficio: {story.get('benefit') or ''}\n"
            f"Criterios actuales (markdown):\n<criterios>\n{story.get('acceptance_criteria') or ''}\n</criterios>\n"
            + (f"Instrucciones adicionales: {data.instructions}\n" if data.instructions else "")
        )
        return self._call(REFINER_INSTRUCTION, _LlmRefinementOutput, RefinementOutput, user_text)

    def size_story(self, data: SizingInput) -> tuple[SizingOutput, Usage]:
        """Puntos de función en dos pasos (ConceptAct): conceptos → clasificación y conteos.

        SequentialAgent con dos LlmAgent: el primero guarda sus conceptos en el estado (`fpa_concepts`) y el
        segundo los recibe en su instrucción. Ninguno calcula puntos: lo hace services/fpa.py.
        """
        story = data.story
        registry = ", ".join(data.registry) if data.registry else "(vacío: ninguna HU anterior contó entidades)"
        user_text = (
            f"HU {story.get('code')} (épica: {story.get('epic') or 'sin épica'}, tipo: {story.get('type') or 'sin tipo'})\n"
            f"Título: {story.get('title')}\n"
            f"Rol: {story.get('role') or ''}\n"
            f"Necesidad: {story.get('need') or ''}\n"
            f"Beneficio: {story.get('benefit') or ''}\n"
            f"Criterios (markdown):\n<criterios>\n{story.get('acceptance_criteria') or ''}\n</criterios>\n"
            f"Registro de entidades ya contadas en el proyecto (ILF/EIF): {registry}\n"
            + (f"Instrucciones adicionales: {data.instructions}\n" if data.instructions else "")
        )
        steps = []
        if data.context_graph:
            # Paso 0: el arquitecto contrasta la HU con el grafo aprobado (funcionalidades, entidades, restricciones).
            user_text += (
                f"\n<grafo_de_contexto version=\"{data.context_graph.get('version')}\">\n"
                f"{context_graph.render(data.context_graph)}\n</grafo_de_contexto>\n"
            )
            steps.append(self._LlmAgent(
                name="context_enricher_agent", model=self._model_ref, instruction=CONTEXT_ENRICHER_INSTRUCTION,
                output_schema=_LlmContextReport, output_key="context_report",
            ))
        pipeline = self._SequentialAgent(
            name="fpa_pipeline",
            sub_agents=[
                *steps,
                self._LlmAgent(
                    name="fpa_concept_agent", model=self._model_ref, instruction=FPA_CONCEPT_INSTRUCTION,
                    output_schema=_LlmConcepts, output_key="fpa_concepts",
                ),
                self._LlmAgent(
                    name="fpa_act_agent", model=self._model_ref, instruction=FPA_ACT_INSTRUCTION,
                    output_schema=_LlmFunctions, output_key="fpa_functions",
                ),
            ],
        )
        final_text, state, usage = self._execute(pipeline, user_text)
        functions = _LlmFunctions.model_validate_json(final_text)
        concepts = state.get("fpa_concepts") or {}
        if isinstance(concepts, str):
            concepts = _LlmConcepts.model_validate_json(concepts).model_dump()
        report = state.get("context_report") if data.context_graph else None
        if isinstance(report, str):
            report = _LlmContextReport.model_validate_json(report).model_dump()
        # Validación estricta con los DTO de ports.py (longitudes, rangos).
        return SizingOutput(
            concepts=concepts.get("concepts", []),
            functions=[f.model_dump() for f in functions.functions],
            rationale=functions.rationale,
            context=ContextReport.model_validate(report) if report else None,
        ), usage

    def extract_context(self, data: ContextExtractionInput, tools) -> tuple[ContextGraphOutput, Usage]:
        """El arquitecto lee los documentos del proyecto con las herramientas de solo lectura y propone el grafo."""
        user_text = (
            f"Proyecto: {data.project_key} — {data.project_name}\n"
            + (f"Instrucciones adicionales: {data.instructions}\n" if data.instructions else "")
            + "Construye el grafo de contexto a partir de los documentos del proyecto."
        )
        return self._call(
            CONTEXT_EXTRACTOR_INSTRUCTION, _LlmGraph, ContextGraphOutput, user_text, tools=tools.functions,
        )

    def review_story(self, data: ReviewInput) -> tuple[ReviewOutput, Usage]:
        """Revisión multiagente: analista → (arquitectura ∥ QA ∥ estimación), como en hu_multiagent.

        Cada agente deja su JSON en el estado de la sesión (output_key). Una salida que no valida queda en None
        con su error: la política HITL lo trata como agente faltante y detiene la HU para una persona.
        """
        def agent(name, instruction, schema, key):
            return self._LlmAgent(name=name, model=self._model_ref, instruction=instruction,
                                  output_schema=schema, output_key=key)

        pipeline = self._SequentialAgent(
            name="revision_hu",
            sub_agents=[
                agent("analista_hu", REVIEW_ANALYST_INSTRUCTION, _LlmAnalysis, "analysis"),
                self._ParallelAgent(name="revision_paralela", sub_agents=[
                    agent("arquitectura_hu", REVIEW_ARCHITECTURE_INSTRUCTION, _LlmArchitecture, "architecture"),
                    agent("qa_hu", REVIEW_QA_INSTRUCTION, _LlmQA, "qa"),
                    agent("estimacion_hu", REVIEW_ESTIMATION_INSTRUCTION, _LlmEstimation, "estimation"),
                ]),
            ],
        )
        _final, state, usage = self._execute(pipeline, review_user_text(data))
        return parse_review_state(state), usage


def parse_review_state(state: dict) -> ReviewOutput:
    """Valida con los DTO estrictos lo que cada agente dejó en el estado (dict o texto JSON)."""
    out, errors = {}, {}
    for key, dto in (("analysis", ReviewAnalysis), ("architecture", ReviewArchitecture), ("qa", ReviewQA),
                     ("estimation", ReviewEstimation)):
        raw = state.get(key)
        if raw is None:
            errors[key] = "sin salida"
            continue
        try:
            out[key] = dto.model_validate_json(raw) if isinstance(raw, str) else dto.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - salida inválida: el HITL la trata como faltante
            errors[key] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return ReviewOutput(**out, errors=errors)
