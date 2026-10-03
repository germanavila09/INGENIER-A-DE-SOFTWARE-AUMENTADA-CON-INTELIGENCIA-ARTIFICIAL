"""Modelos de proyecto: ProjectManifest, NormalizedDocument y ProjectContext."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .common import Question, Statement


class DocumentCategory(str, Enum):
    PROJECT_FILE = "project_file"      # project.yaml
    README = "readme"
    REQUIREMENTS = "requirements"
    USER_STORIES = "user_stories"
    EPICS = "epics"
    ACCEPTANCE_CRITERIA = "acceptance_criteria"
    FUNCTIONAL = "functional"
    TECHNICAL = "technical"
    ARCHITECTURE = "architecture"
    BACKLOG = "backlog"
    DECISIONS = "decisions"
    DEPENDENCIES = "dependencies"
    TESTS = "tests"
    OTHER = "other"


class ProjectInfo(BaseModel):
    """Contenido de project.yaml (todos los campos son opcionales salvo el id)."""

    project_id: str
    project_name: str = ""
    status: str = ""
    owner: str = ""
    version: str = ""
    description: str = ""
    from_project_yaml: bool = False


class DocumentRef(BaseModel):
    """Entrada del manifest: un archivo del proyecto, sin leer su contenido."""

    path: str = Field(description="Ruta relativa a la raíz del proyecto.")
    uri: str = Field(description="URI completa (gs://... o ruta local).")
    category: DocumentCategory
    format: str
    size: int = 0
    signature: str = Field("", description="Versión del objeto (generación GCS o mtime local).")
    updated: str | None = None


class ProjectManifest(BaseModel):
    project_id: str
    project_name: str
    bucket: str
    root_path: str
    documents: list[DocumentRef] = Field(default_factory=list)
    user_stories: list[str] = Field(default_factory=list)
    architecture_documents: list[str] = Field(default_factory=list)
    technical_documents: list[str] = Field(default_factory=list)
    new_documents: list[str] = Field(default_factory=list)
    modified_documents: list[str] = Field(default_factory=list)
    deleted_documents: list[str] = Field(default_factory=list)
    last_scan: str = ""
    version: int = 1
    info: ProjectInfo


class NormalizedDocument(BaseModel):
    """Documento leído y normalizado por el agente de ingesta."""

    path: str
    uri: str
    category: DocumentCategory
    format: str
    title: str = ""
    text: str = ""
    sections: int = 0
    identifiers: list[str] = Field(default_factory=list, description="IDs que el documento define (US-001, RF-02…).")
    references: list[str] = Field(default_factory=list, description="IDs que menciona sin definirlos.")
    dates: list[str] = Field(default_factory=list)
    version: str = ""
    authors: list[str] = Field(default_factory=list)
    structured: dict | list | None = Field(None, description="Contenido parseado de JSON/YAML.")
    warnings: list[str] = Field(default_factory=list)
    signature: str = ""


class ProjectContext(BaseModel):
    """Conocimiento consolidado del proyecto. Solo hechos con fuente; lo que falta va a open_questions."""

    project_id: str
    project_name: str
    business_context: list[Statement] = Field(default_factory=list)
    technical_context: list[Statement] = Field(default_factory=list)
    architecture: list[Statement] = Field(default_factory=list)
    epics: list[Statement] = Field(default_factory=list)
    user_stories: list[str] = Field(default_factory=list)
    dependencies: list[Statement] = Field(default_factory=list)
    business_rules: list[Statement] = Field(default_factory=list)
    technical_constraints: list[Statement] = Field(default_factory=list)
    known_decisions: list[Statement] = Field(default_factory=list)
    open_questions: list[Question] = Field(default_factory=list)
    risks: list[Statement] = Field(default_factory=list)
    source_documents: list[str] = Field(default_factory=list)
    manifest_version: int = 1
