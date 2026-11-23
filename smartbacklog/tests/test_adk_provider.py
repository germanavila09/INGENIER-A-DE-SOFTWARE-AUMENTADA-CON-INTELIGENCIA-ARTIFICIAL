"""Prueba el adaptador real de Google ADK (LlmAgent + Runner + sesión + output_schema) sin red.

El modelo es un BaseLlm falso que devuelve JSON canned con uso de tokens. Así se valida
todo el cableado de ADK; lo único que no se prueba aquí es la llamada real a Gemini.
"""

import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.agents import runner
from app.agents.ports import GenerationInput, RefinementInput
from app.agents.providers.adk import AdkProvider
from app.config import settings
from tests.conftest import INGEST_PAYLOAD, login

GENERATION_JSON = {
    "stories": [
        {
            "epic": "Pagos",
            "title": "Pagar con tarjeta",
            "role": "cliente",
            "need": "pagar con tarjeta",
            "benefit": "completar la compra",
            "acceptance_criteria": "- [ ] Muestra error si la tarjeta es rechazada",
            "type": "HU_TRADICIONAL",
            "type_justification": "Integración con pasarela",
            "source_section": "Actividad: Validar tarjeta",
        }
    ]
}

REFINEMENT_JSON = {
    "title": "Pagar con tarjeta de crédito",
    "acceptance_criteria": "- [ ] Muestra error claro\n- [ ] Permite reintentar 3 veces",
    "notes": "Se añadieron reintentos.",
}


class FakeGemini(BaseLlm):
    """Imita a Gemini: devuelve siempre el mismo JSON y reporta tokens."""

    model: str = "fake-gemini-pro"
    payload: str = ""
    tokens_in: int = 120
    tokens_out: int = 45

    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.payload)]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=self.tokens_in, candidates_token_count=self.tokens_out,
            ),
            turn_complete=True,
        )


def provider_with(payload: dict | str, **tokens) -> AdkProvider:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    fake = FakeGemini(payload=text, **tokens)
    return AdkProvider(model="gemini-2.5-pro", api_key="clave-falsa-de-prueba", llm=fake)


def test_generate_stories_through_adk_pipeline():
    provider = provider_with(GENERATION_JSON)
    output, usage = provider.generate_stories(
        GenerationInput(project_key="PAGO", project_name="Pagos", source_name="proceso.bpmn", text="# Pagos")
    )
    assert len(output.stories) == 1
    assert output.stories[0].title == "Pagar con tarjeta"
    assert output.stories[0].acceptance_criteria.startswith("- [ ]")
    assert (usage.tokens_in, usage.tokens_out) == (120, 45)


def test_refine_story_through_adk_pipeline():
    provider = provider_with(REFINEMENT_JSON)
    story = {"code": "PAGO-1", "title": "Pagar", "acceptance_criteria": "- [ ] algo", "version": 2}
    output, _ = provider.refine_story(RefinementInput(story=story))
    assert output.title == "Pagar con tarjeta de crédito"
    assert output.notes == "Se añadieron reintentos."


def test_schema_sent_to_gemini_has_no_length_constraints():
    """Gemini responde 400 si el esquema trae minLength/maxLength/maxItems/pattern."""
    import json as _json

    from app.agents.providers.adk import _LlmGenerationOutput, _LlmRefinementOutput

    for model in (_LlmGenerationOutput, _LlmRefinementOutput):
        text = _json.dumps(model.model_json_schema())
        for forbidden in ("minLength", "maxLength", "minItems", "maxItems", "pattern"):
            assert forbidden not in text, f"{model.__name__} expone {forbidden}"


def test_invalid_model_output_is_a_validation_error():
    from pydantic import ValidationError

    provider = provider_with({"stories": [{"title": "x"}]})  # título demasiado corto
    with pytest.raises(ValidationError):
        provider.generate_stories(GenerationInput(project_key="X", project_name="x", source_name="s", text="t"))


def test_adk_provider_requires_api_key():
    from app.agents.ports import AgentNotConfigured

    with pytest.raises(AgentNotConfigured, match="GOOGLE_API_KEY"):
        AdkProvider(model="gemini-2.5-pro", api_key=None)


@pytest.fixture
def adk_generation(demo_app, monkeypatch, tmp_path):
    client, service_key = demo_app
    monkeypatch.setattr(settings, "agent_provider", "adk")
    monkeypatch.setattr(settings, "agent_execution", "inline")
    monkeypatch.setattr(settings, "agent_price_in_per_1m", 1.25)
    monkeypatch.setattr(settings, "agent_price_out_per_1m", 10.0)
    monkeypatch.setattr(runner, "get_provider", lambda: provider_with(GENERATION_JSON))
    return client


