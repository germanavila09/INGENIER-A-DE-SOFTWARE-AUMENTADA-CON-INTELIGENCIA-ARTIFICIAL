"""Motor de orquestación: el control del flujo vive en código, no en el LLM.

El ORCHESTRATOR_AGENT (conversacional) llama a estos métodos a través de sus
herramientas. Aquí se aplican la máquina de estados, los límites de iteración, los
reintentos, la pausa HITL y la reanudación, el versionado y la auditoría.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from ..agents.common import K_AGG, K_ANALYSIS, K_ARCH, K_CONTEXT, K_FEEDBACK, K_HITL, K_PROJECT_ID, K_QA, K_RUN_ID, K_STORY
from ..config import HuSettings, get_hu_settings
from ..models.agent_response import AggregatedReview
from ..models.common import Severity, now_iso, stable_hash
from ..models.hitl import (
    DecisionRecord,
    HitlDecisionRequest,
    HitlEvaluation,
    HitlLevel,
    HumanDecision,
    HumanDecisionType,
)
from ..models.project import NormalizedDocument, ProjectContext, ProjectManifest
from ..models.state import STORY_IN_PROGRESS, STORY_TERMINAL, ProjectState, RunSummary, StoryRecord, WorkflowState
from ..models.user_story import ArchitectureReviewOutput, StoryAnalysisOutput, StoryGenerationOutput, UserStory
from ..services.audit_service import AuditEntry, AuditService
from ..services.state_service import StateService
from ..services.storage_service import InputStorage, ProjectStore, make_backend
from ..tools.gcs_tools import list_project_roots, read_project_file, root_folder_uri, root_label, scan_project_files, split_root
from ..tools.project_tools import context_for_prompt, parse_project_info
from ..agents.story_generator.agent import build_story_generator
from ..agents.story_generator.prompt import K_GEN_CONTEXT, K_GEN_LIMIT, K_GEN_OUT, K_PREVIOUS
from .project_workflow import build_project_workflow
from .report import build_reports, dependency_order
from .story_workflow import PARALLEL_AGENTS, build_story_workflow

S = WorkflowState
GENERATION_FILE = "generated/story_generation.json"
LLM_AGENTS = ("story_analyst_agent",) + PARALLEL_AGENTS


class ProjectNotFound(Exception):
    pass


class RecoverableError(Exception):
    """Error transitorio: se reintenta (cuota, red, JSON inválido del modelo)."""


class FatalError(Exception):
    """Error no recuperable: permisos, credenciales, modelo inexistente, datos de otro proyecto."""


FATAL_MARKERS = ("PERMISSION_DENIED", "403", "401", "UNAUTHENTICATED", "Forbidden", "DefaultCredentialsError",
                 "BLOQUEADO", "InvalidTransition", "404 NOT_FOUND", "was not found")


def classify_error(exc: Exception) -> str:
    if isinstance(exc, FatalError):
        return "fatal"
    if isinstance(exc, RecoverableError):
        return "recoverable"
    txt = f"{type(exc).__name__}: {exc}"
    return "fatal" if any(m in txt for m in FATAL_MARKERS) else "recoverable"


def new_run_id() -> str:
    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"


class HuEngine:
    retry_backoff_s: float = 2.0

    def __init__(self, settings: HuSettings | None = None, *, model=None, input_storage: InputStorage | None = None,
                 results_backend=None, gcs_client=None) -> None:
        self.settings = settings or get_hu_settings()
        self.input = input_storage or InputStorage(self.settings.input_uri, client=gcs_client)
        self._results = results_backend or make_backend(self.settings.results_uri)
        state_backend = self._results if not self.settings.state_uri else make_backend(self.settings.state_uri)
        self.state = StateService(self.settings.effective_state_uri, backend=state_backend)
        self.audit = AuditService(self.settings.results_uri, backend=self._results)
        self.model = model or self.settings.model
        self.project_workflow = build_project_workflow(self)
        self.story_workflow = build_story_workflow(self.model, self.settings.confidence_auto, self.settings.confidence_review,
                                                   self.settings.generated_requires_approval)
        self.story_generator = build_story_generator(self.model)
        self._sessions = InMemorySessionService()
        self._project_runner = Runner(agent=self.project_workflow, app_name="hu_project_workflow", session_service=self._sessions)
        self._story_runner = Runner(agent=self.story_workflow, app_name="hu_story_workflow", session_service=self._sessions)
        self._generation_runner = Runner(agent=self.story_generator, app_name="hu_story_generation", session_service=self._sessions)
        self._roots: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # ================================================================ utilidades
    def results_store(self, project_id: str) -> ProjectStore:
        return ProjectStore(self._results, project_id)

    def _known_project_ids(self) -> list[str]:
        ids = set(self._roots)
        for key in self._results.list("projects/"):
            parts = key.split("/")
            if len(parts) >= 4 and parts[2] == "state":
                ids.add(parts[1])
        return sorted(ids)

    def _match(self, project_id: str) -> tuple[str, str] | None:
        norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())  # noqa: E731
        q = norm(project_id)
        if not q:
            return None
        for pid, root in self._roots.items():
            if q in (norm(pid), norm(root_label(root))):
                return pid, root
        parciales = [(pid, root) for pid, root in self._roots.items() if q in norm(pid) or q in norm(root_label(root))]
        return parciales[0] if len(parciales) == 1 else None

    def _resolve(self, project_id: str) -> tuple[str, str]:
        found = self._match(project_id) if self._roots else None
        if not found:
            self.discover()
            found = self._match(project_id)
        if found:
            return found
        raise ProjectNotFound(f"No encontré el proyecto {project_id!r} en {self.input.description}. "
                              f"Disponibles: {', '.join(self._roots) or 'ninguno'}")

    # =============================================================== descubrir
    def discover(self) -> list[dict]:
        roots = list_project_roots(self.input)
        out, vistos = [], {}
        self._roots = {}
        for root in roots:
            yml = None
            if not split_root(root)[1]:
                try:
                    yml = read_project_file(self.input, root, "project.yaml")
                except Exception:
                    yml = None
            info = parse_project_info(root, yml)
            if info.project_id in vistos:
                out.append({"ubicacion": root, "error": f"project_id {info.project_id} repetido (ya lo usa {vistos[info.project_id]})"})
                continue
            vistos[info.project_id] = root
            self._roots[info.project_id] = root
            nuevo = not self.state.exists(info.project_id)
            st = self.state.load(info.project_id)
            st.project_name = info.project_name
            st.root_path = root_folder_uri(self.input, root)
            self.state.save(st)
            if nuevo:
                self.audit.record(AuditEntry(event="project_discovered", project_id=info.project_id,
                                             agent="project_discovery_agent", source=st.root_path))
            out.append({
                "project_id": info.project_id, "project_name": info.project_name, "ubicacion": root,
                "organizacion": "archivos sueltos agrupados por nombre" if split_root(root)[1] else "carpeta",
                "archivos": len(scan_project_files(self.input, root)),
                "status_declarado": info.status, "owner": info.owner, "version": info.version,
                "tiene_project_yaml": info.from_project_yaml, "estado_flujo": st.state.value,
                "historias_registradas": len(st.stories), "decisiones_pendientes": len(st.pending_decisions()),
            })
        return out

    # ========================================================= analizar proyecto
    async def analyze_project(self, project_id: str, story_ids: list[str] | None = None, force: bool = False,
                              trigger: str = "usuario", regenerate: bool = False) -> dict:
        async with self._lock:
            return await self._analyze_project(project_id, story_ids, force, trigger, regenerate)

    async def _analyze_project(self, project_id, story_ids, force, trigger, regenerate=False) -> dict:
        pid, root = self._resolve(project_id)
        run_id = new_run_id()
        state = self.state.load(pid)
        unfinished = bool(state.runs) and not state.runs[-1].finished_at
        if state.state in (S.INGESTING, S.CONTEXT_BUILDING) or (state.state == S.ANALYZING and unfinished):
            self.state.transition_project(state, S.ERROR, "orchestrator", "ejecución anterior interrumpida", run_id)
        self.state.transition_project(state, S.INGESTING, "orchestrator", f"inicio de {run_id} ({trigger})", run_id)
        summary = RunSummary(run_id=run_id, started_at=now_iso(), trigger=trigger)
        state.runs.append(summary)
        self.state.save(state)
        self.audit.record(AuditEntry(event="run_started", run_id=run_id, project_id=pid, agent="orchestrator_agent",
                                     reason=trigger))

        # ---------------- 1. descubrimiento + ingesta + contexto (flujo determinista)
        try:
            out = await self._run_project_workflow(pid, root, run_id)
            manifest = ProjectManifest.model_validate(out["manifest"])
            context = ProjectContext.model_validate(out["context"])
            stories = [UserStory.model_validate(s) for s in out["stories"]]
            docs = [NormalizedDocument.model_validate(d) for d in out["normalized_docs"]]
        except Exception as exc:
            return self._fail_project(pid, run_id, summary, exc)

        # ---------------- 1b. sin historias escritas → el generador propone el backlog
        generacion = None
        if not stories:
            try:
                stories, generacion = await self._generated_stories(pid, context, docs, run_id, regenerate)
            except Exception as exc:
                summary.errors["_generacion"] = f"{type(exc).__name__}: {exc}"[:300]
                if classify_error(exc) == "fatal":
                    return self._fail_project(pid, run_id, summary, exc, context=context)
                stories = []
            summary.generation = generacion or ""

        state = self.state.load(pid)
        state.manifest_version, state.last_scan = manifest.version, manifest.last_scan
        by_id = {s.story_id: s for s in stories}
        for s in stories:
            rec = state.stories.get(s.story_id)
            if rec is None:
                state.stories[s.story_id] = StoryRecord(story_id=s.story_id, title=s.title, source=s.source,
                                                        source_signature=s.signature, origin=s.origin)
            elif rec.source_signature != s.signature:
                rec.source_changed, rec.source_signature, rec.title = True, s.signature, s.title
        self.state.save(state)

        # ---------------- 2. selección (límites de iteración) y orden por dependencias
        candidatas = [sid for sid in by_id if self._needs_processing(state.stories[sid], force)]
        if story_ids:
            pedidas = {x.strip().upper() for x in story_ids if x.strip()}
            candidatas = [sid for sid in candidatas if sid in pedidas]
        orden, _ = dependency_order(stories)
        candidatas = sorted(candidatas, key=orden.index)
        seleccion = candidatas[: self.settings.max_stories_per_run]
        summary.skipped = candidatas[self.settings.max_stories_per_run:]

        # ---------------- 3. flujo por historia
        if seleccion:
            self.state.transition_project(state, S.ANALYZING, "orchestrator", f"{len(seleccion)} historia(s)", run_id)
        for sid in seleccion:
            try:
                await self._process_story(state, by_id[sid], context, stories, docs, run_id, summary)
            except FatalError as exc:
                summary.errors[sid] = str(exc)
                return self._fail_project(pid, run_id, summary, exc, stories=stories, context=context)

        return self._finalize(pid, run_id, summary, context, stories)

    async def _generated_stories(self, pid: str, context: ProjectContext, docs: list[NormalizedDocument],
                                 run_id: str, regenerate: bool) -> tuple[list[UserStory], str | None]:
        """Historias propuestas por story_generator_agent; se reutilizan mientras los documentos no cambien."""
        if not any(d.text for d in docs):
            return [], None
        store = self.results_store(pid)
        firmas = {d.path: d.signature for d in docs}
        previa = store.read_json(GENERATION_FILE)
        if previa and not regenerate and previa.get("doc_signatures") == firmas:
            return [UserStory.model_validate(s) for s in previa["stories"]], "reutilizadas"

        prompt_ctx = context_for_prompt(context, docs, [], "", self.settings.generation_context_max_chars)
        anteriores = [{k: s[k] for k in ("story_id", "title", "role", "need", "benefit", "acceptance_criteria")}
                      for s in (previa or {}).get("stories", [])]
        session_state = {K_PROJECT_ID: pid, K_RUN_ID: run_id, K_GEN_CONTEXT: prompt_ctx,
                         K_PREVIOUS: anteriores or None, K_GEN_LIMIT: self.settings.max_generated_stories}
        t0 = time.monotonic()
        last_exc, out, tokens = None, None, defaultdict(int)
        for intento in range(self.settings.max_retries + 1):
            session = await self._sessions.create_session(app_name="hu_story_generation", user_id="orchestrator",
                                                          state=dict(session_state))
            try:
                msg = types.Content(role="user", parts=[types.Part.from_text(text=f"Genera el backlog del proyecto {pid}.")])
                async for ev in self._generation_runner.run_async(
                        user_id="orchestrator", session_id=session.id, new_message=msg,
                        run_config=RunConfig(max_llm_calls=self.settings.max_llm_calls_per_story)):
                    if ev.usage_metadata:
                        for k in ("prompt_token_count", "candidates_token_count", "total_token_count"):
                            tokens[k] += getattr(ev.usage_metadata, k, None) or 0
                    if ev.content and ev.content.parts and any((p.text or "").startswith("BLOQUEADO") for p in ev.content.parts):
                        raise FatalError(ev.content.parts[0].text)
                final = await self._sessions.get_session(app_name="hu_story_generation", user_id="orchestrator",
                                                         session_id=session.id)
                if not final.state.get(K_GEN_OUT):
                    raise RecoverableError("story_generator_agent no produjo historias")
                out = StoryGenerationOutput.model_validate(final.state[K_GEN_OUT])
                break
            except Exception as exc:
                last_exc = exc
                if classify_error(exc) == "fatal" or intento >= self.settings.max_retries:
                    raise
                await asyncio.sleep(self.retry_backoff_s * (2 ** intento))
            finally:
                await self._sessions.delete_session(app_name="hu_story_generation", user_id="orchestrator",
                                                    session_id=session.id)
        if out is None:
            raise last_exc or RecoverableError("generación sin resultado")

        source = store.uri(GENERATION_FILE)
        stories, vistos = [], set()
        for g in out.stories[: self.settings.max_generated_stories]:
            sid = re.sub(r"[^A-Z0-9\-]", "", g.story_id.upper()) or f"HU-IA-{len(stories) + 1:03d}"
            while sid in vistos:
                sid += "B"
            vistos.add(sid)
            us = UserStory(
                story_id=sid, project_id=pid, title=g.title, epic=g.epic, role=g.role, need=g.need, benefit=g.benefit,
                description=f"Como {g.role} quiero {g.need} para {g.benefit}", acceptance_criteria=g.acceptance_criteria,
                business_rules=g.business_rules, depends_on=[d.upper() for d in g.depends_on], status="generada por IA",
                source=source, source_format="generated", origin="generated", evidence=g.evidence,
                generation_confidence=g.confidence,
            )
            us.signature = stable_hash(us.model_dump(exclude={"signature", "source"}))
            stories.append(us)

        data = {"project_id": pid, "run_id": run_id, "created_at": now_iso(), "doc_signatures": firmas,
                "output": out.model_dump(mode="json"), "stories": [s.model_dump(mode="json") for s in stories]}
        store.write_json(GENERATION_FILE, data)
        version, _ = store.write_version("generated/story_generation/versions", "BACKLOG", "agent_proposal", data)
        self.audit.record(AuditEntry(
            event="agent_output", run_id=run_id, project_id=pid, agent="story_generator_agent",
            input_hash=stable_hash([prompt_ctx, anteriores]), output_hash=stable_hash(data["output"]),
            confidence=out.confidence, latency_ms=int((time.monotonic() - t0) * 1000), tokens=dict(tokens),
            decision=f"{len(stories)} historias generadas desde {len(docs)} documento(s)",
            human_approval="REVIEW_PENDING", details={"version": version, "ids": [s.story_id for s in stories]},
        ))
        # Historias de una generación anterior que ya no aplican: su decisión pendiente se invalida.
        state = self.state.load(pid)
        nuevas = {s.story_id for s in stories}
        for d in state.decisions.values():
            if d.pending and d.request.story_id not in nuevas:
                d.superseded_by_run = run_id
        self.state.save(state)
        return stories, "regeneradas" if previa else "generadas"

    def _needs_processing(self, rec: StoryRecord, force: bool) -> bool:
        if rec.pending_reanalysis:
            return True
        if rec.state == S.WAITING_FOR_HUMAN:
            return rec.source_changed  # si no cambió la fuente, espera la decisión humana
        if rec.state in (S.DISCOVERED, S.ERROR) or rec.state in STORY_IN_PROGRESS or rec.source_changed:
            return True
        return force

    async def _run_project_workflow(self, pid: str, root: str, run_id: str) -> dict:
        session = await self._sessions.create_session(
            app_name="hu_project_workflow", user_id="orchestrator",
            state={"project_id": pid, "project_root": root, "run_id": run_id})
        msg = types.Content(role="user", parts=[types.Part.from_text(text=f"Prepara el proyecto {pid}.")])
        async for _ in self._project_runner.run_async(user_id="orchestrator", session_id=session.id, new_message=msg):
            pass
        final = await self._sessions.get_session(app_name="hu_project_workflow", user_id="orchestrator", session_id=session.id)
        await self._sessions.delete_session(app_name="hu_project_workflow", user_id="orchestrator", session_id=session.id)
        return dict(final.state)

    # ========================================================= flujo por historia
    async def _process_story(self, state: ProjectState, story: UserStory, context: ProjectContext,
                             stories: list[UserStory], docs: list[NormalizedDocument], run_id: str,
                             summary: RunSummary) -> None:
        pid, sid = state.project_id, story.story_id
        rec = state.stories[sid]
        store = self.results_store(pid)
        base = f"generated/user_stories/{sid}"
        actor = "orchestrator_agent"

        # Versión original: nunca se sobrescribe; una nueva por cada cambio en la fuente.
        if rec.original_version_signature != story.signature:
            store.write_version(f"{base}/versions", sid, "original" if story.origin == "document" else "ai_generated", story)
            store.write_json(f"{base}/original.json", story)
            rec.original_version_signature = story.signature

        if rec.state == S.WAITING_FOR_HUMAN and rec.source_changed and rec.pending_decision_id:
            state.decisions[rec.pending_decision_id].superseded_by_run = run_id
            rec.pending_decision_id = ""
        if rec.state in STORY_IN_PROGRESS and rec.state != S.ANALYZING:
            self.state.transition_story(state, sid, S.ERROR, actor, "ejecución anterior interrumpida", run_id)

        effective, feedback = self._apply_human_modifications(story, rec)
        story_approved = story.approved or rec.approved_by_human
        effective = effective.model_copy(update={"approved": story_approved})
        prompt_ctx = context_for_prompt(context, docs, stories, sid, self.settings.context_max_chars)
        session_state = {
            K_PROJECT_ID: pid, K_RUN_ID: run_id, K_STORY: effective.model_dump(mode="json"),
            K_CONTEXT: prompt_ctx, K_FEEDBACK: feedback or None,
        }
        input_hash = stable_hash([session_state[K_STORY], prompt_ctx, feedback])
        summary.processed.append(sid)

        result, metrics, last_exc = None, {}, None
        for intento in range(self.settings.max_retries + 1):
            if state.stories[sid].state != S.ANALYZING:
                if state.stories[sid].state in STORY_IN_PROGRESS:
                    self.state.transition_story(state, sid, S.ERROR, actor, "reintento", run_id)
                self.state.transition_story(state, sid, S.ANALYZING, actor,
                                            "análisis" if intento == 0 else f"reintento {intento}", run_id)
            try:
                result, metrics = await self._run_story_workflow(state, sid, session_state, run_id)
                break
            except Exception as exc:
                last_exc = exc
                kind = classify_error(exc)
                self.audit.record(AuditEntry(event="error", run_id=run_id, project_id=pid, story_id=sid,
                                             agent="story_workflow", errors=[f"{type(exc).__name__}: {exc}"[:500]],
                                             decision=kind, details={"intento": intento}))
                if kind == "fatal":
                    self._story_error(state, sid, exc, run_id)
                    raise FatalError(str(exc)) from exc
                if intento < self.settings.max_retries:
                    await asyncio.sleep(self.retry_backoff_s * (2 ** intento))
        if result is None:
            self._story_error(state, sid, last_exc, run_id)
            summary.errors[sid] = f"{type(last_exc).__name__}: {last_exc}"[:300]
            return

        analysis = StoryAnalysisOutput.model_validate(result[K_ANALYSIS])
        arch = ArchitectureReviewOutput.model_validate(result[K_ARCH])
        agg = AggregatedReview.model_validate(result[K_AGG])
        hitl = HitlEvaluation.model_validate(result[K_HITL])

        # ---------------- artefactos (contrato común + salida original del agente)
        resp = {r.agent: r for r in agg.responses}
        store.write_json(f"{base}/analysis.json", {"agent_response": resp.get("story_analyst_agent"), "output": result[K_ANALYSIS]})
        store.write_json(f"{base}/architecture_review.json", {"agent_response": resp.get("architecture_agent"), "output": result[K_ARCH]})
        store.write_json(f"{base}/tests.json", {"agent_response": resp.get("qa_agent"), "output": result[K_QA]})
        store.write_json(f"{base}/aggregated_review.json", agg)
        store.write_json(f"{base}/hitl.json", hitl)
        proposed = {
            "story_id": sid, "project_id": pid, "version_label": "agent_proposal", "run_id": run_id,
            "based_on_signature": story.signature, "improved_story": analysis.improved_story.model_dump(),
            "suggested_changes": [c.model_dump() for c in analysis.suggested_changes],
            "invest_score": analysis.invest_score.clamp().model_dump(),
            "quality_score": analysis.invest_score.quality_score, "confidence": agg.confidence,
            "hitl_level": int(hitl.level), "created_at": now_iso(),
        }
        version, _ = store.write_version(f"{base}/versions", sid, "agent_proposal", proposed)
        store.write_json(f"{base}/proposed.json", proposed)

        # ---------------- auditoría por agente
        outputs = {"story_analyst_agent": result[K_ANALYSIS], "architecture_agent": result[K_ARCH], "qa_agent": result[K_QA],
                   "review_aggregator_agent": result[K_AGG], "hitl_evaluator_agent": result[K_HITL]}
        for agent, output in outputs.items():
            m = metrics.get(agent, {})
            conf = output.get("confidence") if isinstance(output, dict) else None
            self.audit.record(AuditEntry(
                event="agent_output", run_id=run_id, project_id=pid, story_id=sid, agent=agent,
                input_hash=input_hash, output_hash=stable_hash(output), confidence=conf,
                hitl=int(hitl.level) if agent == "hitl_evaluator_agent" else None,
                latency_ms=m.get("latency_ms"), tokens=m.get("tokens", {}),
                decision=(output.get("status") or output.get("overall_status") or output.get("action") or "")
                if isinstance(output, dict) else "",
                details={"reasons": [r.code for r in hitl.reasons]} if agent == "hitl_evaluator_agent" else {},
            ))
        approval = {HitlLevel.APPROVAL_REQUIRED: "PENDING", HitlLevel.REVIEW: "REVIEW_PENDING"}.get(hitl.level, "NOT_REQUIRED")
        self.audit.record(AuditEntry(
            event="proposal", run_id=run_id, project_id=pid, story_id=sid, agent="story_analyst_agent",
            before=stable_hash(story), after=stable_hash(proposed), confidence=agg.confidence,
            reason="; ".join(f"{c.field}: {c.reason}" for c in analysis.suggested_changes)[:1000] or analysis.summary,
            source=story.source, human_approval=approval, details={"version": version},
        ))

        # ---------------- registro y decisión HITL
        rec = state.stories[sid]
        rec.hitl_level, rec.quality_score, rec.confidence = int(hitl.level), analysis.invest_score.quality_score, agg.confidence
        rec.analysis_iterations += 1
        rec.pending_reanalysis = rec.source_changed = False
        rec.last_error = ""
        if hitl.level == HitlLevel.APPROVAL_REQUIRED:
            req = self._decision_request(state, story, agg, hitl, analysis, arch, run_id)
            state.decisions[req.decision_id] = DecisionRecord(request=req)
            rec.pending_decision_id = req.decision_id
            self.state.transition_story(state, sid, S.WAITING_FOR_HUMAN, "hitl_evaluator_agent", req.reason[:300], run_id)
            store.write_json(f"{base}/hitl_request.json", req)
            self.audit.record(AuditEntry(event="hitl_request", run_id=run_id, project_id=pid, story_id=sid,
                                         agent=req.agent, decision="WAITING_FOR_HUMAN", confidence=req.confidence,
                                         hitl=2, reason=req.reason[:1000], details={"decision_id": req.decision_id}))
            summary.waiting.append(sid)
        else:
            rec.review_pending = hitl.level == HitlLevel.REVIEW
            motivo = "HITL nivel 1: continúa, revisar después" if rec.review_pending else "HITL nivel 0: automático"
            self.state.transition_story(state, sid, S.READY_FOR_IMPLEMENTATION, "hitl_evaluator_agent", motivo, run_id)
            (summary.review if rec.review_pending else summary.ready).append(sid)
        self.state.save(state)

    def _apply_human_modifications(self, story: UserStory, rec: StoryRecord) -> tuple[UserStory, str]:
        if not (rec.pending_reanalysis and (rec.human_modifications or rec.human_feedback)):
            return story, ""
        mods = rec.human_modifications or {}
        upd = {k: mods[k] for k in ("title", "role", "need", "benefit", "description", "acceptance_criteria",
                                    "business_rules", "depends_on") if k in mods}
        if "narrative" in mods:
            upd["description"] = mods["narrative"]
        feedback = rec.human_feedback
        if mods.get("answers"):
            feedback += "\nRespuestas del humano a las preguntas:\n" + json.dumps(mods["answers"], ensure_ascii=False, indent=1)
        return story.model_copy(update=upd), feedback.strip()

    async def _run_story_workflow(self, state: ProjectState, sid: str, session_state: dict, run_id: str):
        session = await self._sessions.create_session(app_name="hu_story_workflow", user_id="orchestrator",
                                                      state=dict(session_state))
        msg = types.Content(role="user", parts=[types.Part.from_text(
            text=f"Analiza la historia {sid} del proyecto {session_state[K_PROJECT_ID]}.")])
        metrics: dict[str, dict] = defaultdict(lambda: {"tokens": defaultdict(int), "t0": None, "t1": None})
        cfg = RunConfig(max_llm_calls=self.settings.max_llm_calls_per_story)
        try:
            async for ev in self._story_runner.run_async(user_id="orchestrator", session_id=session.id,
                                                         new_message=msg, run_config=cfg):
                m = metrics[ev.author]
                m["t0"] = m["t0"] or ev.timestamp
                m["t1"] = ev.timestamp
                u = ev.usage_metadata
                if u:
                    for k in ("prompt_token_count", "candidates_token_count", "total_token_count"):
                        m["tokens"][k] += getattr(u, k, None) or 0
                if ev.error_code or ev.error_message:
                    raise RecoverableError(f"{ev.author}: {ev.error_code} {ev.error_message}")
                if ev.content and ev.content.parts and any((p.text or "").startswith("BLOQUEADO") for p in ev.content.parts):
                    raise FatalError(ev.content.parts[0].text)
                rec = state.stories[sid]
                if ev.author in PARALLEL_AGENTS and rec.state == S.ANALYZING:
                    self.state.transition_story(state, sid, S.ARCHITECTURE_REVIEW, "parallel_review",
                                                "revisión de arquitectura y generación de pruebas en paralelo", run_id)
                elif ev.author == "review_aggregator_agent" and rec.state == S.ARCHITECTURE_REVIEW:
                    self.state.transition_story(state, sid, S.VALIDATING, ev.author, "agregación y evaluación HITL", run_id)
            final = await self._sessions.get_session(app_name="hu_story_workflow", user_id="orchestrator", session_id=session.id)
        finally:
            await self._sessions.delete_session(app_name="hu_story_workflow", user_id="orchestrator", session_id=session.id)
        result = dict(final.state)
        faltan = [k for k in (K_ANALYSIS, K_ARCH, K_QA, K_AGG, K_HITL) if not result.get(k)]
        if faltan:
            raise RecoverableError(f"Salida incompleta del flujo: faltan {', '.join(faltan)}")
        out_metrics = {}
        for agent, m in metrics.items():
            lat = int(((m["t1"] or 0) - (m["t0"] or 0)) * 1000) if m["t0"] else None
            out_metrics[agent] = {"latency_ms": lat, "tokens": dict(m["tokens"])}
        return result, out_metrics

    def _story_error(self, state: ProjectState, sid: str, exc: Exception | None, run_id: str) -> None:
        rec = state.stories[sid]
        rec.last_error = f"{type(exc).__name__}: {exc}"[:500] if exc else "error desconocido"
        if rec.state != S.ERROR:
            self.state.transition_story(state, sid, S.ERROR, "orchestrator_agent", rec.last_error, run_id)

    def _decision_request(self, state, story: UserStory, agg: AggregatedReview, hitl: HitlEvaluation,
                          analysis: StoryAnalysisOutput, arch: ArchitectureReviewOutput, run_id: str) -> HitlDecisionRequest:
        sid = story.story_id
        n = 1 + sum(1 for d in state.decisions.values() if d.request.story_id == sid)
        stop = [r for r in hitl.reasons if r.code not in ("MEDIUM_CONFIDENCE", "WORDING_IMPROVEMENT")] or hitl.reasons
        codes = {r.code for r in stop}
        if codes & {"ARCHITECTURE_CHANGE", "MIGRATION", "SECURITY_CHANGE", "PUBLIC_API_CHANGE", "DELETION",
                    "IRREVERSIBLE_CHANGE", "COST_INCREASE", "CROSS_PROJECT_IMPACT"}:
            rec_txt = (arch.recommendations[0].statement if arch.recommendations else arch.hitl_reason) or \
                "Validar el cambio crítico con el arquitecto responsable antes de implementar."
        elif "CRITICAL_AMBIGUITY" in codes:
            rec_txt = "Responder las preguntas bloqueantes y luego aprobar la propuesta del agente (o enviarla como MODIFIED con las respuestas)."
        else:
            rec_txt = analysis.hitl_reason or "Revisar la propuesta del agente antes de continuar."
        alternatives = ["Aprobar la propuesta del agente (APPROVED)",
                        "Corregir la historia o responder las preguntas (MODIFIED)",
                        "Rechazar la historia (REJECTED)",
                        "Pedir más información al dueño del producto (NEEDS_MORE_INFORMATION)"]
        if "MIGRATION" in codes:
            alternatives.insert(1, "Separar la migración en una historia habilitadora con plan de respaldo y reversión")
        if codes & {"ARCHITECTURE_CHANGE", "SECURITY_CHANGE", "PUBLIC_API_CHANGE"}:
            alternatives.insert(1, "Registrar un ADR con la decisión antes de implementar")
        sev_order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        risk = max((r.severity for r in stop), key=sev_order.index, default=Severity.MEDIUM)
        return HitlDecisionRequest(
            project_id=state.project_id, story_id=sid, decision_id=f"D-{state.project_id}-{sid}-{n:02d}",
            reason="; ".join(r.description for r in stop)[:1500], agent=stop[0].agent if stop else "hitl_evaluator_agent",
            recommendation=rec_txt, alternatives=alternatives,
            sources=sorted(set(agg.sources + ([story.source] if story.source else []))),
            risk=risk.value, confidence=agg.confidence, reasons=hitl.reasons,
            blocking_questions=[q.question for q in agg.blocking_questions], run_id=run_id,
        )

    # ============================================================ cierre de ejecución
    def _finalize(self, pid: str, run_id: str, summary: RunSummary, context: ProjectContext | None,
                  stories: list[UserStory], story_ids: list[str] | None = None) -> dict:
        state = self.state.load(pid)
        actuales = story_ids or [s.story_id for s in stories] or list(state.stories)
        if state.pending_decisions():
            target, motivo = S.WAITING_FOR_HUMAN, f"{len(state.pending_decisions())} decisión(es) pendiente(s)"
        elif all(state.stories[s].state in STORY_TERMINAL for s in actuales if s in state.stories):
            target, motivo = S.COMPLETED, "todas las historias terminaron"
        else:
            target, motivo = S.ANALYZING, "quedan historias por procesar (errores o límite por ejecución)"
        if state.state != target:
            self.state.transition_project(state, target, "orchestrator_agent", motivo, run_id)
        reports = build_reports(self.results_store(pid), state, context, stories)
        for r in state.runs:
            if r.run_id == run_id:
                r.processed, r.ready, r.review, r.waiting = summary.processed, summary.ready, summary.review, summary.waiting
                r.errors, r.skipped, r.finished_at = summary.errors, summary.skipped, now_iso()
                r.generation = summary.generation
        self.state.save(state)
        self.audit.record(AuditEntry(event="run_finished", run_id=run_id, project_id=pid, agent="orchestrator_agent",
                                     decision=state.state.value, details=summary.model_dump(exclude={"run_id"})))
        return self._run_result(state, run_id, summary, reports)

    def _fail_project(self, pid, run_id, summary, exc, stories=None, context=None) -> dict:
        state = self.state.load(pid)
        state.last_error = f"{type(exc).__name__}: {exc}"[:500]
        if state.state != S.ERROR:
            self.state.transition_project(state, S.ERROR, "orchestrator_agent", state.last_error, run_id)
        for r in state.runs:
            if r.run_id == run_id:
                r.errors = {**summary.errors, "_proyecto": state.last_error}
                r.processed, r.finished_at = summary.processed, now_iso()
        self.state.save(state)
        self.audit.record(AuditEntry(event="error", run_id=run_id, project_id=pid, agent="orchestrator_agent",
                                     errors=[state.last_error], decision="fatal"))
        return {"status": "ERROR", "project_id": pid, "run_id": run_id, "error": state.last_error,
                "recuperable": False,
                "sugerencia": "Revisa permisos del bucket, credenciales de Vertex AI o el project.yaml y vuelve a ejecutar."}

    def _run_result(self, state: ProjectState, run_id: str, summary: RunSummary, reports: dict) -> dict:
        store = self.results_store(state.project_id)
        return {
            "status": state.state.value,
            "project_id": state.project_id,
            "run_id": run_id,
            "historias_generadas_por_ia": summary.generation or "no",
            "procesadas": summary.processed,
            "listas_para_implementacion": summary.ready,
            "continuan_con_revision": summary.review,
            "esperando_decision_humana": [d.request.model_dump(mode="json") for d in state.pending_decisions()],
            "errores": summary.errors,
            "omitidas_por_limite": summary.skipped,
            "reportes": reports,
            "resultados_en": store.uri("generated/"),
        }

    # ===================================================================== HITL
    def pending_decisions(self, project_id: str | None = None) -> list[HitlDecisionRequest]:
        pids = [self._resolve(project_id)[0]] if project_id else self._known_project_ids()
        out = []
        for pid in pids:
            out += [d.request for d in self.state.load(pid).pending_decisions()]
        return out

    async def apply_decision(self, decision_id: str, decision: str, comment: str = "",
                             modifications: dict | None = None, decided_by: str = "usuario") -> dict:
        try:
            dtype = HumanDecisionType(decision.strip().upper())
        except ValueError:
            return {"status": "error", "mensaje": f"Decisión inválida {decision!r}. Usa: {', '.join(d.value for d in HumanDecisionType)}"}
        found = self.state.find_decision(decision_id, self._known_project_ids())
        if not found:
            return {"status": "error", "mensaje": f"No existe la decisión {decision_id}."}
        state, record = found
        if not record.pending:
            return {"status": "error", "mensaje": f"{decision_id} ya fue resuelta ({record.resolution.decision.value if record.resolution else 'invalidada'})."}
        pid, sid = state.project_id, record.request.story_id
        rec = state.stories[sid]
        store = self.results_store(pid)
        base = f"generated/user_stories/{sid}"
        actor = f"human:{decided_by}"
        mods = modifications or {}

        if dtype == HumanDecisionType.MODIFIED:
            if not mods and not comment:
                return {"status": "error", "mensaje": "MODIFIED requiere los cambios (criterios, narrativa o respuestas) o un comentario."}
            if rec.reanalysis_count >= self.settings.max_reanalysis:
                return {"status": "error", "mensaje": f"Se alcanzó el límite de {self.settings.max_reanalysis} reanálisis para {sid}. "
                                                      "Decide APPROVED o REJECTED."}

        hd = HumanDecision(decision_id=decision_id, decision=dtype, comment=comment, modifications=mods, decided_by=decided_by)
        record.history.append(hd)
        record.resolution = hd
        original = store.read_json(f"{base}/original.json") or {}
        self.audit.record(AuditEntry(event="human_decision", project_id=pid, story_id=sid, agent=actor,
                                     decision=dtype.value, human_approval=dtype.value, reason=comment[:1000],
                                     after=stable_hash(mods) if mods else "", details={"decision_id": decision_id}))
        resultado: dict = {"status": "ok", "decision_id": decision_id, "story_id": sid, "decision": dtype.value}

        if dtype == HumanDecisionType.APPROVED:
            self.state.transition_story(state, sid, S.APPROVED, actor, comment or "aprobada")
            proposed = store.read_json(f"{base}/proposed.json") or {}
            approved = {**proposed, "version_label": "human_approved", "approved_by": decided_by,
                        "approved_at": hd.decided_at, "decision_id": decision_id, "comment": comment}
            v, uri = store.write_version(f"{base}/versions", sid, "human_approved", approved)
            store.write_json(f"{base}/approved.json", approved)
            rec.approved_by_human, rec.pending_decision_id, rec.review_pending = True, "", False
            self.state.transition_story(state, sid, S.READY_FOR_IMPLEMENTATION, actor, "aprobada por humano")
            self.audit.record(AuditEntry(event="proposal", project_id=pid, story_id=sid, agent=actor,
                                         before=stable_hash(original), after=stable_hash(approved),
                                         human_approval="APPROVED", reason=comment, details={"version": v}))
            resultado["version_aprobada"] = uri
        elif dtype == HumanDecisionType.REJECTED:
            self.state.transition_story(state, sid, S.REJECTED, actor, comment or "rechazada")
            store.write_json(f"{base}/rejected.json", hd)
            rec.pending_decision_id = ""
        elif dtype == HumanDecisionType.MODIFIED:
            human_version = {"story_id": sid, "project_id": pid, "version_label": "human_modified",
                             "base": original, "modifications": mods, "comment": comment,
                             "decided_by": decided_by, "decided_at": hd.decided_at, "decision_id": decision_id}
            v, uri = store.write_version(f"{base}/versions", sid, "human_modified", human_version)
            rec.human_modifications, rec.human_feedback = mods, comment
            rec.pending_reanalysis, rec.pending_decision_id = True, ""
            rec.reanalysis_count += 1
            resultado["version_humana"] = uri
        else:  # NEEDS_MORE_INFORMATION: la historia sigue esperando
            resultado["nota"] = "La historia sigue en WAITING_FOR_HUMAN hasta que llegue la información."
        self.state.save(state)

        # Continuar automáticamente después de la decisión.
        if dtype == HumanDecisionType.MODIFIED:
            resultado["reanudacion"] = await self.analyze_project(pid, story_ids=[sid], trigger=f"decisión {decision_id}")
        elif dtype in (HumanDecisionType.APPROVED, HumanDecisionType.REJECTED):
            async with self._lock:
                resultado["reanudacion"] = self._finalize_after_decision(pid, decision_id)
        return resultado

    def _finalize_after_decision(self, pid: str, decision_id: str) -> dict:
        store = self.results_store(pid)
        ctx_data = store.read_json("generated/project_context.json")
        context = ProjectContext.model_validate(ctx_data) if ctx_data else None
        stories = []
        for sid in self.state.load(pid).stories:
            data = store.read_json(f"generated/user_stories/{sid}/original.json")
            if data:
                stories.append(UserStory.model_validate(data))
        run_id = new_run_id()
        summary = RunSummary(run_id=run_id, started_at=now_iso(), trigger=f"decisión {decision_id}")
        state = self.state.load(pid)
        state.runs.append(summary)
        self.state.save(state)
        return self._finalize(pid, run_id, summary, context, stories, story_ids=list(state.stories))

    # ============================================================ consultas
    def project_status(self, project_id: str) -> dict:
        pid, _ = self._resolve(project_id)
        st = self.state.load(pid)
        return {
            "project_id": pid, "project_name": st.project_name, "estado": st.state.value,
            "manifest_version": st.manifest_version, "ultimo_escaneo": st.last_scan, "ultimo_error": st.last_error,
            "historias": [{"story_id": r.story_id, "titulo": r.title, "origen": r.origin, "estado": r.state.value, "hitl": r.hitl_level,
                           "revisar_despues": r.review_pending, "calidad": r.quality_score, "confianza": r.confidence,
                           "decision_pendiente": r.pending_decision_id, "aprobada_por_humano": r.approved_by_human,
                           "error": r.last_error} for r in st.stories.values()],
            "decisiones_pendientes": [d.request.decision_id for d in st.pending_decisions()],
            "ultima_ejecucion": st.runs[-1].model_dump() if st.runs else None,
            "resultados_en": self.results_store(pid).uri("generated/"),
        }

    def explain_story(self, project_id: str, story_id: str) -> dict:
        pid, _ = self._resolve(project_id)
        st = self.state.load(pid)
        sid = story_id.strip().upper()
        if sid not in st.stories:
            return {"status": "error", "mensaje": f"{sid} no existe en {pid}."}
        store = self.results_store(pid)
        base = f"generated/user_stories/{sid}"
        agg = store.read_json(f"{base}/aggregated_review.json") or {}
        hitl = store.read_json(f"{base}/hitl.json") or {}
        proposed = store.read_json(f"{base}/proposed.json") or {}
        audit = [e.model_dump(mode="json", exclude_defaults=True) for e in self.audit.entries(pid, sid)]
        return {
            "project_id": pid, "story_id": sid, "estado_final": st.stories[sid].state.value,
            "transiciones": [t.model_dump(mode="json") for t in st.stories[sid].history],
            "evaluacion_hitl": hitl,
            "revisiones_por_agente": [{"agent": r["agent"], "status": r["status"], "confidence": r["confidence"],
                                       "summary": r["summary"], "findings": r.get("findings", [])[:8]}
                                      for r in agg.get("responses", [])],
            "conflictos": agg.get("conflicts", []),
            "propuesta": proposed.get("improved_story"),
            "cambios_sugeridos": proposed.get("suggested_changes", []),
            "decisiones": [d.model_dump(mode="json") for d in st.decisions.values() if d.request.story_id == sid],
            "versiones": store.list(f"{base}/versions/"),
            "auditoria": audit[-40:],
        }


# ------------------------------------------------------------------ singleton
_engine: HuEngine | None = None


def get_engine() -> HuEngine:
    global _engine
    if _engine is None:
        _engine = HuEngine()
    return _engine


def set_engine(engine: HuEngine | None) -> None:
    global _engine
    _engine = engine
