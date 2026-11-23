"""API del HITL: bandeja de revisión, revisión multiagente y decisiones humanas (ver services/hitl.py)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.runner import request_changes, run_out, start_pending_reviews, start_review
from app.db import get_db
from app.deps import get_principal, require_user
from app.models import StoryReview, User
from app.services import hitl
from app.services import stories as story_service
from app.services.access import Principal, load_project, require_member

router = APIRouter(prefix="/api/v1/projects/{key}", tags=["hitl"])


class DecisionIn(BaseModel):
    decision: Literal["aprobar", "pedir_cambios", "descartar", "falta_info", "comentario"]
    comment: str | None = Field(None, max_length=4000)
    apply_proposal: bool = Field(False, description="Solo al aprobar: aplica la versión mejorada que propuso el analista")
    expected_version: int | None = Field(None, description="Versión de la HU que se revisó (evita decidir sobre cambios ajenos)")


def review_out(review: StoryReview | None) -> dict | None:
    if review is None:
        return None
    return {
        "id": review.id, "status": review.status, "story_version": review.story_version,
        "applied_version": review.applied_version, "level": review.level,
        "level_label": hitl.LEVEL_LABELS.get(review.level) if review.level is not None else None,
        "confidence": review.confidence, "quality": review.quality, "story_type": review.story_type,
        "reasons": review.reasons, "questions": review.questions, "proposal": review.proposal,
        "estimation": review.estimation, "agents": review.agents, "error": review.error,
        "agent_run_id": review.agent_run_id,
        "decisions": [{
            "id": d.id, "decision": d.decision, "label": hitl.DECISIONS[d.decision], "comment": d.comment,
            "applied_proposal": d.applied_proposal, "level": d.level, "role": d.role,
            "by": d.decided_by.name if d.decided_by else None, "at": d.created_at.isoformat(),
        } for d in review.decisions],
    }


def state_out(story) -> dict:
    st = hitl.state(story)
    return {"code": story.code, "title": story.title, "status": story.status, "version": story.version,
            "hitl_state": st["key"], "hitl_label": st["label"], "level": st["level"]}


@router.get("/hitl")
def inbox(key: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    role = require_member(db, project, principal)
    box = hitl.inbox(project)
    return {
        "role": role, "total": box["total"],
        **{group: [state_out(r["story"]) for r in box[group]]
           for group in ("decidir", "agentes", "en_curso", "aprobadas", "descartadas")},
        "stage1_blockers": hitl.stage1_blockers(project),
    }


@router.get("/stories/{code}/hitl")
def story_hitl(key: str, code: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    role = require_member(db, project, principal)
    story = story_service.get_story(db, project, code)
    st = hitl.state(story)
    return {**state_out(story), "review": review_out(st["review"]),
            "can_decide": hitl.can_decide(role, st["level"], "aprobar")}


@router.post("/stories/{code}/review", status_code=202)
def review_story(key: str, code: str, background: BackgroundTasks,
                 principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Revisión multiagente de una HU. La pueden lanzar personas con rol de escritura o un servicio."""
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    story = story_service.get_story(db, project, code)
    run = start_review(db, project, [story], user_id=principal.user.id if principal.user else None, background=background)
    return run_out(run)


@router.post("/hitl/review-pending", status_code=202)
def review_pending(key: str, background: BackgroundTasks,
                   principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Revisa todas las HU activas sin revisión vigente (nuevas, cambiadas o con una revisión fallida)."""
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    run = start_pending_reviews(db, project, user_id=principal.user.id if principal.user else None, background=background)
    return run_out(run)


@router.post("/stories/{code}/hitl/decision")
def decide(key: str, code: str, body: DecisionIn, background: BackgroundTasks,
           user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Decisión humana. Un servicio (API key) nunca decide: require_user lo rechaza con 403."""
    project = load_project(db, key)
    role = require_member(db, project, Principal(user=user))
    story = story_service.get_story(db, project, code)
    run = None
    if body.decision == "pedir_cambios":
        run = request_changes(db, project, story, user=user, role=role, comment=body.comment or "",
                              expected_version=body.expected_version, background=background)
    else:
        hitl.decide(db, project, story, user=user, role=role, decision=body.decision, comment=body.comment,
                    apply_proposal=body.apply_proposal, expected_version=body.expected_version)
        db.commit()
    db.refresh(story)
    return {**state_out(story), "refinement_run": run_out(run) if run else None}