@pytest.fixture
def demo_app(client, service_key):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    return client, service_key


def test_full_generation_run_with_adk_records_tokens_and_cost(adk_generation):
    client = adk_generation
    login(client, "po@demo.co")
    res = client.post("/api/v1/projects/DEMO/agents/generate-stories", json={
        "document_text": "# Pagos\n## Actividades\n- [user] Validar tarjeta (carril: Cliente): datos",
        "source_name": "proceso.bpmn",
    })
    assert res.status_code == 202, res.text
    run = res.json()
    assert run["status"] == "succeeded", run
    assert run["provider"] == "adk" and run["model"] == "gemini-2.5-pro"
    assert run["tokens_in"] == 120 and run["tokens_out"] == 45
    assert run["cost_usd"] == pytest.approx((120 * 1.25 + 45 * 10.0) / 1_000_000, rel=1e-6)
    story = client.get("/api/v1/projects/DEMO/stories/DEMO-3").json()
    assert story["status"] == "borrador"
    assert story["source_ref"] == "proceso.bpmn · Actividad: Validar tarjeta"
    events = client.get("/api/v1/projects/DEMO/stories/DEMO-3/events").json()
    assert events[0]["actor_name"] == "agent:adk"


class ScriptedGemini(BaseLlm):
    """Imita a Gemini con herramientas: el generador consulta a agente_documentos (AgentTool),
    que lista y lee la carpeta; luego el generador responde con set_model_response.

    Cada llamada reporta 10/5 tokens, para comprobar que se suman también las del agente anidado.
    """

    model: str = "fake-gemini-pro"
    calls: list = []

    @staticmethod
    def _responses(llm_request) -> list[dict]:
        return [
            part.function_response.response
            for content in llm_request.contents
            for part in (content.parts or [])
            if part.function_response
        ]

    @staticmethod
    def _call(name: str, args: dict) -> LlmResponse:
        return LlmResponse(content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(name=name, args=args))
        ]))

    async def generate_content_async(self, llm_request, stream=False):
        tools = set(llm_request.tools_dict)
        done = self._responses(llm_request)
        if "agente_documentos" in tools:
            self.calls.append(("generador", len(done)))
            if not done:
                response = self._call("agente_documentos", {"request": "¿Qué documentos hay y qué dicen?"})
            else:
                assert "Validar tarjeta" in str(done[0])  # el generador recibe lo que leyó el agente
                response = self._call("set_model_response", {"stories": [
                    {**GENERATION_JSON["stories"][0], "source_document": "reunion.md"}
                ]})
        else:
            assert {"listar_documentos", "leer_documento"} <= tools
            self.calls.append(("documentos", len(done)))
            if not done:
                response = self._call("listar_documentos", {})
            elif len(done) == 1:
                assert done[0]["documentos"][0]["nombre"] == "reunion.md"
                response = self._call("leer_documento", {"nombre": "reunion.md"})
            else:
                response = LlmResponse(content=types.Content(role="model", parts=[
                    types.Part(text=f"reunion.md dice: {done[1]['texto']}")
                ]))
        response.usage_metadata = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5,
        )
        response.turn_complete = True
        yield response


def test_generate_from_documents_uses_document_agent_as_agent_tool(tmp_path):
    from app.agents.document_tools import make_document_tools
    from app.agents.ports import DocumentGenerationInput
    from app.services.documents import FolderSource

    (tmp_path / "PAGO").mkdir()
    (tmp_path / "PAGO" / "reunion.md").write_text("# Pagos\n- Validar tarjeta del cliente\n", encoding="utf-8")
    tools = make_document_tools(FolderSource(tmp_path, "PAGO"), max_total_chars=10_000)
    fake = ScriptedGemini(calls=[])
    provider = AdkProvider(model="gemini-2.5-pro", api_key="clave-falsa-de-prueba", llm=fake)

    output, usage = provider.generate_from_documents(
        DocumentGenerationInput(project_key="PAGO", project_name="Pagos"), tools
    )

    assert [s.title for s in output.stories] == ["Pagar con tarjeta"]
    assert output.stories[0].source_document == "reunion.md"
    assert tools.documents_read == ["reunion.md"]
    assert fake.calls == [("generador", 0), ("documentos", 0), ("documentos", 1), ("documentos", 2), ("generador", 1)]
    # 5 llamadas al modelo: 2 del generador + 3 del agente anidado.
    assert (usage.tokens_in, usage.tokens_out) == (50, 25)


