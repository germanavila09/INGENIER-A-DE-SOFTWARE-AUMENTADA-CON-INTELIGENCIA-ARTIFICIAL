"""Convierte documentos (TXT, MD, PDF, DOCX, BPMN) a texto plano/markdown para el agente.

Cada formato tiene su cargador. El BPMN se parsea con defusedxml, que rechaza DTD y
entidades externas (XXE, "billion laughs"). Nunca se usa xml.etree directamente.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import PurePosixPath

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.services.errors import PayloadTooLarge, Unprocessable

SUPPORTED_EXTENSIONS = (".txt", ".md", ".pdf", ".docx", ".bpmn")


def _load_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Unprocessable("El archivo de texto no es UTF-8 válido") from exc


def _load_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"## Página {number}\n\n{text}")
    except (PdfReadError, ValueError) as exc:
        raise Unprocessable(f"PDF inválido: {exc}") from exc
    if not pages:
        raise Unprocessable("El PDF no tiene texto extraíble (¿es un escaneo? Requiere OCR)")
    return "\n\n".join(pages)


def _load_docx(data: bytes) -> str:
    """Word (p. ej. las «Notas de Gemini» de Meet): títulos como markdown, viñetas y tablas.

    python-docx parsea el XML del paquete sin resolver entidades externas.
    """
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise Unprocessable("python-docx no está instalado (revisa requirements.txt)") from exc
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - zip o XML inválido
        raise Unprocessable(f"DOCX inválido: {type(exc).__name__}") from exc
    lines: list[str] = []
    for par in doc.paragraphs:
        text = par.text.strip()
        if not text:
            continue
        style = (par.style.name or "").lower() if par.style is not None else ""
        if style.startswith(("heading", "título", "titulo")):
            level = next((int(c) for c in style if c.isdigit()), 1)
            lines.append(f"{'#' * min(level + 1, 6)} {text}")
        elif "list" in style or "lista" in style:
            lines.append(f"- {text}")
        else:
            lines.append(text)
    for number, table in enumerate(doc.tables, start=1):
        lines.append(f"\n## Tabla {number}")
        for row in table.rows:
            lines.append("| " + " | ".join(cell.text.strip().replace("\n", " ") for cell in row.cells) + " |")
    return "\n".join(lines)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _load_bpmn(data: bytes) -> str:
    try:
        root = SafeET.fromstring(data)
    except (DefusedXmlException, SafeET.ParseError) as exc:
        raise Unprocessable(f"BPMN inválido o no permitido: {exc.__class__.__name__}") from exc

    lanes: dict[str, str] = {}
    for el in root.iter():
        if _local(el.tag) == "lane":
            ref_name = el.get("name") or el.get("id", "")
            for ref in el:
                if _local(ref.tag) == "flowNodeRef" and ref.text:
                    lanes[ref.text.strip()] = ref_name

    processes, tasks, gateways, events, flows = [], [], [], [], []
    for el in root.iter():
        kind = _local(el.tag)
        name = el.get("name") or ""
        doc = next((c.text.strip() for c in el if _local(c.tag) == "documentation" and c.text), "")
        if kind == "process":
            processes.append(name or el.get("id", "proceso"))
        elif kind.endswith("Task") or kind == "task":
            lane = lanes.get(el.get("id", ""), "")
            tasks.append((kind.replace("Task", "") or "task", name or el.get("id", ""), lane, doc))
        elif kind.endswith("Gateway"):
            gateways.append(name or kind)
        elif kind in ("startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent"):
            events.append(name or kind)
        elif kind == "sequenceFlow":
            flows.append((el.get("sourceRef"), el.get("targetRef"), el.get("name") or ""))

    lines = ["# Proceso BPMN"]
    if processes:
        lines += ["", "## Procesos", *[f"- {p}" for p in processes]]
    if lanes:
        lines += ["", "## Participantes", *[f"- {p}" for p in sorted(set(lanes.values())) if p]]
    if tasks:
        lines += ["", "## Actividades"]
        for tipo, nombre, lane, doc in tasks:
            extra = f" (carril: {lane})" if lane else ""
            detail = f": {doc}" if doc else ""
            lines.append(f"- [{tipo}] {nombre}{extra}{detail}")
    if gateways:
        lines += ["", "## Decisiones", *[f"- {g}" for g in gateways]]
    if events:
        lines += ["", "## Eventos", *[f"- {e}" for e in events]]
    if flows:
        lines += ["", "## Flujo"]
        for src, dst, label in flows:
            lines.append(f"- {src} → {dst}" + (f" [{label}]" if label else ""))
    if len(lines) == 1:
        raise Unprocessable("El BPMN no contiene procesos ni actividades reconocibles")
    return "\n".join(lines)


_LOADERS: dict[str, Callable[[bytes], str]] = {
    ".txt": _load_text,
    ".md": _load_text,
    ".pdf": _load_pdf,
    ".docx": _load_docx,
    ".bpmn": _load_bpmn,
}


def load_document_text(filename: str, data: bytes, max_chars: int) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    loader = _LOADERS.get(suffix)
    if loader is None:
        raise Unprocessable(f"Formato no soportado: {suffix or 'sin extensión'}. Usa {', '.join(SUPPORTED_EXTENSIONS)}")
    text = loader(data).strip()
    if not text:
        raise Unprocessable("El documento está vacío")
    if len(text) > max_chars:
        raise PayloadTooLarge(f"El documento tiene {len(text)} caracteres; el máximo es {max_chars}")
    return text
