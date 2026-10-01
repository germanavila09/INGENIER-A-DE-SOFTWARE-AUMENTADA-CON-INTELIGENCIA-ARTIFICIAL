"""Acceso de solo lectura al bucket de Cloud Storage.

La autenticación usa Application Default Credentials (ADC):
    gcloud auth application-default login
o una cuenta de servicio vía GOOGLE_APPLICATION_CREDENTIALS.
La identidad necesita al menos el rol roles/storage.objectViewer sobre el bucket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from google.cloud import storage

from .config import get_settings


@dataclass(frozen=True)
class ObjectInfo:
    name: str
    size: int
    content_type: str | None
    updated: datetime | None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "size": self.size,
            "content_type": self.content_type,
            "updated": self.updated.isoformat() if self.updated else None,
        }


class BucketReader:
    """Lista, lee y descarga objetos de un bucket de GCS."""

    def __init__(
        self,
        bucket_name: str | None = None,
        project: str | None = None,
        prefix: str | None = None,
        client: storage.Client | None = None,
    ) -> None:
        settings = get_settings()
        self.bucket_name = bucket_name or settings.bucket
        self.prefix = settings.prefix if prefix is None else prefix
        self._client = client or storage.Client(project=project or settings.project)
        self._bucket = self._client.bucket(self.bucket_name)

    # ------------------------------------------------------------------ listar
    def iter_objects(self, prefix: str | None = None) -> Iterator[ObjectInfo]:
        effective = self.prefix if prefix is None else prefix
        for blob in self._client.list_blobs(self.bucket_name, prefix=effective or None):
            if blob.name.endswith("/"):  # marcadores de "carpeta" creados desde la consola
                continue
            yield ObjectInfo(blob.name, blob.size or 0, blob.content_type, blob.updated)

    def list(self, prefix: str | None = None, max_results: int | None = None) -> list[ObjectInfo]:
        out: list[ObjectInfo] = []
        for obj in self.iter_objects(prefix):
            out.append(obj)
            if max_results is not None and len(out) >= max_results:
                break
        return out

    # ------------------------------------------------------------------- leer
    def read_bytes(self, name: str) -> bytes:
        return self._bucket.blob(name).download_as_bytes()

    def read_text(self, name: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(name).decode(encoding)

    # --------------------------------------------------------------- descargar
    def download(self, name: str, dest_dir: str | Path) -> Path:
        target = _safe_target(Path(dest_dir), name)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(name).download_to_filename(str(target))
        return target

    def sync(self, dest_dir: str | Path | None = None, prefix: str | None = None) -> list[Path]:
        """Descarga los objetos que no existen localmente o cambiaron de tamaño."""
        dest = Path(dest_dir) if dest_dir else get_settings().local_dir
        downloaded: list[Path] = []
        for obj in self.iter_objects(prefix):
            target = _safe_target(dest, obj.name)
            if target.exists() and target.stat().st_size == obj.size:
                continue
            downloaded.append(self.download(obj.name, dest))
        return downloaded


def _safe_target(dest_dir: Path, object_name: str) -> Path:
    """Ruta local para un objeto, impidiendo que un nombre con '..' escape de dest_dir."""
    root = dest_dir.resolve()
    target = (root / object_name).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Nombre de objeto fuera del directorio destino: {object_name!r}")
    return target
