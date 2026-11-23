import logging

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Project, RefinementRequest, utcnow
from app.schemas import RefinementResult
from app.services import audit, stories
from app.services.errors import Conflict, NotFound
from app.services.workflow import Actor

log = logging.getLogger(__name__)
REFINABLE_FIELDS = ("title", "role", "need", "benefit", "acceptance_criteria")


def get_request(db: Session, request_id: int) -> RefinementRequest:
    req = db.get(RefinementRequest, request_id)
    if req is None:
        raise NotFound(f"Solicitud de refinamiento {request_id} no existe")
    return req


def list_pending(db: Session, status: str = "pending", project_key: str | None = None) -> list[RefinementRequest]:
    stmt = select(RefinementRequest).where(RefinementRequest.status == status)
    if project_key:
        stmt = stmt.join(Project).where(Project.key == project_key)
    return list(db.scalars(stmt.order_by(RefinementRequest.id)))


def claim(db: Session, request_id: int, claimer: str) -> RefinementRequest:
    # Update condicional: evita que dos agentes tomen la misma solicitud.
    result = db.execute(
        update(RefinementRequest)
        .where(RefinementRequest.id == request_id, RefinementRequest.status == "pending")
        .values(status="in_progress", claimed_by=claimer, claimed_at=utcnow())
    )
    if result.rowcount == 0:
        db.rollback()
        raise Conflict("La solicitud ya no está pendiente")
    db.commit()
    return get_request(db, request_id)


def submit_result(db: Session, request_id: int, payload: RefinementResult, claimer: str) -> RefinementRequest:
    req = get_request(db, request_id)
    if req.status != "in_progress":
        raise Conflict(f"La solicitud está en estado '{req.status}'; debe estar 'in_progress'")

    story = req.story
    if story.status != "en_refinamiento":
        raise Conflict(f"La HU ya no está en refinamiento (estado actual: {story.status})")
    if payload.expected_version != story.version:
        raise Conflict(
            f"Versión obsoleta: esperada {payload.expected_version}, actual {story.version}"
        )

    changes = {}
    for field in REFINABLE_FIELDS:
        value = getattr(payload, field)
        if value is not None and value != getattr(story, field):
            changes[field] = value
            setattr(story, field, value)

    actor = Actor(kind="service", name=claimer)
    stories.transition(db, story, "refinada", actor, cancel_refinements=False)
    if changes:
        audit.record(db, story, actor, "refined", diff=changes)
    if payload.notes:
        audit.record(db, story, actor, "refinement_notes", diff={"notes": payload.notes})

    req.status = "completed"
    req.result = payload.model_dump()
    req.completed_at = utcnow()
    db.commit()
    return get_request(db, request_id)


def notify_webhook(payload: dict) -> None:
    if not settings.adk_webhook_url:
        return
    try:
        httpx.post(settings.adk_webhook_url, json=payload, timeout=5)
    except httpx.HTTPError as exc:
        log.warning("Webhook ADK falló: %s", exc)


def request_payload(req: RefinementRequest) -> dict:
    return {
        "id": req.id,
        "project_key": req.story.project.key,
        "status": req.status,
        "story": req.input_snapshot,
        "claimed_by": req.claimed_by,
        "created_at": req.created_at.isoformat() if req.created_at else None,
    }
