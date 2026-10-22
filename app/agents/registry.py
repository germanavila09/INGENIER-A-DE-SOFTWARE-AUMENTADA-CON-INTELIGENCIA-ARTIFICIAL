"""Elige el proveedor de IA según AGENT_PROVIDER. Se resuelve en cada llamada (no al importar)."""

from __future__ import annotations

from app.agents.ports import AgentNotConfigured, AgentProvider
from app.config import settings


def get_provider() -> AgentProvider:
    name = settings.agent_provider.strip().lower()
    if name == "none":
        raise AgentNotConfigured("Agentes desactivados: define AGENT_PROVIDER=stub o AGENT_PROVIDER=adk")
    if name == "stub":
        from app.agents.providers.stub import StubProvider

        return StubProvider()
    if name == "adk":
        from app.agents.providers.adk import AdkProvider

        return AdkProvider(
            model=settings.agent_model, api_key=settings.google_api_key,
            vertexai=settings.google_genai_use_vertexai, project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
    raise AgentNotConfigured(f"AGENT_PROVIDER desconocido: {settings.agent_provider}")


def provider_status() -> dict:
    base = {"provider": settings.agent_provider, "model": settings.agent_model, "execution": settings.agent_execution,
            "auto_refine": settings.agent_auto_refine, "docs_source": settings.agent_docs_source}
    try:
        provider = get_provider()
    except AgentNotConfigured as exc:
        return {**base, "configured": False, "reason": str(exc)}
    return {**base, "configured": True, "reason": None, "model": provider.model,
            "backend": getattr(provider, "backend", None)}
