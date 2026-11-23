"""Grafo de conocimiento del contexto de un proyecto: funcionalidades, entidades y restricciones.

Funciones puras: validación, consultas y texto para los agentes. Lo propone un agente a partir de
los documentos del proyecto y lo aprueba una persona (ver services/context.py y docs/metodo-fpa-finops.md).

Nodos:
- feature: funcionalidad del sistema. `status`: existente | planificado.
- entity: grupo de datos de negocio. `status`: existente | planificado.
- constraint: restricción no funcional (seguridad, privacidad, rendimiento...), con `category`,
  `severity` y `rule`. `keywords` (opcional) ayuda a reconocerla en el texto de una HU.

Aristas:
- depends_on: feature → feature
- uses: feature → entity
- applies_to: constraint → feature | entity
"""

from __future__ import annotations

import re
import unicodedata

NODE_KINDS = ("feature", "entity", "constraint")
STATUSES = ("existente", "planificado")
CATEGORIES = ("seguridad", "privacidad", "rendimiento", "disponibilidad", "cumplimiento", "integracion", "costo", "otro")
SEVERITIES = ("baja", "media", "alta", "critica")
EDGE_TYPES = {
    "depends_on": ("feature", ("feature",)),
    "uses": ("feature", ("entity",)),
    "applies_to": ("constraint", ("feature", "entity")),
}
MAX_NODES, MAX_EDGES = 300, 1000
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class GraphError(ValueError):
    """Grafo inválido."""


def _text(node: dict, field: str, limit: int, required: bool = False) -> str | None:
    value = node.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise GraphError(f"Nodo '{node.get('id')}': falta '{field}'")
        return None
    if not isinstance(value, str):
        raise GraphError(f"Nodo '{node.get('id')}': '{field}' debe ser texto")
    return value.strip()[:limit]


def _choice(node: dict, field: str, options: tuple[str, ...], default: str | None = None) -> str:
    value = node.get(field, default)
    if value not in options:
        raise GraphError(f"Nodo '{node.get('id')}': {field} debe ser uno de {', '.join(options)}")
    return value


def _node(raw) -> dict:
    if not isinstance(raw, dict):
        raise GraphError("Cada nodo debe ser un objeto")
    node_id = raw.get("id")
    if not isinstance(node_id, str) or not _ID.match(node_id):
        raise GraphError(f"id de nodo inválido: {node_id!r} (minúsculas, dígitos, '-' o '_', máx. 64)")
    kind = _choice(raw, "kind", NODE_KINDS)
    node = {"id": node_id, "kind": kind, "name": _text(raw, "name", 200, required=True)}
    for field, limit in (("description", 1000), ("source", 300)):
        if (value := _text(raw, field, limit)) is not None:
            node[field] = value
    if kind == "constraint":
        node["category"] = _choice(raw, "category", CATEGORIES)
        node["severity"] = _choice(raw, "severity", SEVERITIES)
        node["rule"] = _text(raw, "rule", 1000, required=True)
        keywords = raw.get("keywords") or []
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            raise GraphError(f"Nodo '{node_id}': keywords debe ser una lista de textos")
        node["keywords"] = sorted({k.strip().lower()[:60] for k in keywords if k.strip()})[:20]
    else:
        node["status"] = _choice(raw, "status", STATUSES, default="planificado")
    return node


