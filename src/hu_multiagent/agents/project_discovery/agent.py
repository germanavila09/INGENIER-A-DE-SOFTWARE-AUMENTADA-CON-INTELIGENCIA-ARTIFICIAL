"""PROJECT_DISCOVERY_AGENT: genera el ProjectManifest de un proyecto (determinista)."""

from __future__ import annotations

import time
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai import types
from pydantic import PrivateAttr

from ...models.common import stable_hash
from ...models.project import ProjectManifest
from ...services.audit_service import AuditEntry
from ...tools.project_tools import build_manifest
from .prompt import DESCRIPTION

MANIFEST_FILE = "generated/manifest.json"


class ProjectDiscoveryAgent(BaseAgent):
    _engine: Any = PrivateAttr(default=None)

    def bind(self, engine) -> "ProjectDiscoveryAgent":
        self._engine = engine
        return self

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        eng = self._engine
        st = ctx.session.state
        root, pid, run_id = st["project_root"], st["project_id"], st.get("run_id", "")
        t0 = time.monotonic()

        store = eng.results_store(pid)
        prev_data = store.read_json(MANIFEST_FILE)
        previous = ProjectManifest.model_validate(prev_data) if prev_data else None
        manifest = build_manifest(eng.input, root, previous)
        if manifest.project_id != pid:
            raise ValueError(f"La carpeta {root} declara project_id {manifest.project_id}, no {pid}.")
        store.write_json(MANIFEST_FILE, manifest)

        eng.audit.record(AuditEntry(
            event="agent_output", run_id=run_id, project_id=pid, agent=self.name,
            input_hash=stable_hash({"root": root, "previous": previous.version if previous else 0}),
            output_hash=stable_hash(manifest), confidence=1.0,
            latency_ms=int((time.monotonic() - t0) * 1000),
            decision=f"manifest v{manifest.version}: {len(manifest.documents)} documentos",
            details={"new": manifest.new_documents, "modified": manifest.modified_documents,
                     "deleted": manifest.deleted_documents},
        ))
        texto = (f"Manifest {pid} v{manifest.version}: {len(manifest.documents)} documentos, "
                 f"{len(manifest.user_stories)} de historias, {len(manifest.modified_documents)} modificados, "
                 f"{len(manifest.new_documents)} nuevos.")
        yield Event(
            author=self.name, invocation_id=ctx.invocation_id, branch=ctx.branch,
            content=types.Content(role="model", parts=[types.Part.from_text(text=texto)]),
            actions=EventActions(state_delta={"manifest": manifest.model_dump(mode="json")}),
        )


def build_project_discovery(engine) -> ProjectDiscoveryAgent:
    return ProjectDiscoveryAgent(name="project_discovery_agent", description=DESCRIPTION).bind(engine)
