"""Herramientas de lectura del bucket de proyectos (las usa ProjectDiscoveryAgent).

Se reconocen tres formas de organizar los proyectos dentro de HU_INPUT_URI:

1. ``projects/<carpeta>/…``  (estructura recomendada): un proyecto por carpeta.
2. ``<carpeta>/…`` en la base: un proyecto por carpeta.
3. Archivos sueltos en la base (o dentro de ``projects/``): se agrupan por el
   prefijo del nombre. Ej.: «SERVI _ SINCHI __ Sesión técnica … .docx» → SERVI_SINCHI.

Una «raíz» de proyecto es un texto serializable:
- ``"proyecto_001"`` o ``"projects/proyecto_001"`` → carpeta;
- ``"@SERVI_SINCHI"`` o ``"projects/@SERVI_SINCHI"`` → archivos sueltos agrupados.
No se asume que todos los proyectos tengan las mismas carpetas.
"""

from __future__ import annotations

import re
import unicodedata

from adk_ing.bucket import ObjectInfo

from ..services.storage_service import InputStorage

IGNORED_DIRS = {"generated"}          # salidas del propio sistema: nunca se leen como entrada
CONTAINER_DIRS = {"projects", "proyectos"}
IGNORED_TOP = {"generated", "salidas_hu"}
UNCLASSIFIED = "SIN_PROYECTO"
_SEPARATORS = ("__", " - ", "::", " | ", " – ", " — ")


def loose_key(filename: str) -> str:
    """Clave de proyecto a partir del prefijo del nombre de un archivo suelto."""
    stem = filename.rsplit(".", 1)[0]
    head = None
    for sep in _SEPARATORS:
        if sep in stem:
            head = stem.split(sep, 1)[0]
            break
    if head is None:
        return UNCLASSIFIED
    plano = unicodedata.normalize("NFKD", head)
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    tokens = re.findall(r"[A-Za-z0-9]+", plano)
    return "_".join(t.upper() for t in tokens)[:40] or UNCLASSIFIED


def split_root(root: str) -> tuple[str, str | None]:
    """('carpeta', None) para proyectos-carpeta; ('carpeta_base', 'CLAVE') para sueltos."""
    if "@" in root:
        folder, key = root.rsplit("@", 1)
        return folder.rstrip("/"), key
    return root.rstrip("/"), None


def root_label(root: str) -> str:
    folder, key = split_root(root)
    return key if key else folder.rsplit("/", 1)[-1]


def root_path(root: str, inner: str) -> str:
    folder, _ = split_root(root)
    return f"{folder}/{inner}" if folder else inner


def root_folder_uri(storage: InputStorage, root: str) -> str:
    folder, _ = split_root(root)
    return storage.uri_for(folder + "/") if folder else storage.uri_for("")


def list_project_roots(storage: InputStorage) -> list[str]:
    roots = set()
    for rel, _ in storage.iter_files():
        parts = rel.split("/")
        if any(p.startswith(".") for p in parts) or parts[0] in IGNORED_TOP:
            continue
        if len(parts) == 1:
            roots.add("@" + loose_key(parts[0]))
        elif parts[0].lower() in CONTAINER_DIRS:
            if len(parts) == 2:
                roots.add(f"{parts[0]}/@{loose_key(parts[1])}")
            elif parts[1] not in IGNORED_TOP:
                roots.add(f"{parts[0]}/{parts[1]}")
        else:
            roots.add(parts[0])
    return sorted(roots)


def scan_project_files(storage: InputStorage, root: str) -> list[tuple[str, ObjectInfo]]:
    """(ruta relativa al proyecto, info) de cada archivo del proyecto, sin generated/."""
    folder, key = split_root(root)
    prefix = folder + "/" if folder else ""
    out = []
    for rel, obj in storage.iter_files(prefix):
        inner = rel[len(prefix):]
        parts = inner.split("/")
        if not inner or any(p.startswith(".") for p in parts):
            continue
        if key is None:
            if parts[0] in IGNORED_DIRS:
                continue
        elif len(parts) != 1 or loose_key(inner) != key:
            continue
        out.append((inner, obj))
    return out


def read_project_file(storage: InputStorage, root: str, inner_path: str) -> bytes:
    return storage.read_bytes(root_path(root, inner_path))
