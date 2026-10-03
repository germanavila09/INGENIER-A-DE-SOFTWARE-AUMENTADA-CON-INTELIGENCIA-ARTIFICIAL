"""El orquestador dentro de ADK (Runner real, modelos simulados): delega con herramientas
y la guardia HITL bloquea una aprobación que el usuario no escribió."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import AsyncGenerator

import hu_fakes
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from hu_multiagent.agents.orchestrator.agent import build_orchestrator
from hu_multiagent.config import HuSettings
from hu_multiagent.workflows.engine import HuEngine, set_engine

EJEMPLOS = Path(__file__).resolve().parents[1] / "ejemplos" / "projects"
RESPUESTAS: list[dict] = []


class OrquestadorSimulado(BaseLlm):
    """Traduce el mensaje del usuario a una llamada de herramienta, como lo haría Gemini."""

    model: str = "orquestador-simulado"

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        ultimo = llm_request.contents[-1]
        fr = [p.function_response for p in ultimo.parts if p.function_response]
        if fr:
            RESPUESTAS.append(fr[0].response)
            parte = types.Part.from_text(text=f"Resultado: {fr[0].response.get('status')}")
        else:
            texto = " ".join(p.text or "" for p in ultimo.parts).lower()
            if "analiza" in texto:
                parte = types.Part.from_function_call(name="analizar_proyecto", args={"project_id": "PRJ001"})
            else:  # el modelo intenta aprobar US-003 en cualquier caso
                parte = types.Part.from_function_call(
                    name="registrar_decision_humana",
                    args={"decision_id": "D-PRJ001-US-003-01", "decision": "APPROVED", "comentario": ""})
        yield LlmResponse(content=types.Content(role="model", parts=[parte]))


def test_orquestador_delega_y_respeta_hitl(tmp_path, monkeypatch):
    for k, v in {"GOOGLE_GENAI_USE_VERTEXAI": "TRUE", "GOOGLE_CLOUD_PROJECT": "t", "GOOGLE_CLOUD_LOCATION": "us-central1"}.items():
        monkeypatch.setenv(k, v)
    shutil.copytree(EJEMPLOS, tmp_path / "in")
    eng = HuEngine(HuSettings(input_uri=str(tmp_path / "in"), results_uri=str(tmp_path / "out")),
                   model=hu_fakes.ModeloSimulado())
    eng.audit.emit_logs = False
    set_engine(eng)
    try:
        runner = InMemoryRunner(agent=build_orchestrator(model=OrquestadorSimulado()), app_name="orq")
        ses = runner.session_service.create_session_sync(app_name="orq", user_id="german")

        async def decir(texto):
            msg = types.Content(role="user", parts=[types.Part.from_text(text=texto)])
            async for _ in runner.run_async(user_id="german", session_id=ses.id, new_message=msg):
                pass

        asyncio.run(decir("Analiza el proyecto PRJ001"))
        assert RESPUESTAS[-1]["status"] == "WAITING_FOR_HUMAN"

        asyncio.run(decir("¿y qué falta?"))  # el modelo intenta aprobar sin que el usuario lo diga
        assert RESPUESTAS[-1]["status"] == "bloqueado"
        assert eng.project_status("PRJ001")["decisiones_pendientes"] == [
            "D-PRJ001-US-001-01", "D-PRJ001-US-003-01", "D-PRJ001-US-004-01"]

        asyncio.run(decir("APPROVED D-PRJ001-US-003-01"))
        assert RESPUESTAS[-1]["status"] == "ok"
        decision = eng.state.load("PRJ001").decisions["D-PRJ001-US-003-01"]
        assert decision.resolution.decided_by == "german"
    finally:
        set_engine(None)


class BucketSimulado(BaseLlm):
    """Modelo del agente del bucket: lista documentos y responde con el total."""

    model: str = "bucket-simulado"

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        fr = [p.function_response for c in llm_request.contents for p in (c.parts or []) if p.function_response]
        if fr:
            parte = types.Part.from_text(text=f"Hay {fr[-1].response['total']} documentos en el bucket.")
        else:
            parte = types.Part.from_function_call(name="listar_documentos", args={})
        yield LlmResponse(content=types.Content(role="model", parts=[parte]))


class OrquestadorPreguntaAlBucket(BaseLlm):
    model: str = "orquestador-simulado-2"

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        ultimo = llm_request.contents[-1]
        fr = [p.function_response for p in ultimo.parts if p.function_response]
        if fr:
            RESPUESTAS.append(fr[0].response)
            parte = types.Part.from_text(text="Listo")
        else:
            parte = types.Part.from_function_call(name="agente_documentos",
                                                  args={"request": "¿Qué documentos hay en el bucket?"})
        yield LlmResponse(content=types.Content(role="model", parts=[parte]))


def test_orquestador_habla_con_el_agente_del_bucket(tmp_path, monkeypatch):
    import adk_ing.agente as agente_mod
    from adk_ing.bucket import CarpetaLocal
    from adk_ing.indice import IndiceDocumentos

    for k, v in {"GOOGLE_GENAI_USE_VERTEXAI": "TRUE", "GOOGLE_CLOUD_PROJECT": "t", "GOOGLE_CLOUD_LOCATION": "us-central1"}.items():
        monkeypatch.setenv(k, v)
    docs = Path(__file__).resolve().parents[1] / "docs_ejemplo"
    monkeypatch.setattr(agente_mod, "_indice", IndiceDocumentos(CarpetaLocal(docs), ttl_segundos=0))
    orq = build_orchestrator(model=OrquestadorPreguntaAlBucket(), bucket_agent_model=BucketSimulado())
    runner = InMemoryRunner(agent=orq, app_name="orq2")
    ses = runner.session_service.create_session_sync(app_name="orq2", user_id="u")
    msg = types.Content(role="user", parts=[types.Part.from_text(text="¿qué hay en el storage?")])

    async def go():
        async for _ in runner.run_async(user_id="u", session_id=ses.id, new_message=msg):
            pass

    asyncio.run(go())
    assert "Hay 6 documentos en el bucket." in str(RESPUESTAS[-1])
