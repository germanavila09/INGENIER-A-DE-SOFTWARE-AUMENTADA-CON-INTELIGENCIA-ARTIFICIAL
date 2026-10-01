"""Configuración leída de variables de entorno (o de un archivo .env en la raíz)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Busca un .env desde el directorio actual hacia arriba (no sobrescribe variables ya definidas).
load_dotenv(find_dotenv(usecwd=True), override=False)

DEFAULT_PROJECT = "servi-modelos-ia-dev"
DEFAULT_BUCKET = "adk_ing"


@dataclass(frozen=True)
class Settings:
    project: str
    bucket: str
    prefix: str
    local_dir: Path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        project=os.getenv("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT),
        bucket=os.getenv("GCS_BUCKET", DEFAULT_BUCKET),
        prefix=os.getenv("GCS_PREFIX", ""),
        local_dir=Path(os.getenv("LOCAL_DATA_DIR", "data/raw")),
    )
