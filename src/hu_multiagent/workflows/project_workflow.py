"""Flujo por proyecto (determinista, agentes ADK nativos):

    project_workflow (SequentialAgent)
    ├── project_discovery_agent     → ProjectManifest
    └── document_ingestion_agent    → documentos normalizados, historias, ProjectContext

Fase 2: project_context_agent (LLM) después de la ingesta para resumir el contexto
conservando las fuentes.
"""

from __future__ import annotations

from google.adk.agents import SequentialAgent

from ..agents.ingestion.agent import build_document_ingestion
from ..agents.project_discovery.agent import build_project_discovery


def build_project_workflow(engine) -> SequentialAgent:
    return SequentialAgent(
        name="project_workflow",
        description="Descubre, normaliza y contextualiza los documentos de un proyecto.",
        sub_agents=[build_project_discovery(engine), build_document_ingestion(engine)],
    )
