from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, ProjectMember, User
from app.services.errors import Forbidden, NotFound
from app.services.workflow import WRITE_ROLES, Actor


@dataclass
class Principal:
    """Quien hace la petición: usuario con sesión o servicio ADK con API key."""

    user: User | None = None
    service_name: str | None = None

    @property
    def is_service(self) -> bool:
        return self.user is None


def load_project(db: Session, key: str) -> Project:
    project = db.scalar(select(Project).where(Project.key == key))
    if project is None:
        raise NotFound(f"Proyecto {key} no existe")
    return project


def project_role(db: Session, project: Project, principal: Principal) -> str | None:
    if principal.is_service:
        return "service"
    if principal.user.is_admin:
        return "product_owner"
    member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == principal.user.id
        )
    )
    return member.role if member else None


def require_member(db: Session, project: Project, principal: Principal, write: bool = False) -> str:
    role = project_role(db, project, principal)
    if role is None:
        raise NotFound(f"Proyecto {project.key} no existe")  # no revelar existencia a no miembros
    if write and role not in WRITE_ROLES and role != "service":
        raise Forbidden("Tu rol es de solo lectura en este proyecto")
    return role


def actor_for(principal: Principal, role: str | None) -> Actor:
    if principal.is_service:
        return Actor(kind="service", name=principal.service_name or "service", project_role="service")
    return Actor(
        kind="user",
        name=principal.user.name,
        user_id=principal.user.id,
        is_admin=principal.user.is_admin,
        project_role=role,
    )
