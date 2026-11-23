"""Endpoints de agentes IA: generar HU desde documentos, refinar y dimensionar HU (FPA). Ver app/agents."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.registry import provider_status
from app.agents.runner import (
    run_out,
    start_backlog_sizing,
    start_context_extraction,
    start_generation,
    start_generation_from_documents,
    start_refinement,
    start_sizing,
)
from app.config import settings
from app.db import get_db
from app.deps import get_principal
from app.models import AgentRun
from app.services import stories as story_service
from app.services.access import Principal, actor_for, load_project, require_member
from app.services.documents import get_document_source, load_document_text
from app.services.documents.folder import MAX_FILE_BYTES
from app.services.errors import NotFound, PayloadTooLarge, Unprocessable

router = APIRouter(prefix="/api/v1", tags=["agentes"])


class GenerateIn(BaseModel):
    filename: str | None = Field(None, description="Archivo de la fuente configurada (AGENT_DOCS_SOURCE): carpeta documents/<KEY>/ o gs://bucket/prefijo/<KEY>/")
    document_text: str | None = Field(None, description="Texto pegado, alternativa a filename")
    source_name: str | None = Field(None, max_length=255, description="Nombre de la fuente para la trazabilidad")
    instructions: str | None = Field(None, max_length=2000)


class GenerateFromDocumentsIn(BaseModel):
    instructions: str | None = Field(None, max_length=2000)


class RefineIn(BaseModel):
    instructions: str | None = Field(None, max_length=2000)


class ExtractContextIn(BaseModel):
    instructions: str | None = Field(None, max_length=2000)


class SizeIn(BaseModel):
    instructions: str | None = Field(None, max_length=2000)


class SizeBacklogIn(BaseModel):
    resize: bool = Field(False, description="true: recalcula también las HU que ya tienen puntos de función")
    instructions: str | None = Field(None, max_length=2000)


def _write_user_id(principal: Principal) -> int | None:
    return principal.user.id if principal.user else None


def _text_from_source(project_key: str, filename: str) -> tuple[str, str]:
    data = get_document_source(project_key).read_bytes(filename)
    return load_document_text(filename, data, settings.agent_max_doc_chars), filename


@router.get("/agents/status")
def agents_status(principal: Principal = Depends(get_principal)):
    return provider_status()


@router.get("/projects/{key}/agents/documents")
def list_documents(key: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal)
    source = get_document_source(project.key)
    return {
        "source": source.kind,
        "folder": source.location,
        "documents": [{"name": d.name, "size": d.size} for d in source.list()],
    }


@router.post("/projects/{key}/agents/generate-stories", status_code=202)
def generate_stories(
    key: str,
    body: GenerateIn,
    background: BackgroundTasks,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    if bool(body.filename) == bool(body.document_text):
        raise Unprocessable("Envía exactamente uno: filename (documento del proyecto) o document_text")
    if body.filename:
        text, source = _text_from_source(project.key, body.filename)
    else:
        if len(body.document_text) > settings.agent_max_doc_chars:
            raise PayloadTooLarge(f"El texto supera {settings.agent_max_doc_chars} caracteres")
        text, source = body.document_text.strip(), body.source_name or "texto pegado"
    run = start_generation(
        db, project, source_name=source, text=text, instructions=body.instructions,
        requested_by_id=_write_user_id(principal), background=background,
    )
    return run_out(run)


@router.post("/projects/{key}/agents/generate-stories/upload", status_code=202)
async def generate_from_upload(
    key: str,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    instructions: str | None = Form(None),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise PayloadTooLarge("El archivo supera 20 MB")
    text = load_document_text(file.filename or "", data, settings.agent_max_doc_chars)
    run = start_generation(
        db, project, source_name=file.filename or "archivo subido", text=text, instructions=instructions,
        requested_by_id=_write_user_id(principal), background=background,
    )
    return run_out(run)


@router.post("/projects/{key}/agents/generate-stories/from-documents", status_code=202)
def generate_from_documents(
    key: str,
    body: GenerateFromDocumentsIn,
    background: BackgroundTasks,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """El generador consulta al agente de documentos (AgentTool), que lista y lee la carpeta o el bucket."""
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    run = start_generation_from_documents(
        db, project, instructions=body.instructions,
        requested_by_id=_write_user_id(principal), background=background,
    )
    return run_out(run)


@router.post("/projects/{key}/stories/{code}/agents/refine", status_code=202)
def refine_story(
    key: str,
    code: str,
    body: RefineIn,
    background: BackgroundTasks,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    project = load_project(db, key)
    role = require_member(db, project, principal, write=True)
    story = story_service.get_story(db, project, code)
    run = start_refinement(
        db, project, story, actor=actor_for(principal, role), user_id=_write_user_id(principal),
        instructions=body.instructions, background=background,
    )
    return run_out(run)


@router.post("/projects/{key}/agents/extract-context", status_code=202)
def extract_context(
    key: str,
    body: ExtractContextIn,
    background: BackgroundTasks,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """El arquitecto (agente) lee los documentos y propone el grafo de contexto como BORRADOR.

    Una persona lo revisa y lo aprueba en `/projects/{key}/context/draft/approve`; hasta entonces no se usa.
    """
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    run = start_context_extraction(
        db, project, user_id=_write_user_id(principal), instructions=body.instructions, background=background,
    )
    return run_out(run)


@router.post("/projects/{key}/stories/{code}/agents/size", status_code=202)
def size_story(
    key: str,
    code: str,
    body: SizeIn,
    background: BackgroundTasks,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """Puntos de función de una HU (agentes concepto → clasificación). No cambia el estado ni aprueba la etapa 3."""
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    story = story_service.get_story(db, project, code)
    run = start_sizing(
        db, project, story, user_id=_write_user_id(principal),
        instructions=body.instructions, background=background,
    )
    return run_out(run)


@router.post("/projects/{key}/agents/size-backlog", status_code=202)
def size_backlog(
    key: str,
    body: SizeBacklogIn,
    background: BackgroundTasks,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """Dimensiona en orden de generación todas las HU activas sin puntos (o todas con `resize`)."""
    project = load_project(db, key)
    require_member(db, project, principal, write=True)
    run = start_backlog_sizing(
        db, project, resize=body.resize, user_id=_write_user_id(principal),
        instructions=body.instructions, background=background,
    )
    return run_out(run)


@router.get("/projects/{key}/agents/runs")
def list_runs(key: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    project = load_project(db, key)
    require_member(db, project, principal)
    runs = db.scalars(
        select(AgentRun).where(AgentRun.project_id == project.id).order_by(AgentRun.id.desc()).limit(100)
    )
    return [run_out(r) for r in runs]


@router.get("/agents/runs/{run_id}")
def get_run(run_id: int, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    run = db.get(AgentRun, run_id)
    if run is None:
        raise NotFound("Ejecución no encontrada")
    require_member(db, run.project, principal)
    return run_out(run)
