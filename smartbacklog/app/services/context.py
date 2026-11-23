"""Ciclo de vida del grafo de contexto de un proyecto (HITL).

Un agente (o una persona) crea un borrador; una persona lo revisa, puede editarlo y lo aprueba o lo
rechaza. El dimensionamiento usa solo la última versión aprobada. Cada versión queda guardada y cada
acción se registra en su `log`.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ContextGraph, Project, User, utcnow
from app.services import context_graph
from app.services.errors import Forbidden, NotFound, Unprocessable


def _validated(nodes, edges) -> dict:
    try:
        return context_graph.validate(nodes, edges)
    except context_graph.GraphError as exc:
        raise Unprocessable(str(exc)) from exc


def _log(graph: ContextGraph, actor: str, action: str, detail: str | None = None) -> None:
    entry = {"at": utcnow().isoformat(), "by": actor, "action": action}
    if detail:
        entry["detail"] = detail[:500]
    graph.log = [*(graph.log or []), entry]


def latest(db: Session, project: Project, status: str) -> ContextGraph | None:
    return db.scalar(
        select(ContextGraph)
        .where(ContextGraph.project_id == project.id, ContextGraph.status == status)
        .order_by(ContextGraph.version.desc())
        .limit(1)
    )


def approved(db: Session, project: Project) -> ContextGraph | None:
    return latest(db, project, "approved")


def draft(db: Session, project: Project) -> ContextGraph | None:
    return latest(db, project, "draft")


def get_version(db: Session, project: Project, version: int) -> ContextGraph:
    graph = db.scalar(select(ContextGraph).where(ContextGraph.project_id == project.id, ContextGraph.version == version))
    if graph is None:
        raise NotFound(f"No existe la versión {version} del grafo de {project.key}")
    return graph


def create_draft(
    db: Session, project: Project, nodes, edges, *, created_by: str,
    sources: list | None = None, notes: str | None = None, agent_run_id: int | None = None,
) -> ContextGraph:
    """Nuevo borrador. Un borrador anterior sin revisar queda reemplazado (superseded)."""
    clean = _validated(nodes, edges)
    previous = draft(db, project)
    if previous is not None:
        previous.status = "superseded"
        _log(previous, created_by, "superseded", "Reemplazado por un borrador nuevo")
    version = (db.scalar(select(func.max(ContextGraph.version)).where(ContextGraph.project_id == project.id)) or 0) + 1
    graph = ContextGraph(
        project_id=project.id, version=version, status="draft", nodes=clean["nodes"], edges=clean["edges"],
        sources=sources or [], notes=(notes or "")[:2000] or None, created_by=created_by, agent_run_id=agent_run_id,
        log=[],
    )
    _log(graph, created_by, "created", notes)
    db.add(graph)
    db.flush()
    return graph


def update_draft(db: Session, project: Project, nodes, edges, actor: str, notes: str | None = None) -> ContextGraph:
    """Edición humana del borrador (o creación, si no hay): reemplaza nodos y aristas."""
    current = draft(db, project)
    if current is None:
        base = approved(db, project)
        return create_draft(db, project, nodes, edges, created_by=actor,
                            sources=base.sources if base else [], notes=notes or "Borrador creado a mano")
    clean = _validated(nodes, edges)
    current.nodes, current.edges = clean["nodes"], clean["edges"]
    _log(current, actor, "edited", notes)
    db.flush()
    return current


def _review(db: Session, project: Project, user: User | None, status: str, notes: str | None) -> ContextGraph:
    if user is None:
        raise Forbidden("La revisión del grafo de contexto la hace una persona, no un servicio")
    current = draft(db, project)
    if current is None:
        raise Unprocessable("No hay un borrador del grafo de contexto por revisar")
    if status == "approved":
        if not current.nodes:
            raise Unprocessable("No se aprueba un grafo vacío")
        previous = approved(db, project)
        if previous is not None:
            previous.status = "superseded"
            _log(previous, user.name, "superseded", f"Reemplazado por la versión {current.version}")
    current.status = status
    current.reviewed_by_id, current.reviewed_at, current.review_notes = user.id, utcnow(), notes
    _log(current, user.name, status, notes)
    db.flush()
    return current


def approve(db: Session, project: Project, user: User | None, notes: str | None) -> ContextGraph:
    return _review(db, project, user, "approved", notes)


def reject(db: Session, project: Project, user: User | None, notes: str | None) -> ContextGraph:
    return _review(db, project, user, "rejected", notes)


def as_dict(graph: ContextGraph | None) -> dict | None:
    if graph is None:
        return None
    return {
        "version": graph.version,
        "status": graph.status,
        "nodes": graph.nodes,
        "edges": graph.edges,
        "summary": context_graph.summary({"nodes": graph.nodes, "edges": graph.edges}),
        "sources": graph.sources,
        "notes": graph.notes,
        "created_by": graph.created_by,
        "agent_run_id": graph.agent_run_id,
        "reviewed_by": graph.reviewed_by.name if graph.reviewed_by else None,
        "reviewed_at": graph.reviewed_at.isoformat() if graph.reviewed_at else None,
        "review_notes": graph.review_notes,
        "log": graph.log,
        "created_at": graph.created_at.isoformat() if graph.created_at else None,
    }


def overview(db: Session, project: Project) -> dict:
    versions = db.scalars(
        select(ContextGraph).where(ContextGraph.project_id == project.id).order_by(ContextGraph.version.desc())
    )
    return {
        "project": project.key,
        "approved": as_dict(approved(db, project)),
        "draft": as_dict(draft(db, project)),
        "versions": [{"version": g.version, "status": g.status, "created_by": g.created_by,
                      "summary": context_graph.summary({"nodes": g.nodes, "edges": g.edges})} for g in versions],
    }
