from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ApiKey, Project, User
from app.agents.registry import provider_status
from app.agents.runner import (
    request_changes,
    start_backlog_sizing,
    start_pending_reviews,
    start_review,
    start_context_extraction,
    start_generation,
    start_generation_from_documents,
    start_refinement,
    start_sizing,
)
from app.config import settings
from app.models import AgentRun
from app.routers.auth import authenticate
from app.routers.finops import project_finops
from app.security import generate_api_key, hash_api_key, hash_password
from app.services import projects as project_service
from app.services import stories as story_service
from app.services import audit
from app.services import hitl
from app.services import context as context_service
from app.services.access import Principal, actor_for, load_project, project_role, require_member
from app.services.documents import SOURCE_LABELS, get_document_source, load_document_text
from app.services.errors import AppError, Forbidden, PayloadTooLarge
from app.agents.runner import auto_refine_if_enabled
from app.services.refinement import notify_webhook, request_payload
from app.services.workflow import STATUS_LABELS, TRANSITIONS, check_transition

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Markdown seguro: html desactivado, así que el HTML crudo se muestra escapado (sin XSS).
_markdown = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")
templates.env.filters["md"] = lambda text: Markup(_markdown.render(text)) if text else Markup("")
# HITL en todas las pantallas del proyecto: contador de la bandeja en la barra lateral.
templates.env.globals["hitl_pending"] = lambda project: len(hitl.inbox(project)["decidir"]) if project else 0
templates.env.globals["hitl"] = hitl
router = APIRouter(include_in_schema=False)

