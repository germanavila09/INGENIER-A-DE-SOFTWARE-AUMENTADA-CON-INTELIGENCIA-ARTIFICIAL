"""Almacenamiento separado en entrada (solo lectura) y resultados (escritura).

- InputStorage: lee el bucket de proyectos (gs://...) o una carpeta local.
- ProjectStore: escribe y lee SOLO dentro de projects/<project_id>/ en el destino
  de resultados. Es la frontera que impide mezclar información entre proyectos.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from adk_ing.bucket import BucketReader, CarpetaLocal, ObjectInfo, _safe_target

from ..models.common import validate_project_id


def parse_uri(uri: str) -> tuple[str, str, str]:
    """('gcs', bucket, prefix) o ('local', ruta, '')."""
    if uri.startswith("gs://"):
        rest = uri[5:]
        bucket, _, prefix = rest.partition("/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        return "gcs", bucket, prefix
    return "local", uri, ""


# ===================================================================== entrada
class InputStorage:
    """Lectura del bucket (o carpeta) donde viven los proyectos."""

    def __init__(self, uri: str, client=None) -> None:
        self.uri = uri
        kind, loc, prefix = parse_uri(uri)
        self.kind = kind
        if kind == "gcs":
            self.bucket = loc
            self.prefix = prefix
            self._reader = BucketReader(bucket_name=loc, prefix=prefix, client=client)
        else:
            self.bucket = ""
            self.prefix = ""
            self._reader = CarpetaLocal(loc)

    def iter_files(self, subprefix: str = "") -> Iterator[tuple[str, ObjectInfo]]:
        """(ruta relativa a la base, info) de cada archivo bajo la base + subprefix."""
        full = self.prefix + subprefix
        for obj in self._reader.iter_objects(full or None):
            rel = obj.name[len(self.prefix):] if self.prefix else obj.name
            if rel:
                yield rel, obj

    def read_bytes(self, rel: str) -> bytes:
        return self._reader.read_bytes(self.prefix + rel)

    def exists(self, rel: str) -> bool:
        if self.kind == "gcs":
            return self._reader._bucket.blob(self.prefix + rel).exists()
        return _safe_target(self._reader.raiz, rel).exists()

    def write_bytes(self, rel: str, data: bytes, content_type: str | None = None) -> str:
        """Agrega un archivo a la entrada. Solo lo usa la carga de documentos del front (SPB):
        el flujo de análisis nunca escribe aquí. Requiere permiso de escritura en el bucket."""
        p = PurePosixPath(rel)
        if not rel or p.is_absolute() or ".." in p.parts or any(x.startswith(".") for x in p.parts):
            raise ValueError(f"Ruta de entrada inválida: {rel!r}")
        if self.kind == "gcs":
            self._reader._bucket.blob(self.prefix + rel).upload_from_string(
                data, content_type=content_type or "application/octet-stream")
        else:
            target = _safe_target(self._reader.raiz, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return self.uri_for(rel)

    def uri_for(self, rel: str) -> str:
        if self.kind == "gcs":
            return f"gs://{self.bucket}/{self.prefix}{rel}"
        return str(Path(self._reader.raiz) / rel)

    @property
    def description(self) -> str:
        return self.uri


# ================================================================== resultados
class _LocalBackend:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError(f"Ruta fuera del destino de resultados: {key}")
        return p

    def read(self, key: str) -> bytes | None:
        p = self._p(key)
        return p.read_bytes() if p.exists() else None

    def write(self, key: str, data: bytes) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def list(self, prefix: str) -> list[str]:
        base = self._p(prefix) if prefix else self.root
        if not base.exists():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())

    def uri(self, key: str) -> str:
        return str(self._p(key))


class _GcsBackend:
    def __init__(self, bucket: str, prefix: str, client=None) -> None:
        from google.cloud import storage

        self.bucket_name = bucket
        self.prefix = prefix
        self._client = client or storage.Client()
        self._bucket = self._client.bucket(bucket)

    def read(self, key: str) -> bytes | None:
        blob = self._bucket.blob(self.prefix + key)
        return blob.download_as_bytes() if blob.exists() else None

    def write(self, key: str, data: bytes) -> None:
        ctype = "application/json" if key.endswith((".json", ".jsonl")) else "text/plain; charset=utf-8"
        self._bucket.blob(self.prefix + key).upload_from_string(data, content_type=ctype)

    def exists(self, key: str) -> bool:
        return self._bucket.blob(self.prefix + key).exists()

    def list(self, prefix: str) -> list[str]:
        full = self.prefix + prefix
        return sorted(b.name[len(self.prefix):] for b in self._client.list_blobs(self.bucket_name, prefix=full))

    def uri(self, key: str) -> str:
        return f"gs://{self.bucket_name}/{self.prefix}{key}"


_locks: dict[str, threading.Lock] = {}


def make_backend(uri: str, client=None):
    kind, loc, prefix = parse_uri(uri)
    return _GcsBackend(loc, prefix, client) if kind == "gcs" else _LocalBackend(loc)


class ProjectStore:
    """Vista del destino de resultados limitada a projects/<project_id>/ (context_namespace)."""

    def __init__(self, backend, project_id: str) -> None:
        self.project_id = validate_project_id(project_id)
        self._b = backend
        self.namespace = f"projects/{project_id}/"
        self._lock = _locks.setdefault(f"{id(backend)}:{project_id}", threading.Lock())

    def _key(self, rel: str) -> str:
        p = PurePosixPath(rel)
        if p.is_absolute() or ".." in p.parts or not rel:
            raise ValueError(f"Ruta relativa inválida: {rel!r}")
        return self.namespace + p.as_posix()

    # --------------------------------------------------------------- básicos
    def read_json(self, rel: str, default: Any = None) -> Any:
        data = self._b.read(self._key(rel))
        return json.loads(data) if data is not None else default

    def write_json(self, rel: str, obj: Any, overwrite: bool = True) -> str:
        key = self._key(rel)
        if not overwrite and self._b.exists(key):
            raise FileExistsError(f"No se sobrescribe {key}")
        self._b.write(key, _dumps(obj).encode("utf-8"))
        return self._b.uri(key)

    def write_text(self, rel: str, text: str) -> str:
        key = self._key(rel)
        self._b.write(key, text.encode("utf-8"))
        return self._b.uri(key)

    def exists(self, rel: str) -> bool:
        return self._b.exists(self._key(rel))

    def list(self, rel_prefix: str = "") -> list[str]:
        keys = self._b.list(self.namespace + rel_prefix)
        return [k[len(self.namespace):] for k in keys if k.startswith(self.namespace)]

    def uri(self, rel: str) -> str:
        return self._b.uri(self._key(rel))

    def append_jsonl(self, rel: str, obj: Any) -> None:
        key = self._key(rel)
        with self._lock:
            previo = self._b.read(key) or b""
            linea = json.dumps(_plain(obj), ensure_ascii=False, default=_default) + "\n"
            self._b.write(key, previo + linea.encode("utf-8"))

    def read_jsonl(self, rel: str) -> list[dict]:
        data = self._b.read(self._key(rel))
        if not data:
            return []
        return [json.loads(l) for l in data.decode("utf-8").splitlines() if l.strip()]

    # ------------------------------------------------------------- versiones
    def write_version(self, rel_dir: str, story_id: str, label: str, obj: Any) -> tuple[int, str]:
        """Escribe <story>_v<N>_<label>.json con N siguiente; nunca sobrescribe."""
        with self._lock:
            existentes = self.list(rel_dir.rstrip("/") + "/")
            nums = []
            for k in existentes:
                name = PurePosixPath(k).name
                if name.startswith(f"{story_id}_v"):
                    try:
                        nums.append(int(name[len(story_id) + 2:].split("_", 1)[0]))
                    except ValueError:
                        pass
            n = max(nums, default=0) + 1
            uri = self.write_json(f"{rel_dir.rstrip('/')}/{story_id}_v{n}_{label}.json", obj, overwrite=False)
            return n, uri


def _plain(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _default(o: Any) -> Any:
    """Serializa modelos Pydantic anidados (p. ej. dentro de un dict)."""
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    return str(o)


def _dumps(obj: Any) -> str:
    return json.dumps(_plain(obj), ensure_ascii=False, indent=2, default=_default)
