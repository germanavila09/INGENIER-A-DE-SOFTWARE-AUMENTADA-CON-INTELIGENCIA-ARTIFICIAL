"""Herramientas del agente de documentos, ligadas a la fuente de UN proyecto (carpeta o bucket).

Son funciones normales (sin ADK): el proveedor ADK las da al agente de documentos, que el
generador usa como AgentTool; el proveedor stub las llama directamente. Como cada closure
solo conoce la fuente de su proyecto, el modelo no puede leer documentos de otro proyecto.

Los errores se devuelven como {"status": "error", ...} para que el modelo pueda reaccionar
(probar otro archivo) en vez de cortar la ejecución.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.services.documents import DocumentSource, load_document_text
from app.services.errors import AppError

DEFAULT_CHUNK = 15_000


def _error(message: str) -> dict:
    return {"status": "error", "mensaje": message[:500]}


@dataclass
class DocumentTools:
    """Estado compartido por las herramientas durante una ejecución."""

    source: DocumentSource
    max_total_chars: int
    chars_served: int = 0
    documents_read: list[str] = field(default_factory=list)
    _cache: dict[str, str] = field(default_factory=dict)

    def _text(self, name: str) -> str:
        if name not in self._cache:
            # Mismo cargador que la generación desde un archivo (PDF, DOCX, BPMN seguro, MD, TXT).
            # El límite por documento es el presupuesto total: ningún documento puede superarlo.
            self._cache[name] = load_document_text(name, self.source.read_bytes(name), self.max_total_chars)
        return self._cache[name]

    def listar_documentos(self) -> dict:
        """Lista los documentos del proyecto (PDF, DOCX, BPMN, MD, TXT) con su tamaño en bytes.

        Returns:
            dict con 'ubicacion' (carpeta o gs://...) y 'documentos' (nombre y tamaño).
        """
        try:
            docs = [{"nombre": d.name, "bytes": d.size} for d in self.source.list()]
        except AppError as exc:
            return _error(exc.message)
        return {"status": "ok", "ubicacion": self.source.location, "total": len(docs), "documentos": docs}

    def leer_documento(self, nombre: str, desde_caracter: int = 0, max_caracteres: int = DEFAULT_CHUNK) -> dict:
        """Lee el texto de un documento del proyecto, por tramos.

        Args:
            nombre: Nombre exacto del documento, tal como lo devuelve listar_documentos.
            desde_caracter: Posición desde donde leer (0 = inicio). Usa 'continuar_desde' de la respuesta anterior.
            max_caracteres: Máximo de caracteres a devolver en esta llamada.

        Returns:
            dict con 'texto' y, si el documento sigue, 'continuar_desde'.
        """
        remaining = self.max_total_chars - self.chars_served
        if remaining <= 0:
            return _error("Se agotó el presupuesto de lectura de esta ejecución; genera las HU con lo ya leído")
        try:
            text = self._text(nombre)
        except AppError as exc:
            return _error(exc.message)
        start = max(int(desde_caracter or 0), 0)
        size = max(1, min(int(max_caracteres or DEFAULT_CHUNK), DEFAULT_CHUNK, remaining))
        chunk = text[start:start + size]
        self.chars_served += len(chunk)
        if nombre not in self.documents_read:
            self.documents_read.append(nombre)
        out = {"status": "ok", "documento": nombre, "total_caracteres": len(text), "desde": start, "texto": chunk}
        if start + len(chunk) < len(text):
            out["continuar_desde"] = start + len(chunk)
        return out

    @property
    def functions(self) -> list[Callable[..., dict]]:
        return [self.listar_documentos, self.leer_documento]


def make_document_tools(source: DocumentSource, max_total_chars: int) -> DocumentTools:
    return DocumentTools(source=source, max_total_chars=max_total_chars)
