"""DOCUMENT_INGESTION_AGENT: normaliza documentos, descubre historias y arma el contexto."""

from __future__ import annotations

import time
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai import types
from pydantic import PrivateAttr

from ...models.common import Statement, StatementType, stable_hash
from ...models.project import DocumentCategory, ProjectManifest
from ...models.state import WorkflowState
from ...services.audit_service import AuditEntry
from ...tools.project_tools import STORY_CATEGORIES, build_context, normalize_document
from ...tools.story_tools import discover_stories
from .prompt import DESCRIPTION


class DocumentIngestionAgent(BaseAgent):
    _engine: Any = PrivateAttr(default=None)

    def bind(self, engine) -> "DocumentIngestionAgent":
        self._engine = engine
        return self

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        eng = self._engine
        st = ctx.session.state
        pid, root, run_id = st["project_id"], st["project_root"], st.get("run_id", "")
        manifest = ProjectManifest.model_validate(st["manifest"])
        t0 = time.monotonic()

        docs = [normalize_document(eng.input, root, ref) for ref in manifest.documents
                if ref.category != DocumentCategory.PROJECT_FILE]
        stories, avisos = discover_stories(docs, pid, STORY_CATEGORIES)

        state = eng.state.load(pid)
        eng.state.transition_project(state, WorkflowState.CONTEXT_BUILDING, actor=self.name,
                                     reason=f"{len(docs)} documentos normalizados", run_id=run_id)
        context = build_context(manifest, docs, stories)
        context.risks += [Statement(statement=a, type=StatementType.FACT, source=manifest.root_path) for a in avisos]

        store = eng.results_store(pid)
        store.write_json("generated/project_context.json", context)
        store.write_json("generated/documents/normalized.json", [d.model_dump(mode="json") for d in docs])

        eng.audit.record(AuditEntry(
            event="agent_output", run_id=run_id, project_id=pid, agent=self.name,
            input_hash=stable_hash(manifest), output_hash=stable_hash(context), confidence=1.0,
            latency_ms=int((time.monotonic() - t0) * 1000),
            decision=f"{len(docs)} documentos, {len(stories)} historias, {len(context.open_questions)} preguntas abiertas",
            errors=[f"{d.path}: {w}" for d in docs for w in d.warnings],
        ))
        texto = (f"Ingesta {pid}: {len(docs)} documentos normalizados, {len(stories)} historias "
                 f"({', '.join(s.story_id for s in stories)}), {len(context.open_questions)} preguntas abiertas.")
        yield Event(
            author=self.name, invocation_id=ctx.invocation_id, branch=ctx.branch,
            content=types.Content(role="model", parts=[types.Part.from_text(text=texto)]),
            actions=EventActions(state_delta={
                "context": context.model_dump(mode="json"),
                "stories": [s.model_dump(mode="json") for s in stories],
                "normalized_docs": [d.model_dump(mode="json") for d in docs],
            }),
        )


def build_document_ingestion(engine) -> DocumentIngestionAgent:
    return DocumentIngestionAgent(name="document_ingestion_agent", description=DESCRIPTION).bind(engine)
