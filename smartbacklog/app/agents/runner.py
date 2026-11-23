"""Orquesta las ejecuciones de agentes y persiste sus resultados con los servicios del dominio.

Reglas que este módulo hace cumplir (no el proveedor):
- La generación solo crea HU en `borrador`, con `external_id` determinista (reprocesar no duplica).
- El refinamiento pasa por el mismo flujo que un ADK externo: `claim` → `submit_result`,
  y termina en `refinada`. Nunca aprueba etapas HITL ni mueve HU a `lista`.
- Cada ejecución queda en `agent_runs` (estado, error, tokens, costo).
- Una falla no deja HU a medias: la generación se hace en una sola transacción.
- El dimensionamiento solo identifica y clasifica funciones (el LLM); los puntos los calcula `services/fpa.py`.
"""

from __future__ import annotations

import hashlib
import logging
import unicodedata
from dataclasses import dataclass
from typing import Callable

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.document_tools import make_document_tools
from app.agents.ports import (
    AgentNotConfigured,
    ContextExtractionInput,
    DocumentGenerationInput,
    GeneratedStory,
    GenerationInput,
    RefinementInput,
    SizingInput,
    Usage,
)
from app.agents.registry import get_provider
from app.config import settings
from app.db import SessionLocal
from app.models import AgentRun, Project, RefinementRequest, StoryReview, UserStory, utcnow
from app.schemas import RefinementResult
from app.services import audit, context_graph, fpa, hitl, nfr
from app.services import context as context_service
from app.services import refinement as refinement_service
from app.services import stories as story_service
from app.services.documents import get_document_source
from app.services.errors import Conflict, NotFound, ServiceUnavailable, Unprocessable
from app.services.workflow import Actor

log = logging.getLogger(__name__)


@dataclass
class ServiceActor:
    """Actor de los agentes: se registra como servicio en el historial."""

    provider_name: str

    def as_actor(self) -> Actor:
        return Actor(kind="service", name=f"agent:{self.provider_name}", project_role="service")


def external_id_for(source_name: str, title: str) -> str:
    digest = hashlib.sha1(f"{source_name}|{title.strip().lower()}".encode()).hexdigest()[:12]
    return f"gen-{digest}"


def _cost(tokens_in: int, tokens_out: int) -> float | None:
    if settings.agent_price_in_per_1m is None or settings.agent_price_out_per_1m is None:
        return None
    return round((tokens_in * settings.agent_price_in_per_1m + tokens_out * settings.agent_price_out_per_1m) / 1_000_000, 6)


def run_out(run: AgentRun) -> dict:
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "provider": run.provider,
        "model": run.model,
        "story_id": run.story_id,
        "input_ref": run.input_ref,
        "input_chars": run.input_chars,
        "result": run.result,
        "error": run.error,
        "tokens_in": run.tokens_in,
        "tokens_out": run.tokens_out,
        "cost_usd": run.cost_usd,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _dispatch(background: BackgroundTasks | None, fn: Callable, *args) -> None:
    """Ejecuta ahora (inline, tests) o en segundo plano (producción). Mover a un worker = cambiar esto."""
    if settings.agent_execution == "inline" or background is None:
        fn(*args)
    else:
        background.add_task(fn, *args)


def _new_run(db: Session, project: Project, kind: str, provider_name: str, model: str | None, **fields) -> AgentRun:
    run = AgentRun(
        project_id=project.id,
        kind=kind,
        status="queued",
        provider=provider_name,
        model=model,
        **fields,
    )
    db.add(run)
    db.commit()
    return run


# ---------- generación de HU desde documentos ----------

