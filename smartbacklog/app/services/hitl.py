"""Human-in-the-loop como función central: revisión multiagente de cada HU y decisión humana.

Traído del orquestador hu_multiagent (política «automatizar si es seguro · revisar si hay duda · detener si
es crítico»), con dos cambios para SmartBacklogIA:

- Nada avanza solo. Toda revisión espera una decisión humana; el nivel define QUIÉN puede decidir:
  nivel 2 (crítico o con información faltante) solo el product owner; niveles 1 y 0, product owner o
  scrum master. Developers y stakeholders comentan y responden preguntas.
- La etapa 1 del pipeline no se aprueba mientras haya HU activas sin revisión vigente aprobada.

El nivel lo calculan reglas en código a partir de la salida de los agentes; el modelo nunca decide su
propio nivel. Funciones puras arriba; las que tocan la base, abajo.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.agents.ports import ReviewInput, ReviewOutput
from app.config import settings
from app.models import HitlDecision, Project, StoryReview, User, UserStory, utcnow
from app.services import audit, estimation
from app.services import stories as story_service
from app.services.errors import Conflict, Forbidden, Unprocessable
from app.services.workflow import Actor

LEVEL_LABELS = {0: "Sin observaciones", 1: "Revisar", 2: "Requiere al product owner"}
DECISIONS = {
    "aprobar": "Aprobada",
    "pedir_cambios": "Cambios pedidos",
    "descartar": "Descartada",
    "falta_info": "Falta información",
    "comentario": "Comentario",
}
STATE_LABELS = {
    "sin_revisar": "Sin revisión",
    "analizando": "Analizando",
    "pendiente": "Espera decisión",
    "aprobada": "Aprobada",
    "cambios": "Cambios pedidos",
    "falta_info": "Falta información",
    "descartada": "Descartada",
    "desactualizada": "Cambió desde la revisión",
    "error": "La revisión falló",
}
# Estados que piden una acción humana (orden de la bandeja: los de mayor riesgo primero).
NEEDS_HUMAN = ("pendiente", "falta_info")
NEEDS_AGENT = ("sin_revisar", "desactualizada", "error")
ROLES_BY_LEVEL = {2: {"product_owner"}, 1: {"product_owner", "scrum_master"}, 0: {"product_owner", "scrum_master"}}
COMMENT_ROLES = {"product_owner", "scrum_master", "developer", "stakeholder"}
ACTIVE_EXCLUDED = ("descartada",)

CRITICAL_CHANGE_TEXT = {
    "SECURITY_MODEL_CHANGE": ("SECURITY_CHANGE", "Cambio en el modelo de seguridad"),
    "ARCHITECTURE_CHANGE": ("ARCHITECTURE_CHANGE", "Cambio de arquitectura"),
    "DATA_MIGRATION": ("MIGRATION", "Migración de datos"),
    "COMPONENT_REMOVAL": ("DELETION", "Eliminación de componentes"),
    "DATA_DELETION": ("DELETION", "Eliminación de información"),
    "PUBLIC_API_CHANGE": ("PUBLIC_API_CHANGE", "Cambio de API pública"),
    "IRREVERSIBLE_CHANGE": ("IRREVERSIBLE_CHANGE", "Cambio irreversible"),
    "SIGNIFICANT_COST_INCREASE": ("COST_INCREASE", "Aumento considerable de costos"),
    "CROSS_PROJECT_IMPACT": ("CROSS_PROJECT_IMPACT", "Decisión que puede afectar otros proyectos"),
}
APPROVING = {"APPROVED", "APPROVED_WITH_OBSERVATIONS"}
AGENT_NAMES = {"analysis": "analista", "architecture": "arquitectura", "qa": "QA", "estimation": "estimación"}


# ============================================================ reglas (puras)
def _reason(code: str, description: str, agent: str, severity: str, stop: bool) -> dict:
    return {"code": code, "description": description, "agent": agent, "severity": severity, "stop": stop}


def criteria_lines(markdown: str | None) -> list[str]:
    out = []
    for line in (markdown or "").splitlines():
        text = line.strip()
        for prefix in ("- [ ]", "- [x]", "- [X]", "* [ ]", "-", "*"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        if text and not text.startswith("#"):
            out.append(text)
    return out


def evaluate(out: ReviewOutput, story: dict, *, approved_before: bool = False, generated: bool = False,
             confidence_auto: float | None = None, confidence_review: float | None = None) -> dict:
    """Combina la salida de los agentes y calcula el nivel HITL con sus motivos (sin LLM)."""
    auto = settings.hitl_confidence_auto if confidence_auto is None else confidence_auto
    review_min = settings.hitl_confidence_review if confidence_review is None else confidence_review
    a, r, q, e = out.analysis, out.architecture, out.qa, out.estimation
    reasons: list[dict] = []

    # 0. Agente sin salida válida: no se puede decidir sobre una revisión incompleta.
    for key in ("analysis", "architecture", "qa"):
        if getattr(out, key) is None:
            reasons.append(_reason("MISSING_AGENT_OUTPUT", f"El agente de {AGENT_NAMES[key]} no produjo una salida "
                                   f"válida ({out.errors.get(key, 'sin salida')}).", AGENT_NAMES[key], "alta", True))

    # 1. Cambios críticos: nivel 2 aunque la confianza sea alta.
    for change in (r.critical_changes if r else []):
        code, text = CRITICAL_CHANGE_TEXT.get(change, (change, change))
        reasons.append(_reason(code, text, "arquitectura", "critica", True))

    # 2. Conflictos entre agentes.
    statuses = {k: getattr(out, k).status for k in ("architecture", "qa") if getattr(out, k) is not None}
    if any(s in APPROVING for s in statuses.values()) and "CRITICAL_RISK" in statuses.values():
        reasons.append(_reason("AGENT_CONFLICT", "Un agente aprueba mientras otro marca riesgo crítico.",
                               "agregador", "critica", True))
    if r and r.status == "APPROVED" and r.critical_changes:
        reasons.append(_reason("AGENT_CONFLICT", "Arquitectura aprueba sin observaciones pero declara cambios críticos.",
                               "agregador", "alta", True))
    if a and q and a.invest.testable >= 4 and q.untestable_criteria:
        reasons.append(_reason("AGENT_CONFLICT", f"El analista califica la HU como verificable ({a.invest.testable}/5) pero "
                               f"QA encontró {len(q.untestable_criteria)} criterio(s) no verificable(s).",
                               "agregador", "media", False))

    # 3. Riesgo crítico declarado.
    for key, status in statuses.items():
        if status == "CRITICAL_RISK":
            reasons.append(_reason("CRITICAL_RISK", getattr(out, key).summary or "Riesgo crítico", AGENT_NAMES[key],
                                   "critica", True))

    # 4. Preguntas bloqueantes: información funcional crítica ambigua.
    blocking = [x.question for x in (a.questions if a else []) if x.blocking]
    if blocking:
        reasons.append(_reason("CRITICAL_AMBIGUITY", f"{len(blocking)} pregunta(s) bloqueante(s).", "analista", "alta", True))

    # 5. Cambios sobre una HU que una persona ya había aprobado.
    if approved_before and a and a.suggested_changes:
        reasons.append(_reason("APPROVED_STORY_MODIFICATION", f"La HU ya estaba aprobada y se proponen "
                               f"{len(a.suggested_changes)} cambio(s).", "analista", "alta", True))

    # 6. Criterios: ninguno o mayoría no verificable → nivel 2; algunos → revisión.
    n_criteria = len(criteria_lines(story.get("acceptance_criteria")))
    n_untestable = len(q.untestable_criteria) if q else 0
    if n_criteria == 0:
        reasons.append(_reason("NO_ACCEPTANCE_CRITERIA", "La HU no tiene criterios de aceptación.", "QA", "alta", True))
    elif n_untestable:
        mayoria = n_untestable * 2 > n_criteria
        reasons.append(_reason("UNVERIFIABLE_CRITERIA", f"{n_untestable} de {n_criteria} criterio(s) no son verificables.",
                               "QA", "alta" if mayoria else "media", mayoria))

    # 7. Un agente pidió intervención humana.
    for key in ("analysis", "architecture"):
        x = getattr(out, key)
        if x is not None and x.requires_hitl and not any(z["agent"] == AGENT_NAMES[key] and z["stop"] for z in reasons):
            reasons.append(_reason("AGENT_REQUESTED", x.hitl_reason or "El agente pide revisión humana.",
                                   AGENT_NAMES[key], "alta", True))

    # 8. Confianza: la mínima de los agentes que deciden (la estimación es informativa).
    confs = [x.confidence for x in (a, r, q) if x is not None]
    confidence = round(min(confs), 2) if confs else 0.0
    if confidence < review_min:
        reasons.append(_reason("LOW_CONFIDENCE", f"Confianza {confidence:.2f} menor que {review_min}.", "evaluador", "alta", True))
    elif confidence < auto:
        reasons.append(_reason("MEDIUM_CONFIDENCE", f"Confianza {confidence:.2f} menor que {auto}.", "evaluador", "media", False))

    # 9. HU propuesta por IA a partir de documentos: siempre la valida una persona.
    if generated:
        reasons.append(_reason("GENERATED_STORY", "HU generada por IA desde los documentos del proyecto.",
                               "generador", "media", False))

    # 10. Mejoras de redacción.
    if a and a.suggested_changes and not approved_before:
        reasons.append(_reason("WORDING_IMPROVEMENT", f"{len(a.suggested_changes)} mejora(s) propuesta(s).",
                               "analista", "baja", False))

    # 11. Estimación por factores (informativa).
    est, est_error = estimation.from_agent_output(e.model_dump() if e else None)
    if est and est["spike"]["required"]:
        f = est["factors"]
        reasons.append(_reason("SPIKE_REQUIRED", f"HU-IA con datos {f.get('datos')}/5 y riesgo {f.get('riesgo')}/5: "
                               f"planear un spike de {est['spike']['points']} pts.", "estimación", "media", False))
    elif est_error:
        reasons.append(_reason("ESTIMATION_INCOMPLETE", f"Sin estimación automática: {est_error}", "estimación", "baja", False))

    level = 2 if any(x["stop"] for x in reasons) else (1 if reasons else 0)
    quality = round(sum(a.invest.model_dump().values()) / 30 * 100, 1) if a else None
    proposal = None
    if a:
        proposal = {
            "title": (a.improved_title or story.get("title") or "")[:255],
            "role": a.improved_role, "need": a.improved_need, "benefit": a.improved_benefit,
            "acceptance_criteria": "\n".join(f"- [ ] {c}" for c in a.improved_criteria) or None,
            "suggested_changes": a.suggested_changes,
        }
    if est:
        est = {**est, "type_justification": e.type_justification, "rationale": e.rationale}
    return {
        "level": level, "confidence": confidence, "quality": quality,
        "story_type": e.story_type if e else None,
        "reasons": sorted(reasons, key=lambda x: (not x["stop"], x["code"])),
        "questions": [{"question": x.question, "blocking": x.blocking} for x in (a.questions if a else [])],
        "proposal": proposal, "estimation": est, "estimation_error": est_error,
        "agents": {k: (getattr(out, k).model_dump() if getattr(out, k) is not None else None)
                   for k in ("analysis", "architecture", "qa", "estimation")} | {"errors": out.errors},
    }


def proposal_changes(story: UserStory, proposal: dict | None) -> dict:
    """Campos que cambiarían si se aplica la propuesta (sin vacíos)."""
    if not proposal:
        return {}
    out = {}
    for field in ("title", "role", "need", "benefit", "acceptance_criteria"):
        value = proposal.get(field)
        if value and value != getattr(story, field):
            out[field] = value
    return out


# ============================================================ estado
def latest_review(story: UserStory) -> StoryReview | None:
    return story.reviews[-1] if story.reviews else None


CONTENT_FIELDS = ("title", "role", "need", "benefit", "acceptance_criteria")


def content_hash(story: UserStory) -> str:
    """Huella de lo que revisan los agentes. Estado, prioridad, asignación y tamaño no la cambian."""
    data = {f: (getattr(story, f) or "").strip() for f in CONTENT_FIELDS}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def is_current(review: StoryReview, story: UserStory) -> bool:
    if review.content_hash:
        return content_hash(story) in (review.content_hash, review.applied_hash)
    return story.version in (review.story_version, review.applied_version)  # revisiones sin huella


def last_decision(review: StoryReview | None) -> HitlDecision | None:
    if review is None:
        return None
    return next((d for d in reversed(review.decisions) if d.decision != "comentario"), None)


def state(story: UserStory) -> dict:
    """Estado HITL de la HU, derivado de su última revisión y de la última decisión sobre ella."""
    review = latest_review(story)
    decision = last_decision(review)
    if review is None:
        key = "descartada" if story.status == "descartada" else "sin_revisar"
    elif review.status in ("queued", "running"):
        key = "analizando"
    elif review.status == "failed":
        key = "error"
    elif decision is not None and decision.decision == "descartar":
        key = "descartada"
    elif story.status == "descartada":
        key = "descartada"
    elif decision is not None and decision.decision == "pedir_cambios":
        key = "cambios"
    elif not is_current(review, story):
        key = "desactualizada"
    elif decision is None:
        key = "pendiente"
    else:
        key = {"aprobar": "aprobada", "falta_info": "falta_info"}[decision.decision]
    return {"key": key, "label": STATE_LABELS[key], "review": review, "decision": decision,
            "level": review.level if review is not None and review.status == "done" else None}


def can_decide(role: str | None, level: int | None, decision: str) -> bool:
    if decision == "comentario":
        return role in COMMENT_ROLES
    return role in ROLES_BY_LEVEL.get(level if level is not None else 2, set())


def inbox(project: Project) -> dict:
    """Bandeja HITL: grupos de HU por lo que necesitan, de mayor a menor riesgo."""
    rows = []
    for s in sorted(project.stories, key=lambda s: s.id):
        st = state(s)
        rows.append({"story": s, "state": st})
    level = lambda r: -(r["state"]["level"] if r["state"]["level"] is not None else 3)  # noqa: E731
    return {
        "decidir": sorted([r for r in rows if r["state"]["key"] in NEEDS_HUMAN], key=lambda r: (level(r), r["story"].id)),
        "agentes": [r for r in rows if r["state"]["key"] in NEEDS_AGENT],
        "en_curso": [r for r in rows if r["state"]["key"] in ("analizando", "cambios")],
        "aprobadas": [r for r in rows if r["state"]["key"] == "aprobada"],
        "descartadas": [r for r in rows if r["state"]["key"] == "descartada"],
        "total": len(rows),
    }


def stage1_blockers(project: Project) -> list[str]:
    """HU activas sin una revisión vigente aprobada: bloquean la aprobación de la etapa 1."""
    return [f"{s.code} ({state(s)['label'].lower()})" for s in sorted(project.stories, key=lambda s: s.id)
            if s.status not in ACTIVE_EXCLUDED and state(s)["key"] not in ("aprobada", "descartada")]


# ============================================================ con base de datos
def build_input(project: Project, story: UserStory, context_text: str | None) -> ReviewInput:
    notes = []
    for review in story.reviews:
        for d in review.decisions:
            if d.comment:
                who = d.decided_by.name if d.decided_by else "equipo"
                notes.append(f"{who} ({DECISIONS[d.decision].lower()}): {d.comment}")
    snap = story_service.snapshot(story)
    snap.update(status=story.status, source_ref=story.source_ref,
                origin="generada por IA" if (story.external_id or "").startswith("gen-") else "escrita por una persona")
    others = [f"{s.code}: {s.title}" for s in sorted(project.stories, key=lambda s: s.id)
              if s.id != story.id and s.status != "descartada"][:60]
    return ReviewInput(project_key=project.key, project_name=project.name, project_description=project.description,
                       story=snap, other_stories=others, context=context_text, human_notes=notes[-20:])


def approved_before(story: UserStory) -> bool:
    if story.status in ("lista", "en_progreso", "en_revision", "hecha"):
        return True
    return any((d := last_decision(r)) is not None and d.decision == "aprobar" for r in story.reviews[:-1])


def save_result(review: StoryReview, story: UserStory, out: ReviewOutput) -> None:
    result = evaluate(out, story_service.snapshot(story), approved_before=approved_before(story),
                      generated=(story.external_id or "").startswith("gen-"))
    review.level, review.confidence, review.quality = result["level"], result["confidence"], result["quality"]
    review.story_type, review.reasons, review.questions = result["story_type"], result["reasons"], result["questions"]
    review.proposal, review.estimation = result["proposal"], result["estimation"]
    review.agents = result["agents"] | {"estimation_error": result["estimation_error"]}
    review.status, review.finished_at = "done", utcnow()


def decide(
    db: Session, project: Project, story: UserStory, *, user: User, role: str | None, decision: str,
    comment: str | None = None, apply_proposal: bool = False, expected_version: int | None = None,
) -> HitlDecision:
    """Registra una decisión humana sobre la revisión vigente y aplica su efecto en la HU.

    `pedir_cambios` solo registra: quien llama encola el refinamiento con el comentario como instrucción.
    """
    if decision not in DECISIONS:
        raise Unprocessable(f"Decisión desconocida: {decision}")
    comment = (comment or "").strip() or None
    review = latest_review(story)
    if review is None or review.status != "done":
        raise Conflict(f"La HU {story.code} no tiene una revisión terminada: revísala con los agentes primero")
    if expected_version is not None and expected_version != story.version:
        raise Conflict(f"La HU {story.code} cambió mientras la revisabas (versión {story.version}); recarga la página")
    st = state(story)
    if not can_decide(role, review.level, decision):
        quien = "product owner" if review.level == 2 else "product owner o scrum master"
        if decision == "comentario":
            raise Forbidden("Solo los miembros del proyecto pueden comentar")
        raise Forbidden(f"Esta revisión es de nivel {review.level} ({LEVEL_LABELS[review.level].lower()}): decide el {quien}")
    if decision != "comentario":
        if st["key"] not in ("pendiente", "falta_info", "aprobada"):
            raise Conflict(f"La HU {story.code} está en «{st['label']}»: no hay una revisión vigente que decidir")
        if st["key"] == "aprobada" and decision == "aprobar":
            raise Conflict(f"La HU {story.code} ya está aprobada")
        if decision in ("pedir_cambios", "descartar", "falta_info") and not comment:
            raise Unprocessable("Escribe el motivo, los cambios que pides o la pregunta para el equipo")
    if decision == "pedir_cambios" and story.status not in ("borrador", "refinada", "en_refinamiento"):
        raise Unprocessable(f"La HU {story.code} está en «{story.status}»: devuélvela a refinada para pedir cambios")

    actor = Actor(kind="user", name=user.name, user_id=user.id, is_admin=user.is_admin, project_role=role)
    applied = False
    from_status = story.status
    if decision == "aprobar":
        if apply_proposal:
            changes = proposal_changes(story, review.proposal)
            if changes:
                story_service.apply_changes(db, project, story, changes, actor)
                applied = True
        if not story.type and review.story_type:
            story.type = review.story_type
            story.type_justification = (review.estimation or {}).get("type_justification")
        # La aprobación humana del HITL es la que autoriza pasar de borrador a refinada.
        if story.status in ("borrador", "en_refinamiento"):
            for req in story.refinements:
                if req.status in ("pending", "in_progress"):
                    req.status = "cancelled"
            story.status = "refinada"
    elif decision == "descartar":
        if story.status in ("en_progreso", "en_revision", "hecha"):
            raise Unprocessable(f"La HU {story.code} ya está en ejecución ({story.status}); no se puede descartar")
        story.status = "descartada"
    if story.status != from_status:
        story.version += 1
    if decision == "aprobar":
        review.applied_version = story.version  # la versión aprobada sigue «vigente» para esta revisión
        review.applied_hash = content_hash(story)

    record = HitlDecision(review=review, story_id=story.id, decision=decision, comment=comment,
                          applied_proposal=applied, level=review.level, role=role, decided_by_id=user.id)
    db.add(record)
    audit.record(db, story, actor, f"hitl_{decision}", from_status if story.status != from_status else None,
                 story.status if story.status != from_status else None,
                 diff={"nivel": review.level, "revision": review.id, "comentario": comment, "aplico_propuesta": applied})
    db.flush()
    return record