BOARD_COLUMNS = ["borrador", "en_refinamiento", "refinada", "lista", "en_progreso", "en_revision", "hecha"]
TYPE_LABELS = {"HU_IA": "HU-IA", "HU_TRADICIONAL": "HU-Tradicional"}


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    user = db.get(User, uid) if uid else None
    if user is None or not user.is_active:
        raise LoginRequired()
    return user


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def render(request: Request, name: str, user: User | None, **ctx):
    ctx.setdefault("project", None)
    ctx.setdefault("role", None)
    ctx["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, name, {"user": user, "status_labels": STATUS_LABELS, "type_labels": TYPE_LABELS, **ctx})


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _opt(raw: str) -> str | None:
    return raw.strip() or None


def _documents(project: Project) -> dict:
    """Documentos del proyecto para las pantallas. Un bucket inaccesible no rompe la página."""
    label = SOURCE_LABELS[settings.agent_docs_source]
    try:
        source = get_document_source(project.key)
        return {"documents": source.list(), "docs_location": source.location, "docs_label": label, "docs_error": None}
    except AppError as exc:
        return {"documents": [], "docs_location": "", "docs_label": label, "docs_error": exc.message}


def _int(raw: str) -> int | None:
    return int(raw) if raw.strip() else None


def _project_for(db: Session, key: str, user: User, write: bool = False):
    project = load_project(db, key)
    role = require_member(db, project, Principal(user=user), write=write)
    return project, role


def _actor(user: User, role: str | None):
    return actor_for(Principal(user=user), role)


# ---------- sesión ----------

@router.get("/")
def root():
    return redirect("/projects")


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", None)


@router.post("/login")
def login_submit(request: Request, email: str = Form(""), password: str = Form(""), db: Session = Depends(get_db)):
    user = authenticate(db, email, password)
    if user is None:
        return render(request, "login.html", None, error="Credenciales inválidas", email=email)
    request.session["user_id"] = user.id
    return redirect("/projects")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


# ---------- proyectos ----------

@router.get("/projects")
def projects_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    all_projects = list(db.scalars(select(Project).order_by(Project.key)))
    visible = [p for p in all_projects if user.is_admin or project_role(db, p, Principal(user=user))]
    cards = []
    for p in visible:
        s = project_service.summary(db, p)
        cards.append({"project": p, "total": s["total_stories"], "pct": s["completion_pct"]})
    return render(request, "projects.html", user, cards=cards)


@router.post("/projects")
def create_project_submit(
    request: Request,
    key: str = Form(""),
    name: str = Form(""),
    description: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    key = key.strip().upper()
    try:
        if db.scalar(select(Project).where(Project.key == key)):
            raise AppError(f"Ya existe el proyecto {key}")
        project = Project(key=key, name=name.strip(), description=_opt(description), created_by_id=user.id)
        db.add(project)
        db.flush()
        project_service.add_member(db, project, user, "product_owner")
        db.commit()
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
        return redirect("/projects")
    return redirect(f"/p/{key}")


# ---------- dashboard ----------

@router.get("/p/{key}")
def dashboard(key: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project, role = _project_for(db, key, user)
    summary = project_service.summary(db, project)
    pipeline = summary["pipeline"]
    db.commit()  # persiste etapas HITL creadas perezosamente
    approved = sum(1 for s in pipeline if s["status"] == "approved")
    return render(
        request, "dashboard.html", user, project=project, role=role, summary=summary,
        pipeline=pipeline, approved=approved, can_write=role in ("product_owner", "scrum_master", "service") or user.is_admin,
        status_chart=[[v["label"], v["count"]] for v in summary["by_status"].values()],
        epic_chart=[[k, v["count"]] for k, v in summary["by_epic"].items()],
    )


@router.post("/p/{key}/pipeline/{number}/approve")
def approve_stage(
    key: str, number: int, request: Request, summary: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, _ = _project_for(db, key, user, write=True)
        project_service.approve_stage(db, project, number, user, _opt(summary))
        flash(request, f"Etapa {number} aprobada por {user.name}")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}")


# ---------- backlog ----------

SORTERS = {
    "priority": lambda s: (s.priority_rank is None, s.priority_rank or 0, s.id),
    "function_points": lambda s: -(s.function_points or 0),
    "status": lambda s: (list(STATUS_LABELS).index(s.status), s.id),
    "title": lambda s: s.title.lower(),
}


@router.get("/p/{key}/backlog")
def backlog(
    key: str, request: Request,
    status: str = "", type: str = "", epic_id: str = "", assignee_id: str = "", sort: str = "priority",
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    project, role = _project_for(db, key, user)
    rows = story_service.list_stories(
        db, project, status or None, type or None, int(epic_id) if epic_id else None, int(assignee_id) if assignee_id else None
    )
    rows.sort(key=SORTERS.get(sort, SORTERS["priority"]))
    groups = []
    for epic in project.epics:
        items = [s for s in rows if s.epic_id == epic.id]
        if items:
            groups.append({"epic": epic, "stories": items})
    orphans = [s for s in rows if s.epic_id is None]
    if orphans:
        groups.append({"epic": None, "stories": orphans})
    return render(
        request, "backlog.html", user, project=project, role=role, groups=groups,
        filters={"status": status, "type": type, "epic_id": epic_id, "assignee_id": assignee_id, "sort": sort},
        can_write=role in ("product_owner", "scrum_master", "service") or user.is_admin,
        agent_status=provider_status(), **_documents(project),
    )


@router.post("/p/{key}/stories")
def create_story_submit(
    key: str, request: Request,
    title: str = Form(""), epic: str = Form(""), role_: str = Form(""), need: str = Form(""), benefit: str = Form(""),
    criteria: str = Form(""), type: str = Form(""), type_justification: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, role = _project_for(db, key, user, write=True)
        data = {
            "title": title.strip(),
            "epic": _opt(epic),
            "role": _opt(role_),
            "need": _opt(need),
            "benefit": _opt(benefit),
            "acceptance_criteria": _opt(criteria),
            "type": _opt(type),
            "type_justification": _opt(type_justification),
        }
        if len(data["title"]) < 3:
            raise AppError("El título debe tener al menos 3 caracteres")
        story = story_service.create_story(db, project, data, _actor(user, role))
        db.commit()
        flash(request, f"HU {story.code} creada")
        return redirect(f"/p/{key}/stories/{story.code}")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
        return redirect(f"/p/{key}/backlog")


# ---------- tablero ----------

@router.get("/p/{key}/board")
def board(key: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project, role = _project_for(db, key, user)
    stories = story_service.list_stories(db, project)
    columns = [{"status": st, "label": STATUS_LABELS[st], "stories": [s for s in stories if s.status == st]} for st in BOARD_COLUMNS]
    discarded = sum(1 for s in stories if s.status == "descartada")
    return render(
        request, "board.html", user, project=project, role=role, columns=columns, discarded=discarded,
        can_write=role in ("product_owner", "scrum_master", "developer", "service") or user.is_admin,
    )


# ---------- detalle de HU ----------

@router.get("/p/{key}/stories/{code}")
def story_detail(key: str, code: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project, role = _project_for(db, key, user)
    story = story_service.get_story(db, project, code)
    actor = _actor(user, role)
    allowed = []
    for target in sorted(TRANSITIONS.get(story.status, set())):
        try:
            check_transition(story.status, target, actor)
            allowed.append(target)
        except AppError:
            continue
    write = role in ("product_owner", "scrum_master", "developer") or user.is_admin
    return render(
        request, "story.html", user, project=project, role=role, story=story,
        allowed=allowed, can_write=write, can_edit=role in ("product_owner", "scrum_master") or user.is_admin,
        events=list(reversed(story.events)), refinement=story.refinements[-1] if story.refinements else None,
        members=project.members, epics=project.epics, agent_status=provider_status(),
    )


@router.post("/p/{key}/stories/{code}/edit")
def edit_story(
    key: str, code: str, request: Request,
    title: str = Form(""), epic: str = Form(""), role_: str = Form(""), need: str = Form(""), benefit: str = Form(""),
    criteria: str = Form(""), type: str = Form(""), type_justification: str = Form(""),
    priority_rank: str = Form(""), priority_method: str = Form(""), source_ref: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, role = _project_for(db, key, user, write=True)
        if role == "developer" and not user.is_admin:
            raise Forbidden("Los developers no editan la definición de la HU")
        story = story_service.get_story(db, project, code)
        data = {
            "title": title.strip(),
            "epic": _opt(epic),
            "role": _opt(role_),
            "need": _opt(need),
            "benefit": _opt(benefit),
            "acceptance_criteria": _opt(criteria),
            "type": _opt(type),
            "type_justification": _opt(type_justification),
            "priority_rank": _int(priority_rank),
            "priority_method": _opt(priority_method),
            "source_ref": _opt(source_ref),
        }
        if len(data["title"]) < 3:
            raise AppError("El título debe tener al menos 3 caracteres")
        story_service.apply_changes(db, project, story, data, _actor(user, role))
        db.commit()
        flash(request, "HU actualizada")
    except ValueError:
        db.rollback()
        flash(request, "La prioridad debe ser un número", "error")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/stories/{code}")


@router.post("/p/{key}/stories/{code}/status")
def change_status(
    key: str, code: str, request: Request, background: BackgroundTasks, status: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, role = _project_for(db, key, user)
        story = story_service.get_story(db, project, code)
        req = story_service.transition(db, story, status, _actor(user, role), user_id=user.id)
        db.commit()
        flash(request, f"{code} → {STATUS_LABELS[status]}")
        if req is not None:
            background.add_task(notify_webhook, request_payload(req))
            auto_refine_if_enabled(db, project, req, user.id, background)
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/stories/{code}")


@router.post("/p/{key}/stories/{code}/assign")
def assign_story(
    key: str, code: str, request: Request, user_id: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, role = _project_for(db, key, user, write=True)
        story = story_service.get_story(db, project, code)
        target = next((m.user for m in project.members if str(m.user_id) == user_id), None)
        old = story.assignee.name if story.assignee else None
        story.assignee = target
        story.version += 1
        audit.record(db, story, _actor(user, role), "assigned", diff={"from": old, "to": target.name if target else None})
        db.commit()
        flash(request, "Asignación actualizada")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/stories/{code}")


# ---------- reportes y miembros ----------

@router.get("/p/{key}/reports")
def reports(key: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project, role = _project_for(db, key, user)
    summary = project_service.summary(db, project)
    db.commit()
    return render(
        request, "reports.html", user, project=project, role=role, summary=summary, finops=project_finops(project),
        context=context_service.overview(db, project),
        can_write=role in ("product_owner", "scrum_master") or user.is_admin, agent_status=provider_status(),
    )


@router.get("/p/{key}/members")
def members_page(key: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project, role = _project_for(db, key, user)
    return render(request, "members.html", user, project=project, role=role, can_write=role == "product_owner" or user.is_admin)


@router.post("/p/{key}/members")
def add_member_submit(
    key: str, request: Request, email: str = Form(""), role_: str = Form("developer"),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, _ = _project_for(db, key, user, write=True)
        target = db.scalar(select(User).where(User.email == email.strip().lower()))
        if target is None:
            raise AppError(f"No existe un usuario con email {email}")
        project_service.add_member(db, project, target, role_)
        db.commit()
        flash(request, f"{target.name} agregado como {role_}")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/members")


@router.post("/p/{key}/members/{user_id}/remove")
def remove_member_submit(key: str, user_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        project, _ = _project_for(db, key, user, write=True)
        member = next((m for m in project.members if m.user_id == user_id), None)
        if member:
            db.delete(member)
            db.commit()
        flash(request, "Miembro removido")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/members")


# ---------- administración ----------

@router.get("/admin")
def admin_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.is_admin:
        raise Forbidden("Requiere rol de administrador")
    users = list(db.scalars(select(User).order_by(User.email)))
    keys = list(db.scalars(select(ApiKey).order_by(ApiKey.id)))
    new_key = request.session.pop("new_key", None)
    return render(request, "admin.html", user, users=users, keys=keys, new_key=new_key)


@router.post("/admin/users")
def admin_create_user(
    request: Request, email: str = Form(""), name: str = Form(""), password: str = Form(""), is_admin: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    if not user.is_admin:
        raise Forbidden("Requiere rol de administrador")
    try:
        if db.scalar(select(User).where(User.email == email.strip().lower())):
            raise AppError("Ya existe un usuario con ese email")
        db.add(User(
            email=email.strip().lower(), name=name.strip(), is_admin=bool(is_admin),
            password_hash=hash_password(password) if len(password) >= 8 else None,
        ))
        db.commit()
        flash(request, f"Usuario {email} creado" + ("" if len(password) >= 8 else " (sin contraseña: no podrá entrar hasta que se defina)"))
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect("/admin")


@router.post("/admin/api-keys")
def admin_create_key(request: Request, name: str = Form(""), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.is_admin:
        raise Forbidden("Requiere rol de administrador")
    try:
        if db.scalar(select(ApiKey).where(ApiKey.name == name.strip())):
            raise AppError("Ya existe una API key con ese nombre")
        raw = generate_api_key()
        db.add(ApiKey(name=name.strip(), key_hash=hash_api_key(raw)))
        db.commit()
        request.session["new_key"] = {"name": name.strip(), "key": raw}
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect("/admin")


# ---------- agentes IA ----------

def _agent_message(run) -> str:
    if run.status == "succeeded":
        return f"Agente completado: {run.result}"
    if run.status == "failed":
        return f"El agente falló: {run.error}"
    return "Agente en curso; revisa Agentes para ver el resultado"


@router.get("/p/{key}/agents")
def agents_page(key: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project, role = _project_for(db, key, user)
    runs = list(db.scalars(
        select(AgentRun).where(AgentRun.project_id == project.id).order_by(AgentRun.id.desc()).limit(50)
    ))
    return render(
        request, "agents.html", user, project=project, role=role, runs=runs,
        status=provider_status(), **_documents(project),
        can_write=role in ("product_owner", "scrum_master", "service") or user.is_admin,
    )


@router.post("/p/{key}/agents/generate")
def generate_submit(
    key: str, request: Request, background: BackgroundTasks,
    filename: str = Form(""), document_text: str = Form(""), source_name: str = Form(""), instructions: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, _ = _project_for(db, key, user, write=True)
        if bool(filename) == bool(document_text.strip()):
            raise AppError("Elige un archivo de la carpeta o pega texto (solo uno de los dos)")
        if filename:
            data = get_document_source(project.key).read_bytes(filename)
            text = load_document_text(filename, data, settings.agent_max_doc_chars)
            source = filename
        else:
            text = document_text.strip()
            if len(text) > settings.agent_max_doc_chars:
                raise PayloadTooLarge(f"El texto supera {settings.agent_max_doc_chars} caracteres")
            source = source_name.strip() or "texto pegado"
        run = start_generation(
            db, project, source_name=source, text=text, instructions=_opt(instructions),
            requested_by_id=user.id, background=background,
        )
        flash(request, _agent_message(run), "ok" if run.status != "failed" else "error")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/agents")


@router.post("/p/{key}/agents/generate-from-documents")
def generate_from_documents_submit(
    key: str, request: Request, background: BackgroundTasks, instructions: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, _ = _project_for(db, key, user, write=True)
        run = start_generation_from_documents(
            db, project, instructions=_opt(instructions), requested_by_id=user.id, background=background,
        )
        flash(request, _agent_message(run), "ok" if run.status != "failed" else "error")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/agents")


@router.post("/p/{key}/stories/{code}/agents/size")
def size_submit(
    key: str, code: str, request: Request, background: BackgroundTasks,
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, _role = _project_for(db, key, user, write=True)
        story = story_service.get_story(db, project, code)
        run = start_sizing(db, project, story, user_id=user.id, instructions=None, background=background)
        flash(request, _agent_message(run), "ok" if run.status != "failed" else "error")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/stories/{code}")


@router.post("/p/{key}/agents/extract-context")
def extract_context_submit(
    key: str, request: Request, background: BackgroundTasks,
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, _role = _project_for(db, key, user, write=True)
        run = start_context_extraction(db, project, user_id=user.id, instructions=None, background=background)
        flash(request, _agent_message(run), "ok" if run.status != "failed" else "error")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/reports")


@router.post("/p/{key}/context/{decision}")
def review_context_submit(
    key: str, decision: str, request: Request, notes: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, _role = _project_for(db, key, user, write=True)
        if decision not in ("approve", "reject"):
            raise AppError("Decisión inválida")
        review = context_service.approve if decision == "approve" else context_service.reject
        graph = review(db, project, user, _opt(notes))
        db.commit()
        flash(request, f"Grafo de contexto v{graph.version}: {'aprobado' if decision == 'approve' else 'rechazado'}")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/reports")


@router.post("/p/{key}/stories/{code}/violations/{constraint_id}/waive")
def waive_submit(
    key: str, code: str, constraint_id: str, request: Request, justification: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, role = _project_for(db, key, user, write=True)
        story = story_service.get_story(db, project, code)
        story_service.waive_violation(db, story, constraint_id, _actor(user, role), justification)
        db.commit()
        flash(request, f"Excepción registrada para {constraint_id}")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/stories/{code}")


@router.post("/p/{key}/agents/size-backlog")
def size_backlog_submit(
    key: str, request: Request, background: BackgroundTasks, resize: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, _role = _project_for(db, key, user, write=True)
        run = start_backlog_sizing(
            db, project, resize=bool(resize), user_id=user.id, instructions=None, background=background,
        )
        flash(request, _agent_message(run), "ok" if run.status != "failed" else "error")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/reports")


@router.post("/p/{key}/stories/{code}/agents/refine")
def refine_submit(
    key: str, code: str, request: Request, background: BackgroundTasks, instructions: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, role = _project_for(db, key, user, write=True)
        story = story_service.get_story(db, project, code)
        run = start_refinement(
            db, project, story, actor=_actor(user, role), user_id=user.id,
            instructions=_opt(instructions), background=background,
        )
        flash(request, _agent_message(run), "ok" if run.status != "failed" else "error")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/stories/{code}")


# ---------- revisión humana (HITL) ----------

def _can_write(role: str | None, user: User) -> bool:
    return role in ("product_owner", "scrum_master") or user.is_admin


@router.get("/p/{key}/revision")
def hitl_inbox(key: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project, role = _project_for(db, key, user)
    box = hitl.inbox(project)
    stages = {s["stage_number"]: s for s in project_service.pipeline_out(db, project)}
    db.commit()
    return render(
        request, "revision.html", user, project=project, role=role, box=box, stage1=stages[1],
        blockers=hitl.stage1_blockers(project), can_write=_can_write(role, user), agent_status=provider_status(),
        running=bool(box["en_curso"]) and any(r["state"]["key"] == "analizando" for r in box["en_curso"]),
    )


@router.post("/p/{key}/hitl/review-pending")
def hitl_review_pending(key: str, request: Request, background: BackgroundTasks,
                        user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        project, _role = _project_for(db, key, user, write=True)
        run = start_pending_reviews(db, project, user_id=user.id, background=background)
        flash(request, f"Revisión con agentes en curso para {run.input_ref}" if run.status in ("queued", "running")
              else _review_message(run), "ok" if run.status != "failed" else "error")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(f"/p/{key}/revision")


@router.post("/p/{key}/stories/{code}/hitl/review")
def hitl_review_story(key: str, code: str, request: Request, background: BackgroundTasks, next: str = Form(""),
                      user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        project, _role = _project_for(db, key, user, write=True)
        story = story_service.get_story(db, project, code)
        run = start_review(db, project, [story], user_id=user.id, background=background)
        flash(request, f"Revisando {code} con los agentes" if run.status in ("queued", "running")
              else _review_message(run), "ok" if run.status != "failed" else "error")
    except AppError as exc:
        db.rollback()
        flash(request, exc.message, "error")
    return redirect(_back(next, f"/p/{key}/stories/{code}"))


@router.post("/p/{key}/stories/{code}/hitl")
def hitl_decide(
    key: str, code: str, request: Request, background: BackgroundTasks,
    decision: str = Form(...), comment: str = Form(""), apply_proposal: str = Form(""),
    expected_version: str = Form(""), next: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    try:
        project, role = _project_for(db, key, user)
        story = story_service.get_story(db, project, code)
        version = _int(expected_version)
        if decision == "aprobar_propuesta":
            decision, apply_proposal = "aprobar", "1"
        if decision == "pedir_cambios":
            request_changes(db, project, story, user=user, role=role, comment=comment, expected_version=version,
                            background=background)
            message = f"{code}: cambios pedidos; el agente la refina y vuelve a revisión"
        else:
            hitl.decide(db, project, story, user=user, role=role, decision=decision, comment=_opt(comment),
                        apply_proposal=bool(apply_proposal), expected_version=version)
            db.commit()
            message = f"{code}: {hitl.DECISIONS[decision].lower()}" + (" con la propuesta aplicada" if apply_proposal else "")
        flash(request, message)
    except (AppError, ValueError) as exc:
        db.rollback()
        flash(request, getattr(exc, "message", str(exc)), "error")
    return redirect(_back(next, f"/p/{key}/revision"))


def _back(next_url: str, default: str) -> str:
    # Solo rutas internas del proyecto (evita redirecciones abiertas).
    return next_url if next_url.startswith("/p/") and "//" not in next_url else default


def _review_message(run) -> str:
    if run.status == "failed":
        return f"La revisión falló: {run.error}"
    result = run.result or {}
    levels = ", ".join(f"{r['story']} nivel {r['level']}" for r in result.get("reviewed", []))
    failed = ", ".join(f["story"] for f in result.get("failed", []))
    return f"Revisión terminada: {levels or 'sin HU'}" + (f". Fallaron: {failed}" if failed else "")
