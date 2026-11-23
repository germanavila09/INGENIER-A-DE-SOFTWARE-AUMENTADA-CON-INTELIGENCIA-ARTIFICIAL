"""Modelo FinOps: tokens, costo y tiempo de generar el backlog con un agente de código autónomo.

Funciones puras: sin base de datos ni LLM. Entrada: las HU activas con sus puntos de función (UFP).
Salida, por HU y total: huella de contexto del repositorio, tokens, costo USD, «impuesto de contexto»
y horas de reloj. Método `finops_context_tax_v1`; ver docs/metodo-fpa-finops.md.

Idea central: las HU se generan en orden (prioridad). El repositorio crece con cada una, y el agente
vuelve a leer parte de ese repositorio en cada iteración de su ciclo. Por eso el costo de cada HU
crece con las anteriores: ese sobrecosto es el impuesto de contexto (crece ~cuadrático en el total).

Todos los valores por defecto son SUPUESTOS iniciales, no medidas. Se pueden sobrescribir por
petición y se devuelven en el resultado para que cada cifra sea reproducible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.services import finops_pricing

METHOD = "finops_context_tax_v1"
SCENARIOS = ("low", "expected", "high")
Scenario = Literal["low", "expected", "high"]


class FinopsParams(BaseModel):
    """Parámetros del modelo. Los valores por defecto son supuestos a calibrar (sección Calibración del método)."""

    model: str = Field(finops_pricing.DEFAULT_MODEL, description="Modelo objetivo de la tabla de precios")
    scenario: Scenario = "expected"
    # Tamaño del código generado
    loc_per_fp: float = Field(50, gt=0, le=1000, description="Líneas de código por punto de función")
    tokens_per_loc: float = Field(10, gt=0, le=100, description="Tokens por línea de código")
    test_ratio: float = Field(0.5, ge=0, le=5, description="Tokens de pruebas por token de código")
    # Contexto
    base_context_tokens: int = Field(20_000, ge=0, le=500_000, description="Instrucciones, herramientas y andamiaje")
    spec_base_tokens: int = Field(1_000, ge=0, le=100_000, description="Especificación de la HU y plan, fijo")
    spec_tokens_per_fp: float = Field(150, ge=0, le=10_000, description="Especificación adicional por punto de función")
    context_read_ratio: float = Field(0.3, ge=0, le=1, description="Fracción del repositorio que el agente relee por iteración")
    tool_output_tokens_per_iteration: int = Field(2_000, ge=0, le=200_000, description="Salidas de pruebas, lint, búsquedas")
    context_headroom_tokens: int = Field(64_000, ge=0, le=500_000, description="Reserva de la ventana para la respuesta")
    # Ciclo del agente
    base_iterations: float = Field(6, gt=0, le=200)
    iterations_per_fp: float = Field(0.4, ge=0, le=20)
    scenario_low: float = Field(0.5, gt=0, le=1, description="Multiplicador de iteraciones, escenario bajo")
    scenario_high: float = Field(2.0, ge=1, le=10, description="Multiplicador de iteraciones, escenario alto")
    ia_iteration_factor: float = Field(1.5, ge=1, le=10, description="Más iteraciones para HU_IA (evaluación, prompts)")
    # Restricciones no funcionales (tamaño no funcional de services/nfr.py): revisión, pruebas y código de soporte
    iterations_per_nfr_point: float = Field(0.3, ge=0, le=20, description="Iteraciones extra por punto no funcional")
    nfr_tokens_per_point: float = Field(750, ge=0, le=100_000, description="Tokens generados (pruebas, controles) por punto no funcional")
    reasoning_ratio: float = Field(1.0, ge=0, le=20, description="Tokens de razonamiento (se cobran como salida) por token de código")
    output_overhead_per_iteration: int = Field(500, ge=0, le=50_000, description="Llamadas a herramientas y mensajes")
    cache_hit_ratio: float = Field(0.8, ge=0, le=1, description="Fracción del prefijo estable servida desde caché")
    # Tiempo
    output_tps: float = Field(60, gt=0, le=10_000, description="Tokens de salida por segundo")
    ttft_s: float = Field(2.0, ge=0, le=600, description="Segundos hasta el primer token, por iteración")
    tool_seconds_per_iteration: float = Field(20, ge=0, le=3_600, description="Ejecución de pruebas y herramientas")
    # Precios (USD por 1M tokens); vacío = tabla de precios
    price_input: float | None = Field(None, ge=0)
    price_output: float | None = Field(None, ge=0)
    price_cache_read: float | None = Field(None, ge=0)
    price_cache_write: float | None = Field(None, ge=0)


@dataclass
class StoryInput:
    code: str
    title: str
    type: str | None
    status: str
    function_points: float | None
    priority_rank: int | None
    id: int = 0
    nfr_points: float | None = None


class FinopsError(ValueError):
    """Parámetros o modelo inválidos."""


def generation_order(stories: list[StoryInput]) -> list[StoryInput]:
    """HU activas en el orden en que el agente las generaría: prioridad (vacías al final) y luego id."""
    active = [s for s in stories if s.status != "descartada"]
    return sorted(active, key=lambda s: (s.priority_rank is None, s.priority_rank or 0, s.id))


def _iterations(p: FinopsParams, ufp: float, story_type: str | None, scenario: str, nfr: float = 0.0) -> int:
    mult = {"low": p.scenario_low, "expected": 1.0, "high": p.scenario_high}[scenario]
    n = (p.base_iterations + p.iterations_per_fp * ufp + p.iterations_per_nfr_point * nfr) * mult
    if story_type == "HU_IA":
        n *= p.ia_iteration_factor
    return max(1, math.ceil(n))


def _prices(p: FinopsParams, model: dict, input_per_request: float) -> dict:
    prices = finops_pricing.prices_for_request(model, input_per_request)
    for key in ("input", "output", "cache_read", "cache_write"):
        override = getattr(p, f"price_{key}")
        if override is not None:
            prices[key] = override
    return prices


def _story_cost(
    p: FinopsParams, model: dict, ufp: float, story_type: str | None, repo_tokens: float, scenario: str, nfr: float = 0.0,
) -> dict:
    """Costo de generar una HU con el repositorio en `repo_tokens`. Sin estado: se llama dos veces por HU."""
    window = model["context_window"]
    spec = p.spec_base_tokens + p.spec_tokens_per_fp * ufp
    available = max(0.0, window - p.context_headroom_tokens - spec - p.tool_output_tokens_per_iteration)
    wanted = p.context_read_ratio * repo_tokens
    context_read = min(wanted, available)
    n = _iterations(p, ufp, story_type, scenario, nfr)

    # El prefijo estable (contexto leído + especificación) se escribe en caché en la 1ª iteración y se lee
    # en las siguientes con `cache_hit_ratio`; los fallos se cobran como entrada normal. Las salidas de
    # herramientas cambian en cada iteración y nunca se cachean.
    prefix = context_read + spec
    cache_write = prefix
    cache_read = (n - 1) * p.cache_hit_ratio * prefix
    uncached = (n - 1) * (1 - p.cache_hit_ratio) * prefix + n * p.tool_output_tokens_per_iteration

    code = ufp * p.loc_per_fp * p.tokens_per_loc * (1 + p.test_ratio) + nfr * p.nfr_tokens_per_point
    output = code * (1 + p.reasoning_ratio) + n * p.output_overhead_per_iteration

    pr = _prices(p, model, prefix + p.tool_output_tokens_per_iteration)
    cost = (uncached * pr["input"] + cache_read * pr["cache_read"] + cache_write * pr["cache_write"]
            + output * pr["output"]) / 1_000_000
    seconds = n * (p.ttft_s + p.tool_seconds_per_iteration) + output / p.output_tps
    return {
        "iterations": n,
        "context_read_tokens": round(context_read),
        "exceeds_window": wanted > available,
        "input_tokens": round(uncached),
        "cache_read_tokens": round(cache_read),
        "cache_write_tokens": round(cache_write),
        "output_tokens": round(output),
        "generated_tokens": round(code),
        "cost_usd": cost,
        "seconds": seconds,
    }


def _run(p: FinopsParams, model: dict, ordered: list[StoryInput], scenario: str) -> tuple[list[dict], dict]:
    rows, repo = [], float(p.base_context_tokens)
    totals = {k: 0.0 for k in ("cost_usd", "context_tax_usd", "seconds", "input_tokens", "cache_read_tokens",
                               "cache_write_tokens", "output_tokens")}
    for order, s in enumerate(ordered, start=1):
        ufp = float(s.function_points) if s.function_points is not None else None
        nfr = float(s.nfr_points or 0)
        row = {"order": order, "code": s.code, "title": s.title, "type": s.type, "status": s.status,
               "function_points": ufp, "nfr_points": nfr, "sized": ufp is not None, "context_before_tokens": round(repo)}
        if ufp is None:
            # Sin dimensionar: no se puede costear. Se marca y no suma.
            rows.append(row)
            continue
        cost = _story_cost(p, model, ufp, s.type, repo, scenario, nfr)
        # Impuesto de contexto: lo que cuesta de más por el repositorio acumulado de las HU anteriores.
        baseline = _story_cost(p, model, ufp, s.type, float(p.base_context_tokens), scenario, nfr)
        tax = cost["cost_usd"] - baseline["cost_usd"]
        row.update(cost, context_tax_usd=tax, hours=cost["seconds"] / 3600)
        rows.append(row)
        for key in totals:
            totals[key] += row["context_tax_usd"] if key == "context_tax_usd" else cost[key]
        repo += cost["generated_tokens"]
    totals["final_context_tokens"] = round(repo)
    return rows, totals


def _round_row(row: dict) -> dict:
    out = dict(row)
    for key in ("cost_usd", "context_tax_usd"):
        if key in out:
            out[key] = round(out[key], 4)
    if "hours" in out:
        out["hours"] = round(out["hours"], 3)
    out.pop("seconds", None)
    return out


def estimate(stories: list[StoryInput], params: FinopsParams | None = None) -> dict:
    p = params or FinopsParams()
    try:
        model = finops_pricing.get_model(p.model)
    except KeyError as exc:
        raise FinopsError(f"Modelo desconocido: {p.model}. Usa uno de {', '.join(finops_pricing.MODELS)}") from exc
    ordered = generation_order(stories)

    by_scenario = {sc: _run(p, model, ordered, sc) for sc in SCENARIOS}
    rows, totals = by_scenario[p.scenario]
    cost = totals["cost_usd"]
    sized = [r for r in rows if r["sized"]]
    summary = {
        "stories": len(rows),
        "sized_stories": len(sized),
        "unsized_stories": [r["code"] for r in rows if not r["sized"]],
        "function_points": sum(r["function_points"] for r in sized),
        "nfr_points": sum(r["nfr_points"] for r in sized),
        "final_context_tokens": totals["final_context_tokens"],
        "input_tokens": round(totals["input_tokens"]),
        "cache_read_tokens": round(totals["cache_read_tokens"]),
        "cache_write_tokens": round(totals["cache_write_tokens"]),
        "output_tokens": round(totals["output_tokens"]),
        "cost_usd": round(cost, 4),
        "context_tax_usd": round(totals["context_tax_usd"], 4),
        "context_tax_pct": round(100 * totals["context_tax_usd"] / cost, 1) if cost else 0.0,
        "hours": round(totals["seconds"] / 3600, 3),
        "stories_exceeding_window": [r["code"] for r in sized if r["exceeds_window"]],
    }

    scenarios = {sc: {"cost_usd": round(t["cost_usd"], 4), "hours": round(t["seconds"] / 3600, 3)}
                 for sc, (_, t) in by_scenario.items()}
    # Media PERT de tres puntos: (bajo + 4·esperado + alto) / 6.
    scenarios["pert"] = {
        k: round((scenarios["low"][k] + 4 * scenarios["expected"][k] + scenarios["high"][k]) / 6, 4)
        for k in ("cost_usd", "hours")
    }

    warnings = []
    if summary["unsized_stories"]:
        warnings.append(f"{len(summary['unsized_stories'])} HU sin puntos de función no se costearon")
    if summary["stories_exceeding_window"]:
        warnings.append("El contexto leído supera la ventana del modelo en algunas HU: se acotó; "
                        "en la práctica requiere recuperación selectiva o compactación")
    return {
        "method": METHOD,
        "model": {"id": p.model, "label": model["label"], "context_window": model["context_window"]},
        "prices": {"as_of": finops_pricing.AS_OF, "source": finops_pricing.SOURCE,
                   **_prices(p, model, 0), "derived": model.get("derived", [])},
        "params": p.model_dump(),
        "scenario": p.scenario,
        "stories": [_round_row(r) for r in rows],
        "totals": summary,
        "scenarios": scenarios,
        "warnings": warnings,
    }


__all__ = ["FinopsError", "FinopsParams", "METHOD", "StoryInput", "estimate", "generation_order"]
