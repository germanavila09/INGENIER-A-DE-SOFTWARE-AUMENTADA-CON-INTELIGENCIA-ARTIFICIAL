"""Tabla de precios de los modelos objetivo (USD por millón de tokens). Una sola fuente, versionada.

Fuente: precios de primera parte de la API de Anthropic, en caché al 2026-10-06. Los campos en
`derived` no vienen publicados por modelo en esa fuente y se calculan con los multiplicadores
estándar de prompt caching (escritura ≈1.25× entrada con TTL de 5 minutos, lectura ≈0.1× entrada).
Antes de presupuestar, verificar en https://platform.claude.com/docs/en/about-claude/pricing.
Cada precio se puede sobrescribir por petición (ver FinopsParams en services/finops.py).
"""

from __future__ import annotations

AS_OF = "2026-10-06"
SOURCE = "Anthropic API, precios de primera parte (caché de la skill claude-api)"
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10
DEFAULT_MODEL = "claude-opus-5-5"

MODELS: dict[str, dict] = {
    "claude-opus-5-5": {
        "label": "Claude Opus 5.5",
        "input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00,
        "context_window": 1_000_000, "max_output": 128_000,
        "derived": ["cache_write"],
    },
    "claude-sonnet-5-5": {
        "label": "Claude Sonnet 5.5",
        "input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50,
        "context_window": 1_000_000, "max_output": 128_000,
        "derived": ["cache_write"],
    },
    "claude-haiku-5-5": {
        "label": "Claude Haiku 5.5",
        "input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125,
        "context_window": 1_000_000, "max_output": 128_000,
        # Precio por petición con más de 100K tokens de entrada.
        "long_context": {"threshold": 100_000, "input": 0.50, "output": 2.50, "cache_read": 0.05, "cache_write": 0.625},
        "derived": ["cache_read", "cache_write", "long_context.cache_read", "long_context.cache_write"],
    },
}


def get_model(model: str) -> dict:
    if model not in MODELS:
        raise KeyError(model)
    return MODELS[model]


def prices_for_request(model: dict, input_tokens: float) -> dict:
    """Precios aplicables a una petición: algunos modelos cobran más si la entrada supera un umbral."""
    long = model.get("long_context")
    if long and input_tokens > long["threshold"]:
        return {k: long[k] for k in ("input", "output", "cache_read", "cache_write")}
    return {k: model[k] for k in ("input", "output", "cache_read", "cache_write")}


def catalog() -> dict:
    return {"as_of": AS_OF, "source": SOURCE, "default_model": DEFAULT_MODEL, "unit": "USD por 1M tokens", "models": MODELS}
