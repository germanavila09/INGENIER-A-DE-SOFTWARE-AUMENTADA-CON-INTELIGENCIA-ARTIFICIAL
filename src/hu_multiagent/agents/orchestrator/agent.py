"""ORCHESTRATOR_AGENT: único agente conversacional; controla el flujo mediante herramientas.

No tiene sub_agents con transferencia libre: los especialistas corren dentro de flujos
deterministas (SequentialAgent / ParallelAgent) que el motor ejecuta, así ningún agente
puede tomar el control ni saltarse pasos.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.genai import types

from ...config import get_hu_settings
from ...tools.hitl_tools import guardia_orquestador, listar_decisiones_pendientes, registrar_decision_humana
from .prompt import PROMPT
from .tools import analizar_proyecto, descubrir_proyectos, estado_proyecto, explicar_historia


def build_orchestrator(model=None) -> LlmAgent:
    return LlmAgent(
        name="orchestrator_agent",
        description="Coordina el análisis multiagente de historias de usuario por proyecto, con HITL y trazabilidad.",
        model=model or get_hu_settings().model,
        instruction=PROMPT,
        tools=[
            descubrir_proyectos,
            analizar_proyecto,
            estado_proyecto,
            listar_decisiones_pendientes,
            registrar_decision_humana,
            explicar_historia,
        ],
        before_tool_callback=guardia_orquestador,
        generate_content_config=types.GenerateContentConfig(temperature=0.1),
    )


root_agent = build_orchestrator()
