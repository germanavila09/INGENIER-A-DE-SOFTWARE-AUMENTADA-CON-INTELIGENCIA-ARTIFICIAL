"""ORCHESTRATOR_AGENT: único agente conversacional; controla el flujo mediante herramientas.

No tiene sub_agents con transferencia libre: los especialistas corren dentro de flujos
deterministas (SequentialAgent / ParallelAgent) que el motor ejecuta, así ningún agente
puede tomar el control ni saltarse pasos.

Habla con el agente del bucket a través de AgentTool (`agente_documentos`): le pregunta
qué documentos hay, dónde está algo o qué dice un documento, y recibe la respuesta con
citas. El agente del bucket no cambia el estado del flujo.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from adk_ing.agente import build_bucket_agent

from ...config import get_hu_settings
from ...tools.hitl_tools import guardia_orquestador, listar_decisiones_pendientes, registrar_decision_humana
from .prompt import PROMPT
from .tools import analizar_proyecto, descubrir_proyectos, estado_proyecto, explicar_historia


def build_orchestrator(model=None, bucket_agent_model=None) -> LlmAgent:
    agente_documentos = build_bucket_agent(
        name="agente_documentos",
        model=bucket_agent_model or model or get_hu_settings().model,
        description=("Agente del bucket: busca en el contenido de todos los documentos de gs://adk_ing, los lista "
                     "y los lee (PDF, Word, Excel, PowerPoint, texto), y responde citando documento y página. "
                     "Úsalo para saber qué documentos hay o qué dice un documento."),
    )
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
            AgentTool(agent=agente_documentos),
        ],
        before_tool_callback=guardia_orquestador,
        generate_content_config=types.GenerateContentConfig(temperature=0.1),
    )


root_agent = build_orchestrator()