def start_generation(
    db: Session,
    project: Project,
    *,
    source_name: str,
    text: str,
    instructions: str | None,
    requested_by_id: int | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    provider_name, model = _provider_identity()
    run = _new_run(
        db, project, "generate_stories", provider_name, model,
        input_ref=source_name, input_chars=len(text), requested_by_id=requested_by_id,
    )
    run_id = run.id
    _dispatch(background, execute_generation, run_id, project.key, source_name, text, instructions)
    db.expire_all()
    return db.get(AgentRun, run_id)


def execute_generation(run_id: int, project_key: str, source_name: str, text: str, instructions: str | None) -> None:
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        project = db.scalar(select(Project).where(Project.key == project_key))
        run.status, run.started_at = "running", utcnow()
        db.commit()
        try:
            provider = get_provider()
            output, usage = provider.generate_stories(
                GenerationInput(
                    project_key=project.key, project_name=project.name,
                    source_name=source_name, text=text, instructions=instructions,
                )
            )
            actor = ServiceActor(provider.name).as_actor()
            created, updated = _persist_stories(db, project, output.stories, actor, source_name)
            run.result = {"created": created, "updated": updated, "total": len(output.stories)}
            run.model, run.tokens_in, run.tokens_out = usage.model, usage.tokens_in, usage.tokens_out
            run.cost_usd = _cost(usage.tokens_in, usage.tokens_out)
            run.status = "succeeded"
            db.commit()
            _review_generated(db, project, created + updated, run.requested_by_id)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo del proveedor queda registrado, sin HU a medias
            db.rollback()
            run = db.get(AgentRun, run_id)
            run.status, run.error = "failed", _error_text(exc)
            db.commit()
            log.warning("Generación %s falló: %s", run_id, exc)
        finally:
            run = db.get(AgentRun, run_id)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


def _persist_stories(
    db: Session, project: Project, items: list[GeneratedStory], actor: Actor, default_source: str,
    *, per_item_source: bool = False,
) -> tuple[list[str], list[str]]:
    """Crea o actualiza las HU generadas (en borrador). La clave de idempotencia es documento + título.

    Con un solo documento, la fuente es siempre su nombre (aunque el modelo llene source_document),
    así reprocesar el mismo archivo sigue sin duplicar.
    """
    created, updated = [], []
    for item in items:
        source = (item.source_document if per_item_source else None) or default_source
        data = item.model_dump(exclude_none=True, exclude={"source_section", "source_document"})
        data["source_ref"] = f"{source} · {item.source_section}" if item.source_section else source
        story, is_new = story_service.upsert_story(
            db, project, data, actor, external_id=external_id_for(source, item.title)
        )
        (created if is_new else updated).append(story.code)
    return created, updated


# ---------- generación de HU desde todos los documentos (agente de documentos como AgentTool) ----------

def start_generation_from_documents(
    db: Session,
    project: Project,
    *,
    instructions: str | None,
    requested_by_id: int | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    provider_name, model = _provider_identity()
    # Falla aquí (antes de crear el run) si la fuente no está configurada o el bucket no responde.
    source = get_document_source(project.key)
    if not source.list():
        raise Unprocessable(f"No hay documentos en {source.location}")
    run = _new_run(
        db, project, "generate_from_docs", provider_name, model,
        input_ref=f"documentos: {source.location}"[:500], requested_by_id=requested_by_id,
    )
    run_id = run.id
    _dispatch(background, execute_generation_from_documents, run_id, project.key, instructions)
    db.expire_all()
    return db.get(AgentRun, run_id)


def execute_generation_from_documents(run_id: int, project_key: str, instructions: str | None) -> None:
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        project = db.scalar(select(Project).where(Project.key == project_key))
        run.status, run.started_at = "running", utcnow()
        db.commit()
        try:
            provider = get_provider()
            tools = make_document_tools(get_document_source(project.key), settings.agent_max_doc_chars)
            output, usage = provider.generate_from_documents(
                DocumentGenerationInput(project_key=project.key, project_name=project.name, instructions=instructions),
                tools,
            )
            actor = ServiceActor(provider.name).as_actor()
            created, updated = _persist_stories(
                db, project, output.stories, actor, "documentos", per_item_source=True
            )
            run.result = {
                "created": created, "updated": updated, "total": len(output.stories),
                "documents_read": tools.documents_read,
            }
            run.input_chars = tools.chars_served
            run.model, run.tokens_in, run.tokens_out = usage.model, usage.tokens_in, usage.tokens_out
            run.cost_usd = _cost(usage.tokens_in, usage.tokens_out)
            run.status = "succeeded"
            db.commit()
            _review_generated(db, project, created + updated, run.requested_by_id)
        except Exception as exc:  # noqa: BLE001 - igual que execute_generation: sin HU a medias
            db.rollback()
            run = db.get(AgentRun, run_id)
            run.status, run.error = "failed", _error_text(exc)
            db.commit()
            log.warning("Generación desde documentos %s falló: %s", run_id, exc)
        finally:
            run = db.get(AgentRun, run_id)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


# ---------- refinamiento de una HU ----------

def start_refinement(
    db: Session,
    project: Project,
    story: UserStory,
    *,
    actor: Actor,
    user_id: int | None,
    instructions: str | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    """Lleva la HU a en_refinamiento (si no lo está) y encola el refinamiento por agente."""
    if story.status != "en_refinamiento":
        req = story_service.transition(db, story, "en_refinamiento", actor, user_id=user_id)
    else:
        req = next((r for r in story.refinements if r.status == "pending"), None)
        if req is None:
            raise Conflict("La HU está en refinamiento sin solicitud pendiente")
    db.commit()
    return enqueue_refinement(db, project, story, req.id, instructions, user_id, background)


def enqueue_refinement(
    db: Session,
    project: Project,
    story: UserStory,
    request_id: int,
    instructions: str | None,
    user_id: int | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    provider_name, model = _provider_identity()
    run = _new_run(
        db, project, "refine_story", provider_name, model,
        story_id=story.id, input_ref=story.code, input_chars=len(story.acceptance_criteria or ""),
        requested_by_id=user_id,
    )
    run_id = run.id
    _dispatch(background, execute_refinement, run_id, request_id, instructions)
    db.expire_all()
    return db.get(AgentRun, run_id)


def execute_refinement(run_id: int, request_id: int, instructions: str | None) -> None:
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        run.status, run.started_at = "running", utcnow()
        db.commit()
        try:
            provider = get_provider()
            req = refinement_service.get_request(db, request_id)
            if req.status != "pending":
                raise Conflict(f"La solicitud {request_id} ya está en estado '{req.status}'")
            claimer = f"agent:{provider.name}"
            refinement_service.claim(db, request_id, claimer)
            req = refinement_service.get_request(db, request_id)
            snapshot = req.input_snapshot

            output, usage = provider.refine_story(RefinementInput(story=snapshot, instructions=instructions))
            result = RefinementResult(
                expected_version=snapshot["version"],
                **output.model_dump(exclude={"notes"}),
                notes=output.notes,
            )
            refinement_service.submit_result(db, request_id, result, claimer)

            run = db.get(AgentRun, run_id)
            run.result = {"request_id": request_id, "story": snapshot["code"], "notes": output.notes}
            run.model, run.tokens_in, run.tokens_out = usage.model, usage.tokens_in, usage.tokens_out
            run.cost_usd = _cost(usage.tokens_in, usage.tokens_out)
            run.status = "succeeded"
            db.commit()
            _review_after_refinement(db, req.story_id, run.requested_by_id)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            req = db.get(RefinementRequest, request_id)
            if req is not None and req.status == "in_progress":
                req.status, req.error = "failed", _error_text(exc)
            run = db.get(AgentRun, run_id)
            run.status, run.error = "failed", _error_text(exc)
            db.commit()
            log.warning("Refinamiento %s falló: %s", run_id, exc)
        finally:
            run = db.get(AgentRun, run_id)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


def auto_refine_if_enabled(db: Session, project: Project, request, user_id: int | None, background: BackgroundTasks | None) -> None:
    """Con AGENT_AUTO_REFINE=true, una HU que entra a refinamiento la toma el agente interno."""
    if not settings.agent_auto_refine or request is None:
        return
    try:
        enqueue_refinement(db, project, request.story, request.id, None, user_id, background)
    except ServiceUnavailable:
        # El cambio de estado ya se guardó; sin proveedor la HU queda pendiente para el flujo manual.
        log.warning("Auto-refinamiento omitido: proveedor de IA no configurado")


# ---------- dimensionamiento con puntos de función (etapa 3) ----------

def _normalize(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name)
    return " ".join("".join(c for c in plain if not unicodedata.combining(c)).lower().split())


def _size_one(
    db: Session, project: Project, story: UserStory, provider, actor: Actor, registry: list[str], instructions: str | None,
    graph: dict | None = None,
) -> tuple[Usage, dict]:
    """Pide al agente los componentes FPA de una HU y los guarda: el servidor calcula los puntos.

    Con un grafo de contexto aprobado, el agente de contexto informa qué restricciones activa o viola la HU;
    el servidor descarta ids que no existan, toma la severidad del grafo y calcula el tamaño no funcional.
    """
    output, usage = provider.size_story(SizingInput(
        story=story_service.snapshot(story), registry=list(registry), instructions=instructions, context_graph=graph,
    ))
    known = {_normalize(n) for n in registry}
    components = output.components()
    for item in components:
        # Regla del servidor, no del modelo: una ILF/EIF ya contada por una HU anterior suma 0.
        if item["type"] in fpa.DATA_TYPES and _normalize(item["name"]) in known:
            item["reused"] = True
    notes = {
        "sized_by": actor.name,
        "concepts": [c.model_dump(exclude_none=True) for c in output.concepts],
        "rationale": output.rationale,
        "registry": list(registry),
    }
    analysis = None
    if graph is not None:
        report = output.context.model_dump() if output.context else None
        analysis = nfr.analyze(graph, graph.get("version"), report, previous=story.context_analysis)
    changes = {
        "fp_components": components, "fp_notes": notes,
        "nfr_points": analysis["nfr_points"] if analysis else None, "context_analysis": analysis,
    }
    diff = story_service.apply_changes(db, project, story, changes, actor)
    if output.rationale:
        audit.record(db, story, actor, "sizing_rationale", diff={"rationale": output.rationale})
    open_ids = [v["constraint_id"] for v in nfr.open_violations(analysis)]
    if open_ids:
        audit.record(db, story, actor, "constraint_violations", diff={"open": open_ids})
    return usage, {
        "story": story.code, "function_points": story.function_points, "nfr_points": story.nfr_points,
        "open_violations": open_ids, "changed": sorted(diff),
    }


def _approved_graph(db: Session, project: Project) -> dict | None:
    graph = context_service.approved(db, project)
    return {"version": graph.version, "nodes": graph.nodes, "edges": graph.edges} if graph else None


def _active_in_order(db: Session, project: Project) -> list[UserStory]:
    """Mismo orden de generación que el modelo FinOps: prioridad (vacías al final) y luego id."""
    return [s for s in story_service.list_stories(db, project) if s.status != "descartada"]


def start_sizing(
    db: Session,
    project: Project,
    story: UserStory,
    *,
    user_id: int | None,
    instructions: str | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    if story.status == "descartada":
        raise Unprocessable("Una HU descartada no se dimensiona")
    provider_name, model = _provider_identity()
    run = _new_run(
        db, project, "size_story", provider_name, model,
        story_id=story.id, input_ref=story.code, input_chars=len(story.acceptance_criteria or ""),
        requested_by_id=user_id,
    )
    run_id = run.id
    _dispatch(background, execute_sizing, run_id, project.key, [story.code], instructions)
    db.expire_all()
    return db.get(AgentRun, run_id)


def start_backlog_sizing(
    db: Session,
    project: Project,
    *,
    resize: bool,
    user_id: int | None,
    instructions: str | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    """Dimensiona en orden todas las HU activas sin puntos (o todas con `resize`)."""
    provider_name, model = _provider_identity()
    codes = [s.code for s in _active_in_order(db, project) if resize or s.function_points is None]
    if not codes:
        raise Unprocessable("No hay HU por dimensionar (usa resize para recalcular todas)")
    run = _new_run(
        db, project, "size_backlog", provider_name, model,
        input_ref=f"{len(codes)} HU: {', '.join(codes)}"[:500], requested_by_id=user_id,
    )
    run_id = run.id
    _dispatch(background, execute_sizing, run_id, project.key, codes, instructions)
    db.expire_all()
    return db.get(AgentRun, run_id)


def execute_sizing(run_id: int, project_key: str, codes: list[str], instructions: str | None) -> None:
    """Recorre las HU activas en orden; dimensiona las de `codes` y hace crecer el registro de entidades.

    Una sola transacción: si una HU falla, no queda ninguna a medias. Nunca cambia estados ni aprueba etapas.
    """
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        run.status, run.started_at = "running", utcnow()
        db.commit()
        try:
            provider = get_provider()
            project = db.scalar(select(Project).where(Project.key == project_key))
            actor = ServiceActor(provider.name).as_actor()
            graph = _approved_graph(db, project)
            # Las entidades que el sistema ya tiene (grafo aprobado) son reuso desde la primera HU.
            registry = context_graph.existing_names(graph, "entity") if graph else []
            targets, sized = set(codes), []
            total = Usage(model=provider.model)
            for story in _active_in_order(db, project):
                if story.code in targets:
                    usage, result = _size_one(db, project, story, provider, actor, registry, instructions, graph)
                    total.model = usage.model or total.model
                    total.tokens_in += usage.tokens_in
                    total.tokens_out += usage.tokens_out
                    sized.append(result)
                registry += [n for n in fpa.data_function_names(story.fp_analysis) if n not in registry]
            missing = sorted(targets - {r["story"] for r in sized})
            if missing:
                raise Unprocessable(f"HU no disponibles para dimensionar: {', '.join(missing)}")
            run = db.get(AgentRun, run_id)
            run.result = {
                "sized": sized,
                "function_points": sum(r["function_points"] or 0 for r in sized),
                "nfr_points": sum(r["nfr_points"] or 0 for r in sized),
                "open_violations": {r["story"]: r["open_violations"] for r in sized if r["open_violations"]},
                "context_graph_version": graph["version"] if graph else None,
                "registry": registry,
            }
            if len(sized) == 1:
                run.result.update(story=sized[0]["story"], points=sized[0]["function_points"])
            run.model, run.tokens_in, run.tokens_out = total.model, total.tokens_in, total.tokens_out
            run.cost_usd = _cost(total.tokens_in, total.tokens_out)
            run.status = "succeeded"
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            run = db.get(AgentRun, run_id)
            run.status, run.error = "failed", _error_text(exc)
            db.commit()
            log.warning("Dimensionamiento %s falló: %s", run_id, exc)
        finally:
            run = db.get(AgentRun, run_id)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


# ---------- grafo de contexto (lo propone el agente, lo aprueba una persona) ----------

def start_context_extraction(
    db: Session,
    project: Project,
    *,
    user_id: int | None,
    instructions: str | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    provider_name, model = _provider_identity()
    source = get_document_source(project.key)
    if not source.list():
        raise Unprocessable(f"No hay documentos en {source.location} para extraer el contexto")
    run = _new_run(
        db, project, "extract_context", provider_name, model,
        input_ref=f"documentos: {source.location}"[:500], requested_by_id=user_id,
    )
    run_id = run.id
    _dispatch(background, execute_context_extraction, run_id, project.key, instructions)
    db.expire_all()
    return db.get(AgentRun, run_id)


def execute_context_extraction(run_id: int, project_key: str, instructions: str | None) -> None:
    """Guarda la propuesta del agente como BORRADOR. Nunca la aprueba: eso lo hace una persona."""
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        project = db.scalar(select(Project).where(Project.key == project_key))
        run.status, run.started_at = "running", utcnow()
        db.commit()
        try:
            provider = get_provider()
            tools = make_document_tools(get_document_source(project.key), settings.agent_max_doc_chars)
            output, usage = provider.extract_context(
                ContextExtractionInput(project_key=project.key, project_name=project.name, instructions=instructions),
                tools,
            )
            nodes, edges = output.as_graph()
            graph = context_service.create_draft(
                db, project, nodes, edges, created_by=f"agent:{provider.name}", sources=tools.documents_read,
                notes=output.notes, agent_run_id=run_id,
            )
            run = db.get(AgentRun, run_id)
            run.result = {
                "version": graph.version, "status": graph.status, "documents_read": tools.documents_read,
                "summary": context_graph.summary({"nodes": graph.nodes, "edges": graph.edges}),
            }
            run.input_chars = tools.chars_served
            run.model, run.tokens_in, run.tokens_out = usage.model, usage.tokens_in, usage.tokens_out
            run.cost_usd = _cost(usage.tokens_in, usage.tokens_out)
            run.status = "succeeded"
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            run = db.get(AgentRun, run_id)
            run.status, run.error = "failed", _error_text(exc)
            db.commit()
            log.warning("Extracción de contexto %s falló: %s", run_id, exc)
        finally:
            run = db.get(AgentRun, run_id)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


# ---------- revisión multiagente y HITL ----------

def _needs_review(story: UserStory) -> bool:
    return story.status != "descartada" and hitl.state(story)["key"] in hitl.NEEDS_AGENT


def start_review(
    db: Session, project: Project, stories: list[UserStory], *, user_id: int | None, background: BackgroundTasks | None,
) -> AgentRun:
    """Encola la revisión multiagente de una o varias HU (una StoryReview por HU, una ejecución en total)."""
    if not stories:
        raise Unprocessable("No hay HU que revisar: todas tienen una revisión vigente")
    busy = [s.code for s in stories if hitl.state(s)["key"] == "analizando"]
    if busy:
        raise Conflict(f"Ya se están revisando: {', '.join(busy)}")
    provider_name, model = _provider_identity()
    run = _new_run(
        db, project, "review_story" if len(stories) == 1 else "review_backlog", provider_name, model,
        story_id=stories[0].id if len(stories) == 1 else None,
        input_ref=", ".join(s.code for s in stories)[:500], requested_by_id=user_id,
    )
    review_ids = []
    for story in stories:
        review = StoryReview(project_id=project.id, story_id=story.id, story_version=story.version,
                             content_hash=hitl.content_hash(story), agent_run_id=run.id, status="queued", reasons=[], questions=[])
        db.add(review)
        db.flush()
        review_ids.append(review.id)
    db.commit()
    run_id = run.id
    _dispatch(background, execute_reviews, run_id, project.key, review_ids)
    db.expire_all()
    return db.get(AgentRun, run_id)


def start_pending_reviews(db: Session, project: Project, *, user_id: int | None, background: BackgroundTasks | None) -> AgentRun:
    return start_review(db, project, [s for s in _active_in_order(db, project) if _needs_review(s)],
                        user_id=user_id, background=background)


def execute_reviews(run_id: int, project_key: str, review_ids: list[int]) -> None:
    """Corre los cuatro agentes por HU y guarda el nivel HITL. Una HU que falla no detiene a las demás."""
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        run.status, run.started_at = "running", utcnow()
        db.commit()
        done, failed = [], []
        try:
            provider = get_provider()
            project = run.project
            graph = _approved_graph(db, project)
            context_text = context_graph.render(graph) if graph else None
            for review_id in review_ids:
                review = db.get(StoryReview, review_id)
                story = review.story
                review.status = "running"
                db.commit()
                try:
                    if not hitl.is_current(review, story):
                        raise Conflict(f"La HU {story.code} cambió antes de revisarse (versión {story.version})")
                    out, usage = provider.review_story(hitl.build_input(project, story, context_text))
                    hitl.save_result(review, story, out)
                    run.tokens_in += usage.tokens_in
                    run.tokens_out += usage.tokens_out
                    run.model = usage.model or run.model
                    audit.record(db, story, ServiceActor(provider.name).as_actor(), "hitl_reviewed",
                                 diff={"revision": review.id, "nivel": review.level, "confianza": review.confidence})
                    db.commit()
                    done.append({"story": story.code, "level": review.level})
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    review = db.get(StoryReview, review_id)
                    review.status, review.error, review.finished_at = "failed", _error_text(exc), utcnow()
                    db.commit()
                    failed.append({"story": review.story.code, "error": _error_text(exc)})
                    log.warning("Revisión de %s falló: %s", review.story.code, exc)
            run = db.get(AgentRun, run_id)
            run.cost_usd = _cost(run.tokens_in, run.tokens_out)
            run.result = {"reviewed": done, "failed": failed}
            run.status = "succeeded" if done or not failed else "failed"
            run.error = None if run.status == "succeeded" else "; ".join(f["error"] for f in failed)[:2000]
            db.commit()
        except Exception as exc:  # noqa: BLE001 - p. ej. proveedor no configurado
            db.rollback()
            for review_id in review_ids:
                review = db.get(StoryReview, review_id)
                if review.status in ("queued", "running"):
                    review.status, review.error, review.finished_at = "failed", _error_text(exc), utcnow()
            run = db.get(AgentRun, run_id)
            run.status, run.error = "failed", _error_text(exc)
            db.commit()
        finally:
            run = db.get(AgentRun, run_id)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


def _review_generated(db: Session, project: Project, codes: list[str], user_id: int | None) -> None:
    """Con HITL_REVIEW_GENERATED=true, las HU que crea o cambia el generador pasan de una vez por la revisión."""
    if not settings.hitl_review_generated or not codes:
        return
    stories = [s for s in project.stories if s.code in codes and _needs_review(s)]
    try:
        if stories:
            start_review(db, project, stories, user_id=user_id, background=None)
    except Exception as exc:  # noqa: BLE001 - la generación ya quedó guardada
        db.rollback()
        log.warning("No se pudieron revisar las HU generadas: %s", exc)


def _review_after_refinement(db: Session, story_id: int, user_id: int | None) -> None:
    """Tras un «pedir cambios» del HITL, la HU refinada vuelve a revisión con los agentes (en el mismo hilo)."""
    if not settings.hitl_review_after_refine:
        return
    story = db.get(UserStory, story_id)
    last = hitl.last_decision(hitl.latest_review(story))
    if last is None or last.decision != "pedir_cambios":
        return
    try:
        start_review(db, story.project, [story], user_id=user_id, background=None)
    except Exception as exc:  # noqa: BLE001 - la HU queda refinada; la revisión se puede lanzar a mano
        db.rollback()
        log.warning("No se pudo revisar %s después de refinarla: %s", story.code, exc)


def request_changes(
    db: Session, project: Project, story: UserStory, *, user, role: str | None, comment: str,
    expected_version: int | None, background: BackgroundTasks | None,
) -> AgentRun:
    """Decisión HITL «pedir cambios»: se registra y el agente refina la HU con el comentario como instrucción."""
    _provider_identity()  # sin agente configurado, 503 antes de registrar nada
    hitl.decide(db, project, story, user=user, role=role, decision="pedir_cambios", comment=comment,
                expected_version=expected_version)
    actor = Actor(kind="user", name=user.name, user_id=user.id, is_admin=user.is_admin, project_role=role)
    return start_refinement(db, project, story, actor=actor, user_id=user.id, instructions=comment, background=background)


# ---------- helpers ----------

def _provider_identity() -> tuple[str, str | None]:
    """Nombre y modelo sin llamar al proveedor. Sin proveedor configurado responde 503 antes de crear el run."""
    try:
        provider = get_provider()
    except AgentNotConfigured as exc:
        raise ServiceUnavailable(str(exc)) from exc
    return provider.name, provider.model


def _error_text(exc: Exception) -> str:
    text = f"{exc.__class__.__name__}: {exc}" if not isinstance(exc, (Conflict, NotFound)) else str(exc)
    return text[:2000]


__all__ = [
    "AgentNotConfigured",
    "auto_refine_if_enabled",
    "execute_context_extraction",
    "execute_sizing",
    "execute_generation",
    "execute_generation_from_documents",
    "execute_refinement",
    "execute_reviews",
    "request_changes",
    "start_pending_reviews",
    "start_review",
    "external_id_for",
    "run_out",
    "start_backlog_sizing",
    "start_context_extraction",
    "start_sizing",
    "start_generation",
    "start_generation_from_documents",
    "start_refinement",
]
