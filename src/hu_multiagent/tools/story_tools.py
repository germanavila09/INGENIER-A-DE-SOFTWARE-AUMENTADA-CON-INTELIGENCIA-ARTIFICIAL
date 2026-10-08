"""Descubrimiento de historias de usuario en documentos normalizados.

Formatos estructurados: JSON / YAML (una historia, lista, o {"user_stories": [...]}),
CSV / XLSX (una fila por historia) y Markdown o texto (secciones '## US-001 ...').
Los nombres de campo aceptan sinónimos en español e inglés.
"""

from __future__ import annotations

import re

from ..models.common import stable_hash
from ..models.project import NormalizedDocument
from ..models.user_story import UserStory

STORY_ID_RE = re.compile(r"\b((?:US|HU)-\d{1,4})\b", re.IGNORECASE)
NARRATIVE_RES = [
    re.compile(r"como\s+(?:un[a]?\s+)?(?P<role>.+?)\s*,?\s+quiero\s+(?P<need>.+?)\s*,?\s+para\s+(?:que\s+)?(?P<benefit>.+?)(?:\.|$)", re.I | re.S),
    re.compile(r"as\s+an?\s+(?P<role>.+?)\s*,?\s+i\s+want\s+(?:to\s+)?(?P<need>.+?)\s*,?\s+so\s+that\s+(?P<benefit>.+?)(?:\.|$)", re.I | re.S),
]

SYN = {
    "story_id": ("story_id", "id", "codigo", "código", "historia_id", "key"),
    "title": ("title", "titulo", "título", "nombre", "name", "summary", "resumen"),
    "epic": ("epic", "epica", "épica"),
    "role": ("role", "rol", "como", "as_a"),
    "need": ("need", "quiero", "necesidad", "i_want", "want"),
    "benefit": ("benefit", "para", "beneficio", "so_that"),
    "description": ("description", "descripcion", "descripción", "historia", "narrative", "narrativa", "story"),
    "acceptance_criteria": ("acceptance_criteria", "criterios", "criterios_aceptacion", "criterios_de_aceptacion",
                            "criterios de aceptación", "criterios de aceptacion", "acceptance criteria", "ac"),
    "business_rules": ("business_rules", "reglas", "reglas_negocio", "reglas de negocio"),
    "depends_on": ("depends_on", "dependencias", "dependencies", "depende_de", "depende de"),
    "estimate": ("estimate", "story_points", "puntos", "estimacion", "estimación", "sp"),
    "status": ("status", "estado"),
    "priority": ("priority", "prioridad"),
    "approved": ("approved", "aprobada", "aprobado"),
}
APPROVED_STATUS = {"aprobada", "aprobado", "approved", "done", "terminada", "cerrada"}


def _norm_key(k: str) -> str:
    return re.sub(r"\s+", " ", str(k).strip().lower())


def _get(d: dict, field: str):
    keys = {_norm_key(k): v for k, v in d.items()}
    for syn in SYN[field]:
        if syn in keys and keys[syn] not in (None, ""):
            return keys[syn]
    return None


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    parts = re.split(r"\n|;|\|", str(v))
    return [p.strip(" -•*\t") for p in parts if p.strip(" -•*\t")]


def _parse_narrative(text: str) -> dict:
    for rx in NARRATIVE_RES:
        m = rx.search(text or "")
        if m:
            return {k: " ".join(v.split()) for k, v in m.groupdict().items()}
    return {}


def _prioridad(v) -> str:
    t = str(v or "").strip().lower()
    if t in ("alta", "high", "1", "must", "crítica", "critica"):
        return "Alta"
    if t in ("media", "medium", "2", "should"):
        return "Media"
    if t in ("baja", "low", "3", "could"):
        return "Baja"
    return ""


def story_from_dict(d: dict, doc: NormalizedDocument, project_id: str) -> UserStory | None:
    sid = _get(d, "story_id")
    if not sid:
        return None
    desc = str(_get(d, "description") or "")
    narr = _parse_narrative(desc)
    status = str(_get(d, "status") or "")
    approved_v = _get(d, "approved")
    approved = (str(approved_v).lower() in ("true", "1", "si", "sí", "yes")) if approved_v is not None else status.lower() in APPROVED_STATUS
    est = _get(d, "estimate")
    try:
        estimate = float(est) if est is not None else None
    except (TypeError, ValueError):
        estimate = None
    return UserStory(
        story_id=str(sid).strip().upper(),
        project_id=project_id,
        title=str(_get(d, "title") or ""),
        epic=str(_get(d, "epic") or ""),
        role=str(_get(d, "role") or narr.get("role", "")),
        need=str(_get(d, "need") or narr.get("need", "")),
        benefit=str(_get(d, "benefit") or narr.get("benefit", "")),
        description=desc,
        acceptance_criteria=_as_list(_get(d, "acceptance_criteria")),
        business_rules=_as_list(_get(d, "business_rules")),
        depends_on=[x.upper() for x in _as_list(_get(d, "depends_on"))],
        estimate=estimate,
        status=status,
        approved=approved,
        priority=_prioridad(_get(d, "priority")),
        source=doc.uri,
        source_format=doc.format,
        signature=doc.signature,
    )


