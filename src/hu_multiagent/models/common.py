"""Tipos compartidos por todos los modelos.

Regla anti-alucinación: toda afirmación declara su tipo (FACT, INFERENCE,
RECOMMENDATION, UNKNOWN) y su fuente. Un UNKNOWN nunca se convierte en FACT.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


class StatementType(str, Enum):
    FACT = "FACT"                      # está escrito en un documento fuente
    INFERENCE = "INFERENCE"            # se deduce de documentos, no está literal
    RECOMMENDATION = "RECOMMENDATION"  # propuesta del agente
    UNKNOWN = "UNKNOWN"                # no hay información; nunca se rellena


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Statement(BaseModel):
    statement: str
    type: StatementType
    source: str = Field("", description="URI o ruta del documento fuente; vacío si no hay fuente.")


class Finding(BaseModel):
    category: str = Field(description="Ej.: ambiguity, missing_information, risk, conflict, duplicate.")
    description: str
    severity: Severity
    type: StatementType
    sources: list[str] = Field(default_factory=list)


class Question(BaseModel):
    question: str
    blocking: bool = Field(description="True si impide implementar la historia sin respuesta humana.")
    reason: str = ""
    sources: list[str] = Field(default_factory=list)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_hash(obj: Any) -> str:
    """SHA-256 corto y estable de cualquier objeto serializable (para auditoría)."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    data = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(
        obj, sort_keys=True, ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def validate_project_id(project_id: str) -> str:
    if not project_id or not PROJECT_ID_RE.match(project_id):
        raise ValueError(f"project_id inválido: {project_id!r}")
    return project_id
