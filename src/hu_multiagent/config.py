"""Configuración del sistema multiagente (variables de entorno / .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class HuSettings:
    # Entrada (solo lectura) y salida (resultados, estado y auditoría) separadas.
    input_uri: str = "gs://adk_ing/projects/"
    results_uri: str = "salidas_hu"
    state_uri: str = ""                     # vacío = mismo lugar que results_uri
    model: str = "gemini-2.5-flash"
    # Umbrales de confianza (sección 8)
    confidence_auto: float = 0.85
    confidence_review: float = 0.60
    # Límites para evitar loops y costos descontrolados
    max_stories_per_run: int = 10
    max_reanalysis: int = 2
    max_retries: int = 2
    max_llm_calls_per_story: int = 12
    context_max_chars: int = 30000
    extra: dict = field(default_factory=dict)

    @property
    def effective_state_uri(self) -> str:
        return self.state_uri or self.results_uri


def get_hu_settings() -> HuSettings:
    """Lee el entorno en cada llamada (las pruebas pueden cambiarlo con monkeypatch)."""
    return HuSettings(
        input_uri=os.getenv("HU_INPUT_URI", "gs://adk_ing/projects/"),
        results_uri=os.getenv("HU_RESULTS_URI", "salidas_hu"),
        state_uri=os.getenv("HU_STATE_URI", ""),
        model=os.getenv("HU_MODEL") or os.getenv("ADK_MODEL", "gemini-2.5-flash"),
        confidence_auto=_f("HU_CONFIDENCE_AUTO", 0.85),
        confidence_review=_f("HU_CONFIDENCE_REVIEW", 0.60),
        max_stories_per_run=_i("HU_MAX_HISTORIAS_POR_EJECUCION", 10),
        max_reanalysis=_i("HU_MAX_REANALISIS", 2),
        max_retries=_i("HU_MAX_REINTENTOS", 2),
        max_llm_calls_per_story=_i("HU_MAX_LLAMADAS_LLM_POR_HISTORIA", 12),
        context_max_chars=_i("HU_CONTEXTO_MAX_CARACTERES", 30000),
    )
