from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PipelineStage, Project, ProjectMember, User, utcnow
from app.services import hitl
from app.services.errors import Conflict, Unprocessable
from app.services.stories import open_violations
from app.services.workflow import STATUS_LABELS

STAGE_NAMES = {
    1: "Generación de HU",
    2: "Clasificación",
    3: "Estimación",
    4: "Priorización",
    5: "Smart Product Backlog",
}


def ensure_stages(db: Session, project: Project) -> list[PipelineStage]:
    existing = {s.stage_number: s for s in project.stages}
    for number in STAGE_NAMES:
        if number not in existing:
            stage = PipelineStage(project=project, stage_number=number, status="pending")
            db.add(stage)
            existing[number] = stage
    db.flush()
    return [existing[n] for n in sorted(existing)]


def approve_stage(db: Session, project: Project, number: int, user: User, summary: str | None) -> PipelineStage:
    """Validación HITL: sólo humanos con rol de escritura. Bloquea avanzar sin aprobar la etapa previa."""
    if number not in STAGE_NAMES:
        raise Unprocessable("La etapa debe estar entre 1 y 5")
    stages = {s.stage_number: s for s in ensure_stages(db, project)}
    stage = stages[number]
    if stage.status == "approved":
        raise Conflict(f"La etapa {number} ya está aprobada")
    if number > 1 and stages[number - 1].status != "approved":
        raise Unprocessable(f"Debes aprobar la etapa {number - 1} antes de la etapa {number}")
    if number == 1:
        # HITL: cada HU activa debe tener una revisión multiagente vigente aprobada por una persona.
        blockers = hitl.stage1_blockers(project)
        if blockers:
            listed = ", ".join(blockers[:10]) + (f" y {len(blockers) - 10} más" if len(blockers) > 10 else "")
            raise Unprocessable(
                f"Hay {len(blockers)} HU sin aprobar en la revisión humana: {listed}. "
                "Revísalas con los agentes y decide en la bandeja «Revisión humana»."
            )
        if not any(s.status != "descartada" for s in project.stories):
            raise Unprocessable("El proyecto no tiene HU activas: genera o crea al menos una antes de aprobar la etapa 1")
    if number == 3:
        pending = open_violations(project)
        if pending:
            listed = ", ".join(f"{v['story']} ({v['constraint_id']})" for v in pending[:10])
            raise Unprocessable(
                f"Hay {len(pending)} restricciones violadas sin resolver: {listed}. "
                "Cambia la HU y vuelve a dimensionarla, o registra una excepción justificada."
            )
    stage.status = "approved"
    stage.approved_by = user
    stage.approved_at = utcnow()
    stage.change_summary = summary
    db.commit()
    return stage


def pipeline_out(db: Session, project: Project) -> list[dict]:
    return [
        {
            "stage_number": s.stage_number,
            "name": STAGE_NAMES[s.stage_number],
            "status": s.status,
            "approved_by": s.approved_by.name if s.approved_by else None,
            "approved_at": s.approved_at.isoformat() if s.approved_at else None,
            "change_summary": s.change_summary,
        }
        for s in ensure_stages(db, project)
    ]


def add_member(db: Session, project: Project, user: User, role: str) -> ProjectMember:
    member = next((m for m in project.members if m.user_id == user.id), None)
    if member is None:
        member = ProjectMember(project=project, user=user, role=role)
        db.add(member)
    else:
        member.role = role
    db.flush()
    return member


def ensure_user(db: Session, email: str, name: str, password_hash: str | None) -> tuple[User, bool]:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        user = User(email=email.lower(), name=name, password_hash=password_hash)
        db.add(user)
        db.flush()
        return user, True
    return user, False


def summary(db: Session, project: Project) -> dict:
    stories = list(project.stories)
    total = len(stories)
    active = [s for s in stories if s.status != "descartada"]
    done = [s for s in active if s.status == "hecha"]

    def group(key_fn):
        out: dict[str, dict] = {}
        for s in stories:
            key = key_fn(s) or "Sin asignar"
            bucket = out.setdefault(key, {"count": 0, "function_points": 0.0})
            bucket["count"] += 1
            bucket["function_points"] += s.function_points or 0
        return out

    by_status = {
        st: {"label": label, **group(lambda s, st=st: st if s.status == st else None).get(st, {"count": 0, "function_points": 0.0})}
        for st, label in STATUS_LABELS.items()
    }

    return {
        "project": {"key": project.key, "name": project.name},
        "total_stories": total,
        "total_function_points": sum(s.function_points or 0 for s in active),
        "unsized_stories": sum(1 for s in active if s.function_points is None),
        "total_nfr_points": sum(s.nfr_points or 0 for s in active),
        "open_violations": open_violations(project),
        "completion_pct": round(100 * len(done) / len(active), 1) if active else 0.0,
        "by_status": by_status,
        "by_type": group(lambda s: s.type),
        "by_epic": group(lambda s: s.epic.title if s.epic else None),
        "by_assignee": group(lambda s: s.assignee.name if s.assignee else None),
        "pipeline": pipeline_out(db, project),
    }


def export_rows(project: Project) -> list[dict]:
    rows = []
    for s in sorted(project.stories, key=lambda s: (s.priority_rank is None, s.priority_rank or 0, s.id)):
        rows.append(
            {
                "code": s.code,
                "epic": s.epic.title if s.epic else "",
                "title": s.title,
                "role": s.role or "",
                "need": s.need or "",
                "benefit": s.benefit or "",
                "acceptance_criteria": s.acceptance_criteria or "",
                "type": s.type or "",
                "status": s.status,
                "function_points": s.function_points if s.function_points is not None else "",
                "nfr_points": s.nfr_points if s.nfr_points is not None else "",
                "priority_rank": s.priority_rank if s.priority_rank is not None else "",
                "assignee": s.assignee.email if s.assignee else "",
                "source_ref": s.source_ref or "",
            }
        )
    return rows
