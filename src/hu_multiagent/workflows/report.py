"""Reporte consolidado del proyecto (determinista en el MVP; DOCUMENTATION_AGENT en fase 2)."""

from __future__ import annotations

from ..models.project import ProjectContext
from ..models.state import ProjectState, WorkflowState
from ..models.user_story import UserStory
from ..services.storage_service import ProjectStore


def dependency_order(stories: list[UserStory]) -> tuple[list[str], list[list[str]]]:
    """Orden topológico por depends_on (habilitadoras primero) y ciclos detectados."""
    ids = {s.story_id for s in stories}
    deps = {s.story_id: [d for d in s.depends_on if d in ids] for s in stories}
    order, visiting, done, cycles = [], [], set(), []

    def visit(n):
        if n in done:
            return
        if n in visiting:
            cycles.append(visiting[visiting.index(n):] + [n])
            return
        visiting.append(n)
        for d in deps.get(n, []):
            visit(d)
        visiting.pop()
        done.add(n)
        order.append(n)

    for s in sorted(ids):
        visit(s)
    return order, cycles


def build_reports(store: ProjectStore, state: ProjectState, context: ProjectContext | None,
                  stories: list[UserStory]) -> dict[str, str]:
    order, cycles = dependency_order(stories)
    by_id = {s.story_id: s for s in stories}
    backlog, risks = [], []
    for sid in order:
        rec = state.stories.get(sid)
        if not rec:
            continue
        base = f"generated/user_stories/{sid}"
        agg = store.read_json(f"{base}/aggregated_review.json") or {}
        hitl = store.read_json(f"{base}/hitl.json") or {}
        analysis = (store.read_json(f"{base}/analysis.json") or {}).get("output", {})
        backlog.append({
            "story_id": sid, "title": rec.title, "state": rec.state.value, "hitl_level": rec.hitl_level,
            "review_pending": rec.review_pending, "quality_score": rec.quality_score, "confidence": rec.confidence,
            "invest_score": analysis.get("invest_score"), "depends_on": by_id[sid].depends_on if sid in by_id else [],
            "pending_decision_id": rec.pending_decision_id, "approved_by_human": rec.approved_by_human,
            "origin": by_id[sid].origin if sid in by_id else rec.origin,
        })
        for resp in agg.get("responses", []):
            for r in resp.get("risks", []):
                risks.append({"story_id": sid, "agent": resp["agent"], **r})
        for c in agg.get("conflicts", []):
            risks.append({"story_id": sid, "agent": "review_aggregator_agent", **c})
        for r in hitl.get("reasons", []):
            if r.get("critical"):
                risks.append({"story_id": sid, "agent": r["agent"], "category": r["code"],
                              "description": r["description"], "severity": r["severity"]})

    uris = {
        "backlog_report": store.write_json("generated/reports/backlog_report.json",
                                           {"project_id": state.project_id, "recommended_order": order,
                                            "dependency_cycles": cycles, "stories": backlog}),
        "risk_report": store.write_json("generated/reports/risk_report.json",
                                        {"project_id": state.project_id,
                                         "project_risks": [r.model_dump() for r in (context.risks if context else [])],
                                         "story_risks": risks}),
    }

    pend = state.pending_decisions()
    lines = [
        f"# Reporte del proyecto {state.project_id} — {state.project_name}",
        "",
        f"- Estado del proyecto: **{state.state.value}**",
        f"- Última ejecución: {state.runs[-1].run_id if state.runs else '—'}",
        f"- Manifest: versión {state.manifest_version}",
        f"- Decisiones humanas pendientes: {len(pend)}",
        "",
        "## Historias",
        "",
        "| Orden | Historia | Estado | HITL | Calidad | Confianza |",
        "|---|---|---|---|---|---|",
    ]
    for i, b in enumerate(backlog, 1):
        flag = " (revisar)" if b["review_pending"] else ""
        q = f"{b['quality_score']:.0f}" if b["quality_score"] is not None else "—"
        c = f"{b['confidence']:.2f}" if b["confidence"] is not None else "—"
        ia = " *(IA)*" if b.get("origin") == "generated" else ""
        lines.append(f"| {i} | {b['story_id']} {b['title']}{ia} | {b['state']}{flag} | {b['hitl_level']} | {q} | {c} |")
    if any(b.get("origin") == "generated" for b in backlog):
        lines += ["", "> Las historias marcadas *(IA)* fueron generadas por story_generator_agent a partir de los "
                      "documentos del proyecto (ver generated/story_generation.json con su evidencia). "
                      "Requieren validación del dueño del producto."]
    if cycles:
        lines += ["", "**Dependencias circulares:** " + "; ".join(" → ".join(c) for c in cycles)]
    if pend:
        lines += ["", "## Decisiones pendientes (WAITING_FOR_HUMAN)", ""]
        for d in pend:
            r = d.request
            lines += [f"### {r.decision_id} — {r.story_id}", f"- Motivo: {r.reason}", f"- Recomendación: {r.recommendation}",
                      f"- Riesgo: {r.risk} · Confianza: {r.confidence:.2f}"]
            if r.blocking_questions:
                lines += ["- Preguntas bloqueantes:"] + [f"  - {q}" for q in r.blocking_questions]
            lines += ["- Opciones: " + ", ".join(o.value for o in r.options), ""]
    if context and context.open_questions:
        lines += ["", "## Preguntas abiertas del proyecto", ""] + [
            f"- {'**[bloqueante]** ' if q.blocking else ''}{q.question}" for q in context.open_questions]
    ready = [b["story_id"] for b in backlog if b["state"] == WorkflowState.READY_FOR_IMPLEMENTATION.value]
    if ready:
        lines += ["", "## Listas para implementación", "", ", ".join(ready)]
    lines += ["", "_Artefactos por historia en generated/user_stories/<ID>/; auditoría en audit/audit_log.jsonl._"]
    uris["project_report"] = store.write_text("generated/reports/project_report.md", "\n".join(lines) + "\n")
    return uris
