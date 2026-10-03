"""Flujo completo dentro de ADK con un modelo simulado: el agente llama a la herramienta
de búsqueda, recibe los fragmentos de docs_ejemplo/ y responde citando la fuente."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from adk_ing.bucket import CarpetaLocal
from adk_ing.indice import IndiceDocumentos

EJEMPLOS = Path(__file__).resolve().parents[1] / "docs_ejemplo"
PETICIONES: list = []


class ModeloSimulado(BaseLlm):
    """1.ª llamada: pide buscar. 2.ª llamada: responde con el primer resultado."""

    model: str = "simulado"

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        PETICIONES.append(llm_request)
        respuestas = [
            p.function_response
            for c in llm_request.contents
            for p in (c.parts or [])
            if p.function_response
        ]
        if not respuestas:
            parte = types.Part.from_function_call(
                name="buscar_en_documentos", args={"consulta": "cobertura mínima de pruebas"}
            )
        else:
            r = respuestas[-1].response["resultados"][0]
            parte = types.Part.from_text(text=f"El mínimo es 80 % ({r['documento']}, {r['ubicacion']}).")
        yield LlmResponse(content=types.Content(role="model", parts=[parte]))


def test_agente_busca_y_cita(monkeypatch, tmp_path):
    from agente_bucket import agent as mod

    shutil.copytree(EJEMPLOS, tmp_path / "docs")
    monkeypatch.setattr(mod, "_indice", IndiceDocumentos(CarpetaLocal(tmp_path / "docs"), ttl_segundos=0))
    agente = mod.root_agent.clone(update={"model": ModeloSimulado()})

    runner = InMemoryRunner(agent=agente, app_name="prueba")
    sesion = runner.session_service.create_session_sync(app_name="prueba", user_id="u")
    eventos = list(
        runner.run(
            user_id="u",
            session_id=sesion.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="¿Qué cobertura se exige?")]),
        )
    )

    llamadas = [p.function_call.name for e in eventos for p in (e.content.parts if e.content else []) if p.function_call]
    assert llamadas == ["buscar_en_documentos"]
    final = eventos[-1].content.parts[0].text
    assert final == "El mínimo es 80 % (politica_calidad_software.pdf, página 1)."

    # Las cinco herramientas se declaran al modelo.
    decl = {f.name for t in PETICIONES[0].config.tools for f in t.function_declarations}
    assert decl == {
        "buscar_en_documentos", "leer_documento", "listar_documentos",
        "estado_del_indice", "consultar_documento_con_gemini",
    }
