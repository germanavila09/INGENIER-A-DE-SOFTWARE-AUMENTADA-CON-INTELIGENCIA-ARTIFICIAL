"""Tamaño no funcional de una HU a partir de las restricciones del grafo de contexto que activa.

Funciones puras. Inspirado en SNAP (IFPUG, Software Non-functional Assessment Process): el tamaño no
funcional se mide aparte de los puntos de función. Es una versión **simplificada**, no SNAP: un puntaje
por restricción activada según su severidad, que fija el grafo aprobado (no el modelo).

Una restricción **violada** también cuenta como activada y queda abierta hasta que una persona la
resuelve (cambiando la HU y volviendo a dimensionar) o la exceptúa con una justificación.
"""

from __future__ import annotations

from app.services import context_graph

METHOD = "nfr_snap_lite_v1"
VERSION = 1
SEVERITY_POINTS = {"baja": 2, "media": 4, "alta": 7, "critica": 10}


def _by_constraint(items) -> dict[str, str]:
    """{constraint_id: motivo}, conservando el primer motivo de cada restricción."""
    out: dict[str, str] = {}
    for item in items or []:
        cid = item.get("constraint_id")
        if cid and cid not in out:
            out[cid] = (item.get("reason") or "").strip()[:500]
    return out


def analyze(graph: dict, graph_version: int | None, report: dict | None, previous: dict | None = None) -> dict:
    """Análisis de contexto a guardar en la HU (`context_analysis`) con su tamaño no funcional.

    `report` es el informe del agente de contexto: impacted_features, existing_entities,
    triggered_constraints, violations y summary. Los ids que no están en el grafo se descartan.
    `previous` es el análisis anterior de la HU: sus excepciones se conservan por restricción.
    """
    report = report or {}
    nodes = context_graph.node_map(graph)
    unknown: list[str] = []

    def known(node_id: str, kinds: tuple[str, ...]) -> bool:
        ok = node_id in nodes and nodes[node_id]["kind"] in kinds
        if not ok and node_id not in unknown:
            unknown.append(node_id)
        return ok

    impacted = [
        {"id": f["node_id"], "name": nodes[f["node_id"]]["name"], "status": nodes[f["node_id"]]["status"],
         "how": (f.get("how") or "")[:300]}
        for f in report.get("impacted_features") or [] if known(f.get("node_id", ""), ("feature",))
    ]
    existing = [
        {"id": nid, "name": nodes[nid]["name"]}
        for nid in dict.fromkeys(report.get("existing_entities") or [])
        if known(nid, ("entity", "feature")) and nodes[nid].get("status") == "existente"
    ]
    triggered = {cid: r for cid, r in _by_constraint(report.get("triggered_constraints")).items()
                 if known(cid, ("constraint",))}
    violated = {cid: r for cid, r in _by_constraint(report.get("violations")).items() if known(cid, ("constraint",))}

    waivers = {v["constraint_id"]: v["waiver"] for v in (previous or {}).get("violations", [])
               if v.get("status") == "waived" and v.get("waiver")}
    items = []
    for cid in dict.fromkeys([*triggered, *violated]):
        c = nodes[cid]
        items.append({"constraint_id": cid, "name": c["name"], "category": c["category"], "severity": c["severity"],
                      "points": SEVERITY_POINTS[c["severity"]], "reason": violated.get(cid) or triggered.get(cid, ""),
                      "violated": cid in violated})
    violations = []
    for cid, reason in violated.items():
        c = nodes[cid]
        entry = {"constraint_id": cid, "name": c["name"], "category": c["category"], "severity": c["severity"],
                 "rule": c["rule"], "reason": reason, "status": "open"}
        if cid in waivers:
            entry.update(status="waived", waiver=waivers[cid])
        violations.append(entry)

    return {
        "method": METHOD,
        "version": VERSION,
        "graph_version": graph_version,
        "summary": (report.get("summary") or "")[:2000],
        "impacted_features": impacted,
        "existing_entities": existing,
        "nfr_points": sum(i["points"] for i in items),
        "constraints": items,
        "violations": violations,
        "unknown_ids": unknown,
    }


def open_violations(analysis: dict | None) -> list[dict]:
    return [v for v in (analysis or {}).get("violations", []) if v.get("status") == "open"]


__all__ = ["METHOD", "SEVERITY_POINTS", "analyze", "open_violations"]
