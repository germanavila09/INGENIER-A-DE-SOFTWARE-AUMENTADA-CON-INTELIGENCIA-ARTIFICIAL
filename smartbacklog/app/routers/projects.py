import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_principal
from app.models import Epic, Project, ProjectMember, User
from app.schemas import EpicIn, MemberIn, ProjectIn, epic_out
from app.services import projects as service
from app.services.access import Principal, load_project, require_member
from app.services.errors import Conflict, NotFound, Unprocessable

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

EXPORT_COLUMNS = [
    "code", "epic", "title", "role", "need", "benefit", "acceptance_criteria",
    "type", "status", "function_points", "nfr_points", "priority_rank", "assignee", "source_ref",
]


def project_out(p: Project) -> dict:
    return {"id": p.id, "key": p.key, "name": p.name, "description": p.description, "story_count": len(p.stories)}


@router.get("")
def list_projects(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    projects = list(db.scalars(select(Project).order_by(Project.key)))
    if principal.is_service or principal.user.is_admin:
        return [project_out(p) for p in projects]
    visible = {m.project_id for m in db.scalars(select(ProjectMember).where(ProjectMember.user_id == principal.user.id))}
    return [project_out(p) for p in projects if p.id in visible]


@router.post("", status_code=201)
def create_project(body: ProjectIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if db.scalar(select(Project).where(Project.key == body.key)):
        raise Conflict(f"Ya existe el proyecto {body.key}")
    project = Project(key=body.key, name=body.name, description=body.description)
    if principal.user:
        project.created_by_id = principal.user.id
    db.add(project)
    db.flush()
    if principal.user:
        db.add(ProjectMember(project=project, user=principal.user, role="product_owner"))
    db.commit()
    return project_out(project)


@router.get("/{key}")
def get_project(key: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal)
    return project_out(project)


@router.get("/{key}/members")
def list_members(key: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal)
    return [{"user_id": m.user_id, "name": m.user.name, "email": m.user.email, "role": m.role} for m in project.members]


@router.post("/{key}/members", status_code=201)
def add_member(key: str, body: MemberIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        raise NotFound(f"No existe un usuario con email {body.email}")
    service.add_member(db, project, user, body.role)
    db.commit()
    return {"user_id": user.id, "email": user.email, "role": body.role}


@router.delete("/{key}/members/{user_id}")
def remove_member(key: str, user_id: int, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    member = next((m for m in project.members if m.user_id == user_id), None)
    if member is None:
        raise NotFound("El usuario no es miembro del proyecto")
    db.delete(member)
    db.commit()
    return {"ok": True}


@router.get("/{key}/epics")
def list_epics(key: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal)
    return [epic_out(e) for e in project.epics]


@router.post("/{key}/epics", status_code=201)
def create_epic(key: str, body: EpicIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    if any(e.title == body.title for e in project.epics):
        raise Conflict("Ya existe una épica con ese título")
    epic = Epic(project=project, title=body.title, description=body.description, position=body.position)
    db.add(epic)
    db.commit()
    return epic_out(epic)


@router.patch("/{key}/epics/{epic_id}")
def update_epic(key: str, epic_id: int, body: EpicIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    epic = db.get(Epic, epic_id)
    if epic is None or epic.project_id != project.id:
        raise NotFound("La épica no existe en este proyecto")
    epic.title, epic.description, epic.position = body.title, body.description, body.position
    db.commit()
    return epic_out(epic)


@router.get("/{key}/pipeline")
def get_pipeline(key: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal)
    stages = service.pipeline_out(db, project)
    db.commit()
    return stages


@router.post("/{key}/pipeline/{number}/approve")
def approve_stage(
    key: str,
    number: int,
    summary: str | None = Query(None),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    if principal.is_service:
        raise Unprocessable("La validación HITL la realiza un humano, no un servicio")
    project = load_project(db, key)
    role = require_member(db, project, principal, write=True)
    stage = service.approve_stage(db, project, number, principal.user, summary)
    return {"stage_number": stage.stage_number, "status": stage.status, "role": role}


@router.get("/{key}/reports/summary")
def report_summary(key: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal)
    return service.summary(db, project)


@router.get("/{key}/export")
def export_backlog(
    key: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    project = load_project(db, key)
    require_member(db, project, principal)
    rows = service.export_rows(project)
    if format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{key}-backlog.csv"'},
        )
    return {"project": key, "stories": rows}
