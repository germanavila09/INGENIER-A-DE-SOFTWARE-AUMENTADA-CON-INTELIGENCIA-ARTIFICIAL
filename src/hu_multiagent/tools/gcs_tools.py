"""Herramientas de lectura del bucket de proyectos (las usa ProjectDiscoveryAgent).

Estructura esperada (flexible): <base>/<carpeta_proyecto>/{project.yaml, README.md,
requirements/, user_stories/, architecture/, technical/, decisions/, tests/, generated/}.
No se asume que todos los proyectos tengan las mismas carpetas.
"""

from __future__ import annotations

from adk_ing.bucket import ObjectInfo

from ..services.storage_service import InputStorage

IGNORED_DIRS = {"generated"}  # salidas del propio sistema: nunca se leen como entrada


def list_project_roots(storage: InputStorage) -> list[str]:
    """Carpetas de primer nivel bajo la base que contienen al menos un archivo."""
    roots = set()
    for rel, _ in storage.iter_files():
        parts = rel.split("/")
        if len(parts) >= 2 and not parts[0].startswith("."):
            roots.add(parts[0])
    return sorted(roots)


def scan_project_files(storage: InputStorage, root: str) -> list[tuple[str, ObjectInfo]]:
    """(ruta relativa al proyecto, info) de cada archivo del proyecto, sin generated/."""
    out = []
    for rel, obj in storage.iter_files(root.rstrip("/") + "/"):
        inner = rel[len(root.rstrip("/")) + 1:]
        parts = inner.split("/")
        if not inner or parts[0] in IGNORED_DIRS or any(p.startswith(".") for p in parts):
            continue
        out.append((inner, obj))
    return out


def read_project_file(storage: InputStorage, root: str, inner_path: str) -> bytes:
    return storage.read_bytes(f"{root.rstrip('/')}/{inner_path}")