class FpaGemini(BaseLlm):
    """Imita a Gemini en el pipeline FPA: responde según el paso (concepto o clasificación) de la instrucción."""

    model: str = "fake-gemini-pro"
    seen: list = []

    async def generate_content_async(self, llm_request, stream=False):
        instruction = str(llm_request.config.system_instruction)
        if "Paso 1 de 2" in instruction:
            self.seen.append(("concept", "Registro de entidades ya contadas en el proyecto (ILF/EIF): Cliente" in str(llm_request.contents)))
            payload = {"concepts": [
                {"name": "Pago", "kind": "entidad", "evidence": "pagar con tarjeta"},
                {"name": "Cliente", "kind": "entidad", "evidence": "como cliente"},
                {"name": "Registrar pago", "kind": "transaccion", "evidence": "- [ ] Registra el pago"},
            ]}
        else:
            # El paso 2 recibe en su instrucción los conceptos que el paso 1 dejó en el estado.
            self.seen.append(("act", "Registrar pago" in instruction))
            payload = {"functions": [
                {"name": "Pago", "type": "ILF", "det": 12, "ret": 2},
                {"name": "Cliente", "type": "ILF", "det": 8, "ret": 1, "reused": True},
                {"name": "Registrar pago", "type": "EI", "det": 8, "ftr": 2, "rationale": "Mantiene Pago y lee Cliente"},
            ], "rationale": "Cliente ya existe en el registro."}
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=json.dumps(payload))]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=5),
            turn_complete=True,
        )


def test_size_story_runs_concept_then_act_agents_in_sequence():
    from app.agents.ports import SizingInput
    from app.services import fpa

    fake = FpaGemini(seen=[])
    provider = AdkProvider(model="gemini-2.5-pro", api_key="clave-falsa-de-prueba", llm=fake)
    story = {"code": "PAGO-1", "title": "Pagar con tarjeta", "role": "cliente", "need": "pagar con tarjeta",
             "acceptance_criteria": "- [ ] Registra el pago", "type": "HU_TRADICIONAL"}
    output, usage = provider.size_story(SizingInput(story=story, registry=["Cliente"]))

    assert fake.seen == [("concept", True), ("act", True)]
    assert [c.name for c in output.concepts] == ["Pago", "Cliente", "Registrar pago"]
    assert [f.type for f in output.functions] == ["ILF", "ILF", "EI"]
    assert output.rationale == "Cliente ya existe en el registro."
    assert (usage.tokens_in, usage.tokens_out) == (20, 10)  # dos agentes, dos llamadas
    assert fpa.analyze(output.components())["ufp"] == 7 + 0 + 4


def test_fpa_schemas_sent_to_gemini_have_no_length_constraints():
    import json as _json

    from app.agents.providers.adk import _LlmConcepts, _LlmFunctions

    for model in (_LlmConcepts, _LlmFunctions):
        text = _json.dumps(model.model_json_schema())
        for forbidden in ("minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "pattern"):
            assert forbidden not in text, f"{model.__name__} expone {forbidden}"


def test_invalid_fpa_output_is_rejected_by_strict_dtos():
    from pydantic import ValidationError

    from app.agents.ports import SizingInput

    class ZeroDet(FpaGemini):
        async def generate_content_async(self, llm_request, stream=False):
            text = '{"concepts": []}' if "Paso 1 de 2" in str(llm_request.config.system_instruction) else \
                '{"functions": [{"name": "X", "type": "EI", "det": 0, "ftr": 1}]}'
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), turn_complete=True)

    provider = AdkProvider(model="gemini-2.5-pro", api_key="clave-falsa-de-prueba", llm=ZeroDet(seen=[]))
    with pytest.raises(ValidationError):
        provider.size_story(SizingInput(story={"code": "X-1", "title": "Algo"}))


CONTEXT_GRAPH = {
    "version": 4,
    "nodes": [
        {"id": "cliente", "kind": "entity", "name": "Cliente", "status": "existente"},
        {"id": "privacidad-datos", "kind": "constraint", "name": "Datos locales", "category": "privacidad",
         "severity": "critica", "rule": "Los datos de clientes no salen de servidores propios", "keywords": []},
    ],
    "edges": [{"source": "privacidad-datos", "target": "cliente", "type": "applies_to"}],
}


