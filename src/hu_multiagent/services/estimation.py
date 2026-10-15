"""Estimación de historias de usuario por factores (funciones puras, sin LLM ni almacenamiento).

Método adoptado de SmartBacklogIA (https://github.com/diegodgt02-ai/SmartBacklogIA,
``app/services/estimation.py`` y ``docs/metodo-estimacion-hu-ia.md``). Ver
``docs/metodo_estimacion.md`` para la justificación y las referencias.

Hay un método por tipo de historia:

- ``fibonacci_tradicional_v1`` (HU_TRADICIONAL): complejidad, esfuerzo e incertidumbre.
- ``ia_factores_v1`` (HU_IA): datos, modelo, evaluación, riesgo e integración. Un riesgo
  de 4 o más sube un nivel la estimación; datos o riesgo de 4 o más exigen un spike.

Quién hace qué: el agente de estimación (o una persona) puntúa cada factor de 1 a 5; los
puntos, el rango y el spike los calcula siempre este módulo, así el modelo no puede
inventar un número fuera del método. Cambiar pesos o escala es cambiar el sufijo de versión
del método y el documento.
"""

from __future__ import annotations

import math

HU_IA, HU_TRADICIONAL = "HU_IA", "HU_TRADICIONAL"
STORY_TYPES = (HU_IA, HU_TRADICIONAL)
IA_METHOD = "ia_factores_v1"
TRADITIONAL_METHOD = "fibonacci_tradicional_v1"
FIBONACCI = (1, 2, 3, 5, 8, 13)
SPIKE_POINTS = 2          # timebox de experimentación; se cuenta aparte de la historia
MIN_FACTOR, MAX_FACTOR = 1, 5

WEIGHTS = {
    IA_METHOD: {"datos": 0.25, "modelo": 0.25, "evaluacion": 0.15, "riesgo": 0.15, "integracion": 0.20},
    TRADITIONAL_METHOD: {"complejidad": 0.4, "esfuerzo": 0.4, "incertidumbre": 0.2},
}
METHOD_BY_TYPE = {HU_IA: IA_METHOD, HU_TRADICIONAL: TRADITIONAL_METHOD}
ALL_FACTORS = tuple(dict.fromkeys(k for w in WEIGHTS.values() for k in w))

# Escalas en lenguaje simple (las usan el prompt del agente y la interfaz).
FACTOR_HELP = {
    "complejidad": "reglas de negocio, pantallas o flujos y casos de error (1 trivial · 5 muy complejo)",
    "esfuerzo": "construcción e integración: APIs, base de datos, pruebas (1 horas · 5 semanas)",
    "incertidumbre": "claridad del requisito (1 criterios cerrados · 5 ambiguo o por definir)",
    "datos": "disponibilidad y calidad de datos para entrenar o evaluar (1 listos · 5 no existen o son malos)",
    "modelo": "complejidad del modelo o del prompt (1 clasificación simple · 5 multimodal o razonamiento largo)",
    "evaluacion": "necesidad de validar con métricas o conjunto de referencia (1 verificación directa · 5 evaluación humana extensa)",
    "riesgo": "probabilidad de no alcanzar el resultado: precisión, alucinaciones, sesgo (1 bajo · 5 alto)",
    "integracion": "trabajo tradicional alrededor del modelo: API, UI, seguridad, auditoría (1 mínimo · 5 mucho)",
}


class EstimationError(ValueError):
    """Tipo de historia o factores inválidos para estimar."""


def normalize_type(story_type: str | None) -> str | None:
    """Acepta HU_IA / HU-IA / hu ia y HU_TRADICIONAL / HU-Tradicional; devuelve el valor canónico o None."""
    t = (story_type or "").strip().upper().replace("-", "_").replace(" ", "_")
    return {"HU_IA": HU_IA, "IA": HU_IA, "HU_TRADICIONAL": HU_TRADICIONAL, "TRADICIONAL": HU_TRADICIONAL}.get(t)


def method_for(story_type: str | None) -> str:
    t = normalize_type(story_type)
    if t is None:
        raise EstimationError("La historia necesita tipo HU_IA o HU_TRADICIONAL antes de estimarse.")
    return METHOD_BY_TYPE[t]


def factors_for(story_type: str | None) -> tuple[str, ...]:
    return tuple(WEIGHTS[method_for(story_type)])


def _validate(method: str, factors: dict) -> dict[str, int]:
    expected = WEIGHTS[method]
    if set(factors) != set(expected):
        faltan, sobran = sorted(set(expected) - set(factors)), sorted(set(factors) - set(expected))
        raise EstimationError(f"Factores incompletos o desconocidos para {method}: faltan {faltan}, sobran {sobran}.")
    clean: dict[str, int] = {}
    for name in expected:
        value = factors[name]
        # bool es subclase de int en Python: se rechaza explícitamente.
        if isinstance(value, bool) or not isinstance(value, int) or not MIN_FACTOR <= value <= MAX_FACTOR:
            raise EstimationError(f"El factor '{name}' debe ser un entero entre {MIN_FACTOR} y {MAX_FACTOR}.")
        clean[name] = value
    return clean


def _index(raw: float) -> int:
    # Escala lineal [1, 5] → índice de Fibonacci [0, 5] con redondeo hacia arriba en .5: 1 → 1 pt, 5 → 13 pt.
    return math.floor((raw - MIN_FACTOR) * (len(FIBONACCI) - 1) / (MAX_FACTOR - MIN_FACTOR) + 0.5)


def _clamp(i: int) -> int:
    return max(0, min(len(FIBONACCI) - 1, i))


def estimate(story_type: str | None, factors: dict) -> dict:
    """Calcula la estimación completa: tipo, método, puntos, rango, spike, puntaje bruto y factores validados."""
    method = method_for(story_type)
    clean = _validate(method, factors)
    weights = WEIGHTS[method]
    raw = sum(weights[k] * clean[k] for k in weights)
    idx = _index(raw)
    spike = False
    if method == IA_METHOD:
        if clean["riesgo"] >= 4:
            idx += 1
        spike = clean["datos"] >= 4 or clean["riesgo"] >= 4
    idx = _clamp(idx)
    return {
        "story_type": normalize_type(story_type),
        "method": method,
        "points": FIBONACCI[idx],
        "range": {"min": FIBONACCI[_clamp(idx - 1)], "max": FIBONACCI[_clamp(idx + 1)]},
        "spike": {"required": spike, "points": SPIKE_POINTS if spike else 0},
        "raw_score": round(raw, 2),
        "factors": clean,
    }


def from_agent_output(output: dict | None) -> tuple[dict | None, str]:
    """Convierte la salida del estimation_agent (un campo por factor) en una estimación.

    Devuelve (estimación, error). Sin salida del agente → (None, ""): la historia queda sin
    estimar y no se pide revisión por eso. Solo se usan los factores del tipo elegido; los del
    otro tipo se descartan. No corrige valores fuera de rango: si los hay, no hay estimación y
    el error queda a la vista para que una persona la complete.
    """
    if not output:
        return None, ""
    try:
        names = factors_for(output.get("story_type"))
        factors = {k: output.get(k) for k in names if output.get(k) is not None}
        return estimate(output.get("story_type"), factors), ""
    except EstimationError as exc:
        return None, str(exc)


__all__ = [
    "ALL_FACTORS", "EstimationError", "FACTOR_HELP", "HU_IA", "HU_TRADICIONAL", "IA_METHOD", "STORY_TYPES",
    "TRADITIONAL_METHOD", "WEIGHTS", "estimate", "factors_for", "from_agent_output", "method_for", "normalize_type",
]
