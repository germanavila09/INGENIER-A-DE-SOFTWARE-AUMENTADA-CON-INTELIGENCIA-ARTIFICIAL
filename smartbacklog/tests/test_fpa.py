"""Puntos de función IFPUG sin ajustar (services/fpa.py) y su uso en la API como corrección humana (HITL)."""

import pytest

from app.services import fpa
from tests.conftest import INGEST_PAYLOAD, login

# (tipo, det, ret o ftr, complejidad esperada): bordes de cada banda de la matriz IFPUG.
MATRIX_CASES = [
    # ILF/EIF: RET 1 | 2–5 | 6+ ; DET 1–19 | 20–50 | 51+
    ("ILF", 19, 1, "low"), ("ILF", 20, 1, "low"), ("ILF", 51, 1, "average"),
    ("ILF", 19, 2, "low"), ("ILF", 20, 5, "average"), ("ILF", 51, 5, "high"),
    ("ILF", 1, 6, "average"), ("ILF", 20, 6, "high"), ("EIF", 51, 6, "high"),
    # EI: FTR 0–1 | 2 | 3+ ; DET 1–4 | 5–15 | 16+
    ("EI", 4, 0, "low"), ("EI", 15, 1, "low"), ("EI", 16, 1, "average"),
    ("EI", 4, 2, "low"), ("EI", 5, 2, "average"), ("EI", 16, 2, "high"),
    ("EI", 4, 3, "average"), ("EI", 5, 3, "high"),
    # EO/EQ: FTR 0–1 | 2–3 | 4+ ; DET 1–5 | 6–19 | 20+
    ("EO", 5, 1, "low"), ("EO", 19, 1, "low"), ("EO", 20, 1, "average"),
    ("EQ", 5, 3, "low"), ("EQ", 6, 2, "average"), ("EQ", 20, 3, "high"),
    ("EQ", 5, 4, "average"), ("EO", 6, 4, "high"),
]


@pytest.mark.parametrize("type_,det,other,expected", MATRIX_CASES)
def test_complexity_matrix_boundaries(type_, det, other, expected):
    assert fpa.complexity(type_, det, other) == expected


def test_ifpug_weights():
    assert fpa.WEIGHTS == {
        "ILF": {"low": 7, "average": 10, "high": 15},
        "EIF": {"low": 5, "average": 7, "high": 10},
        "EI": {"low": 3, "average": 4, "high": 6},
        "EO": {"low": 4, "average": 5, "high": 7},
        "EQ": {"low": 3, "average": 4, "high": 6},
    }


def test_analyze_sums_ufp_and_reused_data_functions_count_zero():
    result = fpa.analyze([
        {"name": "Pago", "type": "ILF", "det": 12, "ret": 2},                   # low → 7
        {"name": "Cliente", "type": "ILF", "det": 30, "ret": 3, "reused": True},  # average, pero reusada → 0
        {"name": "Pasarela", "type": "EIF", "det": 5, "ret": 1},                 # low → 5
        {"name": "Registrar pago", "type": "EI", "det": 8, "ftr": 2},            # average → 4
        {"name": "Comprobante", "type": "EO", "det": 25, "ftr": 4},              # high → 7
        {"name": "Consultar pago", "type": "EQ", "det": 4, "ftr": 1},            # low → 3
    ])
    assert result["ufp"] == 26
    assert result["by_type"] == {"ILF": 7, "EIF": 5, "EI": 4, "EO": 7, "EQ": 3}
    reused = next(f for f in result["functions"] if f["name"] == "Cliente")
    assert reused["complexity"] == "average" and reused["fp"] == 0
    assert result["method"] == "fpa_ifpug_ufp_v1"
    assert fpa.data_function_names(result) == ["Pago", "Pasarela"]


@pytest.mark.parametrize("bad,message", [
    ("no-lista", "lista"),
    ([{"type": "ILF", "det": 3, "ret": 1}], "name"),
    ([{"name": "X", "type": "ABC", "det": 3}], "type"),
    ([{"name": "X", "type": "ILF", "det": 0, "ret": 1}], "det"),
    ([{"name": "X", "type": "ILF", "det": 3}], "ret"),
    ([{"name": "X", "type": "EI", "det": 3, "ftr": -1}], "ftr"),
    ([{"name": "X", "type": "EI", "det": True, "ftr": 1}], "det"),
    ([{"name": "X", "type": "EI", "det": 3, "reused": "si"}], "reused"),
    ([{"name": f"F{i}", "type": "EI", "det": 3} for i in range(101)], "Máximo"),
])
def test_invalid_components_are_rejected(bad, message):
    with pytest.raises(fpa.FpaError, match=message):
        fpa.analyze(bad)


def test_empty_list_is_zero_and_missing_components_change_nothing():
    assert fpa.analyze([])["ufp"] == 0
    assert fpa.apply_to_story_data({"title": "x", "fp_components": None}) == {"title": "x"}


# ---------- API: corrección humana (HITL) ----------

def test_patch_fp_components_recomputes_and_records_history(client, service_key):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    login(client, "po@demo.co")
    components = [
        {"name": "Usuario", "type": "ILF", "det": 5, "ret": 1},
        {"name": "Iniciar sesión", "type": "EI", "det": 3, "ftr": 1, "rationale": "Correo, clave y botón"},
    ]
    res = client.patch("/api/v1/projects/DEMO/stories/DEMO-1", json={"fp_components": components})
    assert res.status_code == 200, res.text
    story = res.json()
    assert story["function_points"] == 10
    assert story["fp_analysis"]["functions"][1]["rationale"] == "Correo, clave y botón"

    events = client.get("/api/v1/projects/DEMO/stories/DEMO-1/events").json()
    assert events[-1]["actor_name"] == "Paula PO" and "function_points" in events[-1]["diff"]

    bad = client.patch("/api/v1/projects/DEMO/stories/DEMO-1", json={"fp_components": [{"name": "X", "type": "ILF", "det": 1}]})
    assert bad.status_code == 422 and "ret" in bad.json()["detail"]
    old = client.patch("/api/v1/projects/DEMO/stories/DEMO-1", json={"estimate_points": 5}).json()
    assert old["function_points"] == 10 and "estimate_points" not in old  # campo retirado: se ignora


def test_ingest_accepts_fp_components(client, service_key):
    payload = {**INGEST_PAYLOAD, "stories": [{
        "external_id": "fp-1", "title": "Consultar pagos",
        "fp_components": [{"name": "Consultar pagos", "type": "EQ", "det": 6, "ftr": 2}],
    }]}
    res = client.post("/api/v1/ingest/projects", json=payload, headers={"X-API-Key": service_key})
    assert res.status_code == 201, res.text
    login(client, "po@demo.co")
    stories = {s["title"]: s for s in client.get("/api/v1/projects/DEMO/stories").json()}
    assert stories["Consultar pagos"]["function_points"] == 4
