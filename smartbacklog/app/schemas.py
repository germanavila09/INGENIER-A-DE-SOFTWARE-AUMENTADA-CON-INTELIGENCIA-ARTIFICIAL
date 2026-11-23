from typing import Literal

from pydantic import BaseModel, Field

from app.models import Epic, RefinementRequest, UserStory

StoryType = Literal["HU_IA", "HU_TRADICIONAL"]
ProjectRole = Literal["product_owner", "scrum_master", "developer", "stakeholder"]


class StoryIn(BaseModel):
    external_id: str | None = Field(None, max_length=120)
    epic: str | None = Field(None, max_length=255, description="Título de la épica; se crea si no existe")
    title: str = Field(min_length=3, max_length=255)
    role: str | None = None
    need: str | None = None
    benefit: str | None = None
    acceptance_criteria: str | None = Field(None, max_length=20000, description="Criterios en markdown (checklist o Gherkin)")
    type: StoryType | None = None
    type_justification: str | None = None
    fp_components: list[dict] | None = Field(
        None, description="Funciones IFPUG: [{name, type: ILF|EIF|EI|EO|EQ, det, ret|ftr, reused}]; el servidor calcula los puntos"
    )
    priority_rank: int | None = None
    priority_method: str | None = None
    source_ref: str | None = None
    assignee_email: str | None = Field(None, description="Correo de un miembro del proyecto")


class StoryPatch(BaseModel):
    epic: str | None = Field(None, max_length=255)
    title: str | None = Field(None, min_length=3, max_length=255)
    role: str | None = None
    need: str | None = None
    benefit: str | None = None
    acceptance_criteria: str | None = Field(None, max_length=20000)
    type: StoryType | None = None
    type_justification: str | None = None
    fp_components: list[dict] | None = Field(
        None, description="Funciones IFPUG: [{name, type: ILF|EIF|EI|EO|EQ, det, ret|ftr, reused}]; el servidor calcula los puntos"
    )
    priority_rank: int | None = None
    priority_method: str | None = None
    source_ref: str | None = None
    assignee_email: str | None = None
    expected_version: int | None = None


class StatusIn(BaseModel):
    status: str
    expected_version: int | None = None


class AssignIn(BaseModel):
    user_id: int | None = None
    assignee_email: str | None = None


class BulkStoriesIn(BaseModel):
    stories: list[StoryIn] = Field(min_length=1)


class ProjectIn(BaseModel):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}$")
    name: str = Field(min_length=2, max_length=200)
    description: str | None = None


class MemberIn(BaseModel):
    email: str
    role: ProjectRole


class EpicIn(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    description: str | None = None
    position: int = 0


class UserIn(BaseModel):
    email: str
    name: str = Field(min_length=2, max_length=120)
    password: str | None = Field(None, min_length=8)
    is_admin: bool = False


class IngestUser(BaseModel):
    email: str
    name: str = Field(min_length=2, max_length=120)
    password: str | None = Field(None, min_length=8)


class IngestMember(BaseModel):
    email: str
    role: ProjectRole


class IngestEpic(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    description: str | None = None
    stories: list[StoryIn] = Field(default_factory=list)


class IngestProject(BaseModel):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}$")
    name: str = Field(min_length=2, max_length=200)
    description: str | None = None
    users: list[IngestUser] = Field(default_factory=list)
    members: list[IngestMember] = Field(default_factory=list)
    epics: list[IngestEpic] = Field(default_factory=list)
    stories: list[StoryIn] = Field(default_factory=list, description="HU sin épica")


class RefinementResult(BaseModel):
    expected_version: int
    title: str | None = Field(None, min_length=3, max_length=255)
    role: str | None = None
    need: str | None = None
    benefit: str | None = None
    acceptance_criteria: str | None = Field(None, max_length=20000)
    notes: str | None = None


class ApproveIn(BaseModel):
    summary: str | None = None


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)


# ---------- serializadores ----------

def iso(value) -> str | None:
    return value.isoformat() if value else None


def user_out(user) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin, "is_active": user.is_active}


def story_out(s: UserStory) -> dict:
    return {
        "id": s.id,
        "code": s.code,
        "external_id": s.external_id,
        "epic_id": s.epic_id,
        "epic": s.epic.title if s.epic else None,
        "title": s.title,
        "role": s.role,
        "need": s.need,
        "benefit": s.benefit,
        "acceptance_criteria": s.acceptance_criteria,
        "type": s.type,
        "type_justification": s.type_justification,
        "status": s.status,
        "function_points": s.function_points,
        "fp_analysis": s.fp_analysis,
        "nfr_points": s.nfr_points,
        "context_analysis": s.context_analysis,
        "open_violations": [v["constraint_id"] for v in (s.context_analysis or {}).get("violations", [])
                            if v.get("status") == "open"],
        "priority_rank": s.priority_rank,
        "priority_method": s.priority_method,
        "assignee": {"id": s.assignee.id, "name": s.assignee.name, "email": s.assignee.email} if s.assignee else None,
        "source_ref": s.source_ref,
        "version": s.version,
        "updated_at": iso(s.updated_at),
    }


def event_out(e) -> dict:
    return {
        "id": e.id,
        "actor_type": e.actor_type,
        "actor_name": e.actor_name,
        "event_type": e.event_type,
        "from_status": e.from_status,
        "to_status": e.to_status,
        "diff": e.diff,
        "created_at": iso(e.created_at),
    }


def epic_out(e: Epic) -> dict:
    return {"id": e.id, "title": e.title, "description": e.description, "position": e.position}


def refinement_out(r: RefinementRequest) -> dict:
    return {
        "id": r.id,
        "story_code": r.story.code,
        "project_key": r.story.project.key,
        "status": r.status,
        "claimed_by": r.claimed_by,
        "claimed_at": iso(r.claimed_at),
        "completed_at": iso(r.completed_at),
        "created_at": iso(r.created_at),
        "input": r.input_snapshot,
        "result": r.result,
        "error": r.error,
    }