class ContextAwareGemini(FpaGemini):
    """Tres pasos: arquitecto (contexto) → conceptos → clasificación."""

    async def generate_content_async(self, llm_request, stream=False):
        instruction = str(llm_request.config.system_instruction)
        if "Actúas como el arquitecto" in instruction:
            self.seen.append(("architect", "<grafo_de_contexto version=\"4\">" in str(llm_request.contents)))
            payload = {"impacted_features": [], "existing_entities": ["cliente"],
                       "triggered_constraints": [{"constraint_id": "privacidad-datos", "reason": "Usa datos de clientes"}],
                       "violations": [{"constraint_id": "privacidad-datos", "reason": "Los envía a OpenAI"}],
                       "summary": "Viola la restricción de privacidad."}
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=json.dumps(payload))]),
                usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=5),
                turn_complete=True,
            )
            return
        if "Paso 2 de 2" in instruction:
            # El analista de FPA recibe el informe del arquitecto en su instrucción.
            self.seen.append(("report-in-act", "Viola la restricción de privacidad." in instruction))
        async for response in super().generate_content_async(llm_request, stream):
            yield response


def test_size_story_with_context_runs_architect_first():
    from app.agents.ports import SizingInput

    fake = ContextAwareGemini(seen=[])
    provider = AdkProvider(model="gemini-2.5-pro", api_key="clave-falsa-de-prueba", llm=fake)
    story = {"code": "PAGO-1", "title": "Pagar con tarjeta", "acceptance_criteria": "- [ ] Registra el pago"}
    output, usage = provider.size_story(SizingInput(story=story, registry=["Cliente"], context_graph=CONTEXT_GRAPH))

    assert fake.seen[0] == ("architect", True)
    assert ("report-in-act", True) in fake.seen
    assert output.context.violations[0].constraint_id == "privacidad-datos"
    assert output.context.existing_entities == ["cliente"]
    assert (usage.tokens_in, usage.tokens_out) == (30, 15)  # tres agentes


class ExtractorGemini(BaseLlm):
    """Imita al arquitecto que lee los documentos con herramientas y entrega el grafo con set_model_response."""

    model: str = "fake-gemini-pro"

    async def generate_content_async(self, llm_request, stream=False):
        done = [p.function_response.response for c in llm_request.contents for p in (c.parts or []) if p.function_response]
        if not done:
            call = types.FunctionCall(name="listar_documentos", args={})
        elif len(done) == 1:
            call = types.FunctionCall(name="leer_documento", args={"nombre": done[0]["documentos"][0]["nombre"]})
        else:
            assert "nunca salen" in done[1]["texto"]
            call = types.FunctionCall(name="set_model_response", args={
                "nodes": [
                    {"id": "pagos", "kind": "feature", "name": "Pagos", "status": "planificado"},
                    {"id": "datos-locales", "kind": "constraint", "name": "Datos locales", "category": "privacidad",
                     "severity": "critica", "rule": "Los datos nunca salen de servidores propios",
                     "keywords": ["api externa"], "source": "reunion.md · Restricciones"},
                ],
                "edges": [{"source": "datos-locales", "target": "pagos", "type": "applies_to"}],
                "notes": "Extraído de la reunión.",
            })
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=call)]), turn_complete=True)


def test_extract_context_reads_documents_with_tools(tmp_path):
    from app.agents.document_tools import make_document_tools
    from app.agents.ports import ContextExtractionInput
    from app.services import context_graph
    from app.services.documents import FolderSource

    (tmp_path / "PAGO").mkdir()
    (tmp_path / "PAGO" / "reunion.md").write_text("# Restricciones\n- Los datos nunca salen de servidores propios\n")
    tools = make_document_tools(FolderSource(tmp_path, "PAGO"), max_total_chars=10_000)
    provider = AdkProvider(model="gemini-2.5-pro", api_key="clave-falsa-de-prueba", llm=ExtractorGemini())

    output, _ = provider.extract_context(ContextExtractionInput(project_key="PAGO", project_name="Pagos"), tools)
    nodes, edges = output.as_graph()
    graph = context_graph.validate(nodes, edges)
    assert context_graph.summary(graph) == {"feature": 1, "entity": 0, "constraint": 1, "edges": 1}
    assert tools.documents_read == ["reunion.md"] and output.notes == "Extraído de la reunión."