def validate(nodes, edges) -> dict:
    """Valida y normaliza el grafo. Devuelve {"nodes": [...], "edges": [...]} o lanza GraphError."""
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise GraphError("nodes y edges deben ser listas")
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise GraphError(f"Máximo {MAX_NODES} nodos y {MAX_EDGES} aristas")
    clean_nodes, by_id = [], {}
    for raw in nodes:
        node = _node(raw)
        if node["id"] in by_id:
            raise GraphError(f"id de nodo repetido: {node['id']}")
        by_id[node["id"]] = node
        clean_nodes.append(node)
    clean_edges, seen = [], set()
    for raw in edges:
        if not isinstance(raw, dict):
            raise GraphError("Cada arista debe ser un objeto con source, target y type")
        src, dst, type_ = raw.get("source"), raw.get("target"), raw.get("type")
        if type_ not in EDGE_TYPES:
            raise GraphError(f"Tipo de arista inválido: {type_!r} (usa {', '.join(EDGE_TYPES)})")
        if src not in by_id or dst not in by_id:
            raise GraphError(f"La arista {src} → {dst} apunta a un nodo que no existe")
        src_kind, dst_kinds = EDGE_TYPES[type_]
        if by_id[src]["kind"] != src_kind or by_id[dst]["kind"] not in dst_kinds:
            raise GraphError(f"{type_} va de {src_kind} a {' o '.join(dst_kinds)}: {src} → {dst} no es válida")
        if src == dst:
            raise GraphError(f"Arista de un nodo a sí mismo: {src}")
        if (src, dst, type_) not in seen:
            seen.add((src, dst, type_))
            clean_edges.append({"source": src, "target": dst, "type": type_})
    return {"nodes": clean_nodes, "edges": clean_edges}


def slugify(text: str, taken: set[str] | None = None) -> str:
    """id válido y único a partir de un nombre: «Validar tarjeta» → «validar-tarjeta»."""
    plain = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in plain if not unicodedata.combining(c)).lower()
    base = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:56] or "nodo"
    slug, n = base, 2
    while taken is not None and slug in taken:
        slug, n = f"{base}-{n}", n + 1
    if taken is not None:
        taken.add(slug)
    return slug


def node_map(graph: dict) -> dict[str, dict]:
    return {n["id"]: n for n in graph.get("nodes", [])}


def constraints(graph: dict) -> list[dict]:
    return [n for n in graph.get("nodes", []) if n["kind"] == "constraint"]


def existing_names(graph: dict, kind: str) -> list[str]:
    """Nombres de funcionalidades o entidades que el sistema ya tiene: su trabajo es reuso."""
    return [n["name"] for n in graph.get("nodes", []) if n["kind"] == kind and n.get("status") == "existente"]


def targets_of(graph: dict, constraint_id: str) -> list[str]:
    return [e["target"] for e in graph.get("edges", []) if e["type"] == "applies_to" and e["source"] == constraint_id]


def summary(graph: dict) -> dict:
    counts = {k: 0 for k in NODE_KINDS}
    for n in graph.get("nodes", []):
        counts[n["kind"]] += 1
    return {**counts, "edges": len(graph.get("edges", []))}


def render(graph: dict) -> str:
    """Texto compacto del grafo para la instrucción de un agente (ids estables para citarlos)."""
    nodes = node_map(graph)
    lines = ["FUNCIONALIDADES:"]
    lines += [f"- [{n['id']}] {n['name']} ({n['status']})" + (f": {n['description']}" if n.get("description") else "")
              for n in nodes.values() if n["kind"] == "feature"] or ["- (ninguna)"]
    lines.append("ENTIDADES DE DATOS:")
    lines += [f"- [{n['id']}] {n['name']} ({n['status']})" for n in nodes.values() if n["kind"] == "entity"] or ["- (ninguna)"]
    lines.append("RESTRICCIONES (NFR):")
    for n in constraints(graph):
        applies = ", ".join(nodes[t]["name"] for t in targets_of(graph, n["id"])) or "todo el sistema"
        lines.append(f"- [{n['id']}] {n['name']} · {n['category']} · severidad {n['severity']} · aplica a: {applies}. "
                     f"Regla: {n['rule']}")
    if not constraints(graph):
        lines.append("- (ninguna)")
    relations = [e for e in graph.get("edges", []) if e["type"] != "applies_to"]
    if relations:
        lines.append("RELACIONES:")
        lines += [f"- {nodes[e['source']]['name']} {e['type']} {nodes[e['target']]['name']}" for e in relations]
    return "\n".join(lines)


__all__ = [
    "CATEGORIES", "EDGE_TYPES", "GraphError", "NODE_KINDS", "SEVERITIES", "STATUSES",
    "constraints", "existing_names", "node_map", "render", "slugify", "summary", "targets_of", "validate",
]
