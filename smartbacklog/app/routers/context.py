"""Grafo de contexto del proyecto (HITL) y excepciones a restricciones violadas.

El agente propone el grafo (`POST /projects/{key}/agents/extract-context`, en routers/agents.py) como borrador;
una persona lo edita y lo aprueba o rechaza aquí. Solo una persona aprueba y solo una persona exceptúa una
restricción violada: una API key de servicio recibe 403.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_principal
from app.schemas import story_out
from app.services import context as context_service
from app.services import stories as story_service
from app.services.access import Principal, actor_for, load_project, require_member

router = APIRouter(prefix="/api/v1/projects", tags=["contexto"])


class GraphIn(BaseModel):
    nodes: list[dict] = Field(description="Nodos: {id, kind: feature|entity|constraint, name, ...}")
    edges: list[dict] = Field(default_factory=list, description="Aristas: {source, target, type: depends_on|uses|applies_to}")
    notes: str | None = Field(None, max_length=2000, description="Qué se cambió y por qué")


class ReviewIn(BaseModel):
    notes: str | None = Field(None, max_length=2000)


class WaiveIn(BaseModel):
    justification: str = Field(min_length=10, max_length=1000, description="Por qué se acepta el incumplimiento")


def _actor_name(principal: Principal) -> str:
    return principal.user.name if principal.user else f"service:{principal.service_name or 'api'}"


@router.get("/{key}/context")
def get_context(key: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Versión aprobada (la que usa el dimensionamiento), borrador pendiente e historial de versiones."""
    project = load_project(db, key)
    require_member(db, project, principal)
    return context_service.overview(db, project)


@router.get("/{key}/context/versions/{version}")
def get_context_version(
    key: str, version: int, principal: Principal = Depends(get_principal), db: Session = Depends(get_db),
):
    project = load_project(db, key)
    require_member(db, project, principal)
    return context_service.as_dict(context_service.get_version(db, project, version))


@router.put("/{key}/context/draft")
def put_context_draft(
    key: str, body: GraphIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db),
):
    """Reemplaza el borrador (o crea uno a mano). Valida tipos, ids y aristas: 422 si algo no cuadra."""
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    graph = context_service.update_draft(db, project, body.nodes, body.edges, _actor_name(principal), body.notes)
    db.commit()
    return context_service.as_dict(graph)


@router.post("/{key}/context/draft/approve")
def approve_context_draft(
    key: str, body: ReviewIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db),
):
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    graph = context_service.approve(db, project, principal.user, body.notes)
    db.commit()
    return context_service.as_dict(graph)


@router.post("/{key}/context/draft/reject")
def reject_context_draft(
    key: str, body: ReviewIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db),
):
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    graph = context_service.reject(db, project, principal.user, body.notes)
    db.commit()
    return context_service.as_dict(graph)


@router.post("/{key}/stories/{code}/violations/{constraint_id}/waive")
def waive_violation(
    key: str, code: str, constraint_id: str, body: WaiveIn,
    principal: Principal = Depends(get_principal), db: Session = Depends(get_db),
):
    """Excepción justificada a una restricción violada. Desbloquea la etapa 3 para esa violación."""
    project = load_project(db, key)
    role = require_member(db, project, principal, write=True)
    story = story_service.get_story(db, project, code)
    story_service.waive_violation(db, story, constraint_id, actor_for(principal, role), body.justification)
    db.commit()
    return story_out(story)