def test_context_schemas_sent_to_gemini_have_no_length_constraints():
    import json as _json

    from app.agents.providers.adk import _LlmContextReport, _LlmGraph

    for model in (_LlmGraph, _LlmContextReport):
        text = _json.dumps(model.model_json_schema())
        for forbidden in ("minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "pattern"):
            assert forbidden not in text, f"{model.__name__} expone {forbidden}"


# ---------- revisión multiagente HITL: analista → (arquitectura ∥ QA ∥ estimación) ----------

REVIEW_PAYLOADS = {
    "Eres el analista de requisitos": {
        "summary": "Falta el umbral.", "invest": {"independent": 4, "negotiable": 4, "valuable": 5, "estimable": 3,
                                                  "small": 4, "testable": 2},
        "questions": [{"question": "¿Cuál es el umbral de nubosidad?", "blocking": True}],
        "improved_criteria": ["Descarta escenas con nubosidad mayor al umbral configurado"], "confidence": 0.7,
    },
    "Eres el arquitecto": {"summary": "Nuevo servicio.", "impacts": ["backend: job de filtrado"],
                           "critical_changes": ["SIGNIFICANT_COST_INCREASE"], "status": "APPROVED_WITH_OBSERVATIONS",
                           "requires_hitl": True, "hitl_reason": "Costo de procesamiento", "confidence": 0.8},
    "Eres el ingeniero de calidad": {"summary": "1 caso.", "test_cases": [
        {"title": "Filtra nubes", "type": "POSITIVE", "given": "una escena", "when": "se procesa", "then": "se descarta"}],
        "untestable_criteria": ["«Pocas nubes» no tiene umbral"], "status": "NEEDS_CHANGES", "confidence": 0.8},
    "Eres el analista que puntúa": {"story_type": "HU_IA", "type_justification": "Ajusta un clasificador",
                                    "datos": 4, "modelo": 3, "evaluacion": 3, "riesgo": 3, "integracion": 2,
                                    "rationale": "Datos etiquetados por conseguir", "confidence": 0.75},
}


class ReviewGemini(BaseLlm):
    """Responde según el agente (por su instrucción) y registra si el paso paralelo vio al analista."""

    model: str = "fake-gemini-pro"
    seen: list = []

    async def generate_content_async(self, llm_request, stream=False):
        instruction = str(llm_request.config.system_instruction)
        key = next(k for k in REVIEW_PAYLOADS if k in instruction)
        self.seen.append((key, "Falta el umbral." in str(llm_request.contents)))
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=json.dumps(REVIEW_PAYLOADS[key]))]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=5),
            turn_complete=True,
        )


def test_review_story_through_adk_pipeline():
    from app.agents.ports import ReviewInput
    from app.services import hitl

    fake = ReviewGemini(seen=[])
    provider = AdkProvider(model="gemini-2.5-flash", api_key="clave-falsa-de-prueba", llm=fake)
    story = {"code": "SIN-1", "title": "Filtrar nubes", "role": "analista", "need": "descartar escenas nubladas",
             "acceptance_criteria": "- [ ] Pocas nubes"}
    out, usage = provider.review_story(ReviewInput(project_key="SIN", project_name="Sinchi", story=story,
                                                   human_notes=["El umbral lo define el equipo SIG"]))
    assert fake.seen[0] == ("Eres el analista de requisitos", False)
    # los tres del paso paralelo ven la respuesta del analista en la conversación
    assert sorted(k for k, saw in fake.seen[1:] if saw) == sorted(list(REVIEW_PAYLOADS)[1:])
    assert (usage.tokens_in, usage.tokens_out) == (40, 20) and not out.errors

    result = hitl.evaluate(out, story)
    codes = {r["code"] for r in result["reasons"]}
    assert result["level"] == 2 and {"COST_INCREASE", "CRITICAL_AMBIGUITY", "UNVERIFIABLE_CRITERIA", "SPIKE_REQUIRED"} <= codes
    assert result["estimation"]["method"] == "ia_factores_v1" and result["story_type"] == "HU_IA"


def test_review_invalid_agent_output_is_missing_not_crash():
    from app.agents.providers.adk import parse_review_state

    out = parse_review_state({"analysis": REVIEW_PAYLOADS["Eres el analista de requisitos"],
                              "architecture": json.dumps({"summary": "x", "status": "MAYBE", "confidence": 2}),
                              "qa": None})
    assert out.analysis is not None and out.architecture is None and out.qa is None
    assert set(out.errors) == {"architecture", "qa", "estimation"}