def _from_structured(doc: NormalizedDocument, project_id: str) -> list[UserStory]:
    data = doc.structured
    if isinstance(data, dict):
        for key in ("user_stories", "historias", "stories", "historias_usuario"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    items = data if isinstance(data, list) else [data]
    return [s for s in (story_from_dict(i, doc, project_id) for i in items if isinstance(i, dict)) if s]


def _from_table(doc: NormalizedDocument, project_id: str) -> list[UserStory]:
    """CSV/XLSX normalizados como 'col | col | col' con la primera fila de encabezados."""
    out = []
    bloques = re.split(r"^\[hoja [^\]]+\]\s*$", doc.text, flags=re.M) if doc.format in ("xlsx", "xlsm") else [doc.text]
    for bloque in bloques:
        filas = [l for l in bloque.splitlines() if l.strip() and not l.startswith("[")]
        if len(filas) < 2:
            continue
        headers = [h.strip() for h in filas[0].split(" | ")]
        for fila in filas[1:]:
            valores = [v.strip() for v in fila.split(" | ")]
            s = story_from_dict(dict(zip(headers, valores)), doc, project_id)
            if s:
                out.append(s)
    return out


def _from_text(doc: NormalizedDocument, project_id: str) -> list[UserStory]:
    """Markdown / texto: cada sección que empieza con un ID de historia."""
    out = []
    lineas = doc.text.splitlines()
    inicios = [i for i, l in enumerate(lineas) if STORY_ID_RE.match(l.strip().lstrip("#*- ").strip())]
    for n, i in enumerate(inicios):
        fin = inicios[n + 1] if n + 1 < len(inicios) else len(lineas)
        cab = lineas[i].strip().lstrip("#*- ").strip()
        sid = STORY_ID_RE.match(cab).group(1).upper()
        title = cab[len(sid):].strip(" :–—-")
        cuerpo = lineas[i + 1:fin]
        campos: dict[str, list[str]] = {}
        actual = "description"
        for l in cuerpo:
            t = l.strip()
            if not t:
                continue
            m = re.match(r"^[*_]*([A-Za-zÁÉÍÓÚáéíóúñÑ ]{3,40})[*_]*\s*:\s*(.*)$", t.lstrip("#-* "))
            clave = _norm_key(m.group(1)) if m else ""
            campo = next((f for f, syns in SYN.items() if clave in syns), None) if m else None
            if campo:
                actual = campo
                if m.group(2).strip():
                    campos.setdefault(actual, []).append(m.group(2).strip())
            else:
                campos.setdefault(actual, []).append(t.lstrip("-*•0123456789.) ").strip())
        d = {"story_id": sid, "title": title}
        for campo, valores in campos.items():
            d[campo] = valores if campo in ("acceptance_criteria", "business_rules", "depends_on") else " ".join(valores)
        if isinstance(d.get("depends_on"), list):
            d["depends_on"] = [x for v in d["depends_on"] for x in STORY_ID_RE.findall(v)] or d["depends_on"]
        s = story_from_dict(d, doc, project_id)
        if s:
            out.append(s)
    return out


def extract_stories(doc: NormalizedDocument, project_id: str) -> list[UserStory]:
    if doc.structured is not None:
        return _from_structured(doc, project_id)
    if doc.format in ("csv", "tsv", "xlsx", "xlsm"):
        return _from_table(doc, project_id)
    return _from_text(doc, project_id)


def discover_stories(docs: list[NormalizedDocument], project_id: str, story_categories) -> tuple[list[UserStory], list[str]]:
    """Historias de los documentos de historias/backlog. Devuelve (historias, avisos de duplicados)."""
    vistos: dict[str, UserStory] = {}
    avisos = []
    for d in docs:
        if d.category not in story_categories:
            continue
        for s in extract_stories(d, project_id):
            if s.story_id in vistos:
                avisos.append(f"{s.story_id} aparece en {vistos[s.story_id].source} y en {s.source}; se usa la primera.")
                continue
            # Firma por historia (no por documento): cambiar una historia no reabre las demás.
            s.signature = stable_hash(s.model_dump(exclude={"signature", "source"}))
            vistos[s.story_id] = s
    return sorted(vistos.values(), key=lambda s: s.story_id), avisos
