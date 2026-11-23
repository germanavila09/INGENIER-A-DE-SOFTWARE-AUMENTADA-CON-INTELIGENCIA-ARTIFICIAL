from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_service
from app.models import Project, User
from app.schemas import IngestProject, RefinementResult, refinement_out
from app.security import hash_password
from app.services import projects as project_service
from app.services import refinement as refinement_service
from app.services import stories as story_service
from app.services.access import Principal
from app.services.errors import Unprocessable
from app.services.workflow import Actor

router = APIRouter(prefix="/api/v1", tags=["adk"])


def _actor(principal: Principal) -> Actor:
    return Actor(kind="service", name=principal.service_name, project_role="service")


@router.post("/ingest/projects", status_code=201)
def ingest_project(body: IngestProject, principal: Principal = Depends(require_service), db: Session = Depends(get_db)):
    """Upsert idempotente de proyecto, usuarios, miembros, épicas y HU en una sola llamada."""
    actor = _actor(principal)
    created_users = 0
    for u in body.users:
        _, is_new = project_service.ensure_user(
            db, u.email, u.name, hash_password(u.password) if u.password else None
        )
        created_users += is_new

    project = db.scalar(select(Project).where(Project.key == body.key))
    project_created = project is None
    if project is None:
        project = Project(key=body.key, name=body.name, description=body.description)
        db.add(project)
        db.flush()
    else:
        project.name = body.name
        project.description = body.description

    for m in body.members:
        user = db.scalar(select(User).where(User.email == m.email.lower()))
        if user is None:
            db.rollback()
            raise Unprocessable(f"Miembro {m.email} no existe en users; inclúyelo en 'users'")
        project_service.add_member(db, project, user, m.role)

    created = updated = 0
    for epic in body.epics:
        story_service.get_or_create_epic(db, project, epic.title, epic.description)
        for s in epic.stories:
            data = s.model_dump(exclude={"external_id"}, exclude_unset=True)
            data["epic"] = epic.title
            _, is_new = story_service.upsert_story(db, project, data, actor, s.external_id)
            created += is_new
            updated += not is_new
    for s in body.stories:
        data = s.model_dump(exclude={"external_id"}, exclude_unset=True)
        _, is_new = story_service.upsert_story(db, project, data, actor, s.external_id)
        created += is_new
        updated += not is_new

    db.commit()
    return {
        "project": body.key,
        "project_created": project_created,
        "users_created": created_users,
        "stories_created": created,
        "stories_updated": updated,
    }


@router.get("/refinements")
def list_refinements(
    status: str = Query("pending", pattern="^(pending|in_progress|completed|cancelled)$"),
    project_key: str | None = Query(None),
    principal: Principal = Depends(require_service),
    db: Session = Depends(get_db),
):
    return [refinement_out(r) for r in refinement_service.list_pending(db, status, project_key)]


@router.get("/refinements/{request_id}")
def get_refinement(request_id: int, principal: Principal = Depends(require_service), db: Session = Depends(get_db)):
    return refinement_out(refinement_service.get_request(db, request_id))


@router.post("/refinements/{request_id}/claim")
def claim_refinement(request_id: int, principal: Principal = Depends(require_service), db: Session = Depends(get_db)):
    req = refinement_service.claim(db, request_id, principal.service_name)
    return refinement_out(req)


@router.post("/refinements/{request_id}/result")
def submit_refinement(
    request_id: int,
    body: RefinementResult,
    principal: Principal = Depends(require_service),
    db: Session = Depends(get_db),
):
    req = refinement_service.submit_result(db, request_id, body, principal.service_name)
    return refinement_out(req)
