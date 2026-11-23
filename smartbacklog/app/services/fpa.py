"""Tamaño funcional de una HU en puntos de función IFPUG sin ajustar (UFP, ISO/IEC 20926).

Funciones puras: sin base de datos ni proveedor de IA. El agente (o una persona) identifica y
clasifica las funciones con sus conteos (DET, RET, FTR); este módulo calcula la complejidad y los
puntos. El LLM nunca hace la aritmética. Ver docs/metodo-fpa-finops.md.

Una función de datos (ILF/EIF) se cuenta una sola vez por aplicación: si una HU anterior ya la
contó, llega con `reused=True` y suma 0 puntos (sigue contando como FTR de las transacciones).
"""

from __future__ import annotations

METHOD = "fpa_ifpug_ufp_v1"
VERSION = 1
MAX_FUNCTIONS = 100

DATA_TYPES = ("ILF", "EIF")
TRANSACTION_TYPES = ("EI", "EO", "EQ")
FUNCTION_TYPES = DATA_TYPES + TRANSACTION_TYPES

WEIGHTS = {
    "ILF": {"low": 7, "average": 10, "high": 15},
    "EIF": {"low": 5, "average": 7, "high": 10},
    "EI": {"low": 3, "average": 4, "high": 6},
    "EO": {"low": 4, "average": 5, "high": 7},
    "EQ": {"low": 3, "average": 4, "high": 6},
}

# Matriz de complejidad IFPUG: fila = banda de RET (datos) o FTR (transacciones), columna = banda de DET.
_MATRIX = (
    ("low", "low", "average"),
    ("low", "average", "high"),
    ("average", "high", "high"),
)
# (límites superiores de las bandas de fila, límites superiores de las bandas de DET)
_BANDS = {
    "ILF": ((1, 5), (19, 50)),
    "EIF": ((1, 5), (19, 50)),
    "EI": ((1, 2), (4, 15)),
    "EO": ((1, 3), (5, 19)),
    "EQ": ((1, 3), (5, 19)),
}


class FpaError(ValueError):
    """Componentes de puntos de función inválidos."""


def _band(value: int, limits: tuple[int, int]) -> int:
    if value <= limits[0]:
        return 0
    if value <= limits[1]:
        return 1
    return 2


def complexity(type_: str, det: int, ret_or_ftr: int) -> str:
    rows, cols = _BANDS[type_]
    return _MATRIX[_band(ret_or_ftr, rows)][_band(det, cols)]


def _int(value, field: str, name: str, minimum: int) -> int:
    # bool es subclase de int en Python: se rechaza explícitamente.
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise FpaError(f"'{name}': {field} debe ser un entero ≥ {minimum}")
    return value


def _validate(item) -> dict:
    if not isinstance(item, dict):
        raise FpaError("Cada función debe ser un objeto con name, type y sus conteos")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise FpaError("Cada función necesita 'name'")
    name = name.strip()[:200]
    type_ = item.get("type")
    if type_ not in FUNCTION_TYPES:
        raise FpaError(f"'{name}': type debe ser uno de {', '.join(FUNCTION_TYPES)}")
    det = _int(item.get("det"), "det", name, 1)
    if type_ in DATA_TYPES:
        counts = {"det": det, "ret": _int(item.get("ret"), "ret", name, 1)}
    else:
        counts = {"det": det, "ftr": _int(item.get("ftr", 0), "ftr", name, 0)}
    reused = item.get("reused", False)
    if not isinstance(reused, bool):
        raise FpaError(f"'{name}': reused debe ser true o false")
    clean = {"name": name, "type": type_, **counts, "reused": reused}
    if item.get("rationale"):
        clean["rationale"] = str(item["rationale"])[:500]
    return clean


def analyze(components) -> dict:
    """Valida los componentes y devuelve el análisis completo a guardar en `fp_analysis`."""
    if isinstance(components, dict):
        components = components.get("functions")
    if not isinstance(components, list):
        raise FpaError("fp_components debe ser una lista de funciones")
    if len(components) > MAX_FUNCTIONS:
        raise FpaError(f"Máximo {MAX_FUNCTIONS} funciones por HU; divide la HU")
    functions, by_type, ufp = [], {t: 0 for t in FUNCTION_TYPES}, 0
    for item in components:
        f = _validate(item)
        level = complexity(f["type"], f["det"], f.get("ret", f.get("ftr", 0)))
        points = 0 if f["reused"] else WEIGHTS[f["type"]][level]
        functions.append({**f, "complexity": level, "fp": points})
        by_type[f["type"]] += points
        ufp += points
    return {"method": METHOD, "version": VERSION, "ufp": ufp, "by_type": by_type, "functions": functions}


def data_function_names(analysis: dict | None) -> list[str]:
    """ILF/EIF que una HU contó (no reusados): alimentan el registro del proyecto."""
    if not analysis:
        return []
    return [f["name"] for f in analysis.get("functions", []) if f["type"] in DATA_TYPES and not f["reused"]]


def apply_to_story_data(data: dict) -> dict:
    """Si `data` trae `fp_components`, calcula `function_points` y `fp_analysis`. Devuelve data nueva.

    `fp_notes` (opcional) se guarda dentro del análisis: conceptos y justificación del agente.
    """
    if data.get("fp_components") is None:
        data.pop("fp_components", None)
        data.pop("fp_notes", None)
        return data
    out = dict(data)
    analysis = analyze(out.pop("fp_components"))
    notes = out.pop("fp_notes", None)
    if notes:
        analysis["notes"] = notes
    out["function_points"] = analysis["ufp"]
    out["fp_analysis"] = analysis
    return out


__all__ = [
    "DATA_TYPES",
    "FUNCTION_TYPES",
    "FpaError",
    "METHOD",
    "TRANSACTION_TYPES",
    "WEIGHTS",
    "analyze",
    "apply_to_story_data",
    "complexity",
    "data_function_names",
]
