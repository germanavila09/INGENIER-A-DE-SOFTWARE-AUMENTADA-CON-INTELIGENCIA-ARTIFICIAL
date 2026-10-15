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


def _precio(name: str) -> float | None:
    v = os.getenv(name, "").strip()
    return float(v) if v else None


# Precio de lista en USD por millón de tokens (texto) en Vertex AI, como referencia cuando no se
# configura HU_PRECIO_ENTRADA_1M / HU_PRECIO_SALIDA_1M. Los tokens de razonamiento se cobran como salida.
# Verifica los valores vigentes en https://cloud.google.com/vertex-ai/generative-ai/pricing
PRECIOS_REFERENCIA = {"gemini-2.5-flash": (0.30, 2.50)}
MODELOS_SIMULADOS = ("simulado", "demo")


@dataclass(frozen=True)
class HuSettings:
    # Entrada (solo lectura) y salida (resultados, estado y auditoría) separadas.
    input_uri: str = "gs://adk_ing/"
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
    context_max_chars: int = 60000
    # Generación de historias desde actas, notas y propuestas
    max_generated_stories: int = 12
    generation_context_max_chars: int = 150000
    generated_requires_approval: bool = False
    # Costo estimado (USD por millón de tokens); None = usa PRECIOS_REFERENCIA si el modelo está ahí
    price_input_1m: float | None = None
    price_output_1m: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def effective_state_uri(self) -> str:
        return self.state_uri or self.results_uri

    @property
    def prices(self) -> tuple[float, float] | None:
        if self.price_input_1m is not None and self.price_output_1m is not None:
            return self.price_input_1m, self.price_output_1m
        return PRECIOS_REFERENCIA.get(self.model if isinstance(self.model, str) else "")

    def cost_usd(self, tokens_in: int, tokens_out: int) -> float | None:
        """Costo estimado de una llamada; None si no hay precio para el modelo."""
        if not self.prices or (not tokens_in and not tokens_out):
            return None
        pin, pout = self.prices
        return round((tokens_in * pin + tokens_out * pout) / 1_000_000, 6)


def resolve_model(model):
    """Nombre del modelo de Gemini, o el modelo simulado si HU_MODEL=simulado (modo demostración)."""
    if isinstance(model, str) and model.strip().lower() in MODELOS_SIMULADOS:
        from .simulado import ModeloSimulado

        return ModeloSimulado()
    return model


def get_hu_settings() -> HuSettings:
    """Lee el entorno en cada llamada (las pruebas pueden cambiarlo con monkeypatch)."""
    return HuSettings(
        input_uri=os.getenv("HU_INPUT_URI", "gs://adk_ing/"),
        results_uri=os.getenv("HU_RESULTS_URI", "salidas_hu"),
        state_uri=os.getenv("HU_STATE_URI", ""),
        model=os.getenv("HU_MODEL") or os.getenv("ADK_MODEL", "gemini-2.5-flash"),
        confidence_auto=_f("HU_CONFIDENCE_AUTO", 0.85),
        confidence_review=_f("HU_CONFIDENCE_REVIEW", 0.60),
        max_stories_per_run=_i("HU_MAX_HISTORIAS_POR_EJECUCION", 10),
        max_reanalysis=_i("HU_MAX_REANALISIS", 2),
        max_retries=_i("HU_MAX_REINTENTOS", 2),
        max_llm_calls_per_story=_i("HU_MAX_LLAMADAS_LLM_POR_HISTORIA", 12),
        context_max_chars=_i("HU_CONTEXTO_MAX_CARACTERES", 60000),
        max_generated_stories=_i("HU_MAX_HISTORIAS_GENERADAS", 12),
        generation_context_max_chars=_i("HU_CONTEXTO_GENERACION_MAX_CARACTERES", 150000),
        generated_requires_approval=os.getenv("HU_GENERADAS_REQUIEREN_APROBACION", "false").lower() in ("1", "true", "si", "sí"),
        price_input_1m=_precio("HU_PRECIO_ENTRADA_1M"),
        price_output_1m=_precio("HU_PRECIO_SALIDA_1M"),
    )
