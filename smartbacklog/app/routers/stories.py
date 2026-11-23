from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_principal
from app.schemas import (
    AssignIn,
    BulkStoriesIn,
    StatusIn,
    StoryIn,
    StoryPatch,
    event_out,
    story_out,
)
from app.services import stories as service
from app.services.access import Principal, actor_for, load_project, require_member
from app.services.errors import Conflict, Unprocessable
from app.agents.runner import auto_refine_if_enabled
from app.services.refinement import notify_webhook, request_payload
from app.services.workflow import STATUS_LABELS

router = APIRouter(prefix="/api/v1/projects/{key}/stories", tags=["stories"])


def _check_version(story, expected: int | None) -> None:
    if expected is not None and expected != story.version:
        raise Conflict(f"Versión obsoleta: esperada {expected}, actual {story.version}")


@router.get("")
def list_stories(
    key: str,
    status: str | None = Query(None),
    type: str | None = Query(None),
    epic_id: int | None = Query(None),
    assignee_id: int | None = Query(None),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    project = load_project(db, key)
    require_member(db, project, principal)
    rows = service.list_stories(db, project, status, type, epic_id, assignee_id)
    return [story_out(s) for s in rows]


@router.post("", status_code=201)
def create_story(key: str, body: StoryIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    role = require_member(db, project, principal, write=True)
    actor = actor_for(principal, role)
    story, _ = service.upsert_story(db, project, body.model_dump(exclude={"external_id"}, exclude_unset=True), actor, body.external_id)
    db.commit()
    return story_out(story)


@router.post(":bulk", status_code=201)
def bulk_stories(key: str, body: BulkStoriesIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    role = require_member(db, project, principal, write=True)
    actor = actor_for(principal, role)
    created = updated = 0
    for item in body.stories:
        _, is_new = service.upsert_story(
            db, project, item.model_dump(exclude={"external_id"}, exclude_unset=True), actor, item.external_id
        )
        created += is_new
        updated += not is_new
    db.commit()
    return {"created": created, "updated": updated}


@router.get("/{code}")
def get_story(key: str, code: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal)
    return story_out(service.get_story(db, project, code))


@router.patch("/{code}")
def patch_story(key: str, code: str, body: StoryPatch, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    role = require_member(db, project, principal, write=True)
    story = service.get_story(db, project, code)
    _check_version(story, body.expected_version)
    fields = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    if "title" in fields and fields["title"] is None:
        raise Unprocessable("El título no puede quedar vacío")
    actor = actor_for(principal, role)
    service.apply_changes(db, project, story, fields, actor)
    db.commit()
    return story_out(story)


@router.post("/{code}/status")
def change_status(
    key: str,
    code: str,
    body: StatusIn,
    background: BackgroundTasks,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    if body.status not in STATUS_LABELS:
        raise Unprocessable(f"Estado desconocido: {body.status}")
    project = load_project(db, key)
    # Membresía basta: check_transition decide qué rol puede mover a cada estado.
    role = require_member(db, project, principal)
    story = service.get_story(db, project, code)
    _check_version(story, body.expected_version)
    actor = actor_for(principal, role)
    req = service.transition(db, story, body.status, actor, user_id=principal.user.id if principal.user else None)
    db.commit()
    if req is not None:
        background.add_task(notify_webhook, request_payload(req))
        auto_refine_if_enabled(db, project, req, principal.user.id if principal.user else None, background)
    return story_out(story)


@router.post("/{code}/assign")
def assign_story(key: str, code: str, body: AssignIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Asigna por assignee_email (ADK) o user_id (humanos). Ambos deben ser miembros del proyecto."""
    project = load_project(db, key)
    role = require_member(db, project, principal, write=True)
    story = service.get_story(db, project, code)
    if body.assignee_email is not None:
        data = {"assignee_email": body.assignee_email or None}
    else:
        if body.user_id is not None and not any(m.user_id == body.user_id for m in project.members):
            raise Unprocessable("Sólo se puede asignar a miembros del proyecto")
        data = {"assignee_id": body.user_id}
    service.apply_changes(db, project, story, data, actor_for(principal, role))
    db.commit()
    return story_out(story)


@router.get("/{code}/events")
def story_events(key: str, code: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal)
    story = service.get_story(db, project, code)
    return [event_out(e) for e in story.events]
