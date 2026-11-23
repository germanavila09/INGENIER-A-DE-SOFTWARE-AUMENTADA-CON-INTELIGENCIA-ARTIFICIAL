"""Carpetas locales de documentos: una subcarpeta por proyecto (documents/<KEY>/).

Un proyecto solo puede leer su propia carpeta. Las rutas se resuelven y se validan
dentro de ella: ni '..', ni symlinks que salgan de la carpeta.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.documents.loaders import SUPPORTED_EXTENSIONS
from app.services.errors import NotFound, Unprocessable

MAX_FILE_BYTES = 20 * 1024 * 1024


@dataclass
class DocumentInfo:
    name: str
    size: int


class FolderSource:
    kind = "folder"

    def __init__(self, root: Path, project_key: str):
        self.root = Path(root).resolve()
        self.project_dir = (self.root / project_key).resolve()
        # El nombre del proyecto no puede escapar de la raíz (la clave ya viene validada, esto es defensa extra).
        if self.root not in self.project_dir.parents:
            raise Unprocessable("Clave de proyecto inválida para la carpeta de documentos")

    @property
    def location(self) -> str:
        return str(self.project_dir)

    def list(self) -> list[DocumentInfo]:
        if not self.project_dir.is_dir():
            return []
        items = []
        for path in sorted(self.project_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                items.append(DocumentInfo(name=path.name, size=path.stat().st_size))
        return items

    def resolve(self, filename: str) -> Path:
        if not filename or Path(filename).name != filename or "\x00" in filename:
            raise Unprocessable("Nombre de archivo inválido (solo el nombre, sin rutas)")
        path = (self.project_dir / filename).resolve()
        if path.parent != self.project_dir or not path.is_file():
            raise NotFound(f"No existe el documento {filename} en la carpeta del proyecto")
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise Unprocessable(f"Formato no soportado: {path.suffix}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise Unprocessable("El archivo supera 20 MB")
        return path

    def read_bytes(self, filename: str) -> bytes:
        return self.resolve(filename).read_bytes()
