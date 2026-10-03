"""Auditoría append-only y logs estructurados.

Cada entrada queda en projects/<project_id>/audit/audit_log.jsonl y también se
emite como JSON por el logger 'hu_multiagent.audit' (Cloud Logging lo interpreta
como jsonPayload cuando corre en Cloud Run). Permite reconstruir:
«¿por qué esta historia terminó con esta recomendación?».
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from pydantic import BaseModel, Field

from ..models.common import now_iso
from .storage_service import ProjectStore, make_backend

AUDIT_FILE = "audit/audit_log.jsonl"

logger = logging.getLogger("hu_multiagent.audit")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class AuditEntry(BaseModel):
    event: str = Field(description="agent_output, state_transition, hitl_request, human_decision, proposal, error…")
    run_id: str = ""
    project_id: str
    story_id: str = ""
    agent: str = ""
    timestamp: str = Field(default_factory=now_iso)
    input_hash: str = ""
    output_hash: str = ""
    decision: str = ""
    confidence: float | None = None
    hitl: int | None = None
    latency_ms: int | None = None
    tokens: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    # Para modificaciones propuestas (sección 19)
    before: str = ""
    after: str = ""
    reason: str = ""
    source: str = ""
    human_approval: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class AuditService:
    def __init__(self, uri: str, backend=None, emit_logs: bool = True) -> None:
        self._backend = backend or make_backend(uri)
        self.emit_logs = emit_logs

    def record(self, entry: AuditEntry) -> AuditEntry:
        ProjectStore(self._backend, entry.project_id).append_jsonl(AUDIT_FILE, entry)
        if self.emit_logs:
            payload = entry.model_dump(mode="json", exclude_defaults=True)
            payload["severity"] = "ERROR" if entry.errors else "INFO"
            payload["message"] = f"[{entry.event}] {entry.project_id} {entry.story_id} {entry.agent} {entry.decision}".strip()
            logger.info(json.dumps(payload, ensure_ascii=False, default=str))
        return entry

    def entries(self, project_id: str, story_id: str | None = None) -> list[AuditEntry]:
        rows = ProjectStore(self._backend, project_id).read_jsonl(AUDIT_FILE)
        out = [AuditEntry.model_validate(r) for r in rows]
        return [e for e in out if story_id is None or e.story_id == story_id]
