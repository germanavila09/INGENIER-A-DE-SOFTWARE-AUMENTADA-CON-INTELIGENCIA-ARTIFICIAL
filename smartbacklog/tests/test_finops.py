"""Modelo FinOps (services/finops.py): contexto acumulado, impuesto de contexto, costo, tiempo y API."""

import pytest
from pydantic import ValidationError

from app.services import finops, finops_pricing
from app.services.finops import FinopsParams, StoryInput
from tests.conftest import INGEST_PAYLOAD, login


def story(i, fp, type_="HU_TRADICIONAL", status="borrador", rank=None):
    return StoryInput(code=f"P-{i}", title=f"HU {i}", type=type_, status=status, function_points=fp,
                      priority_rank=i if rank is None else rank, id=i)


def test_first_story_matches_hand_calculation():
    """20 FP con los supuestos por defecto y Claude Opus 5.5 ($4 / $20, caché $0.20 lectura y $5 escritura).

    spec = 1000 + 150·20 = 4000; contexto leído = 0.3 · 20000 = 6000; prefijo = 10000; iteraciones = ceil(6 + 0.4·20) = 14
    escritura = 10000; lectura = 13 · 0.8 · 10000 = 104000; sin caché = 13 · 0.2 · 10000 + 14 · 2000 = 54000
    código = 20 · 50 · 10 · 1.5 = 15000; salida = 15000 · 2 + 14 · 500 = 37000
    costo = (54000·4 + 104000·0.2 + 10000·5 + 37000·20) / 1e6 = 1.0268
    tiempo = 14 · (2 + 20) + 37000 / 60 = 924.67 s
    """
    row = finops.estimate([story(1, 20)])["stories"][0]
    assert row["iterations"] == 14
    assert (row["input_tokens"], row["cache_read_tokens"], row["cache_write_tokens"]) == (54000, 104000, 10000)
    assert row["output_tokens"] == 37000 and row["generated_tokens"] == 15000
    assert row["cost_usd"] == pytest.approx(1.0268)
    assert row["hours"] == pytest.approx(924.667 / 3600, abs=1e-3)
    assert row["context_tax_usd"] == 0  # la primera HU parte del contexto base: sin impuesto


def test_context_grows_with_generated_code_and_tax_compounds():
    result = finops.estimate([story(i, 10) for i in range(1, 9)])
    rows = result["stories"]
    contexts = [r["context_before_tokens"] for r in rows]
    assert contexts == [20_000 + 7_500 * k for k in range(8)]  # cada HU de 10 FP agrega 7500 tokens
    taxes = [r["context_tax_usd"] for r in rows]
    assert taxes[0] == 0 and all(b > a for a, b in zip(taxes, taxes[1:]))
    # Impuesto total superlineal: duplicar el backlog más que duplica el impuesto.
    half = finops.estimate([story(i, 10) for i in range(1, 5)])["totals"]["context_tax_usd"]
    assert result["totals"]["context_tax_usd"] > 2 * half
    assert result["totals"]["final_context_tokens"] == 20_000 + 7_500 * 8


def test_order_exclusions_and_unsized_stories():
    stories = [story(1, 10, rank=3), story(2, 5, rank=1), story(3, None, rank=2), story(4, 50, status="descartada"),
               story(5, 8, rank=None)]
    stories[4].priority_rank = None
    result = finops.estimate(stories)
    assert [r["code"] for r in result["stories"]] == ["P-2", "P-3", "P-1", "P-5"]  # prioridad, vacías al final
    unsized = result["stories"][1]
    assert unsized["sized"] is False and "cost_usd" not in unsized
    assert result["stories"][2]["context_before_tokens"] == result["stories"][1]["context_before_tokens"]
    assert result["totals"]["unsized_stories"] == ["P-3"] and result["totals"]["function_points"] == 23
    assert result["warnings"] and "sin puntos" in result["warnings"][0]


def test_scenarios_pert_cache_and_ia_factor():
    stories = [story(1, 15), story(2, 15, "HU_IA")]
    result = finops.estimate(stories)
    sc = result["scenarios"]
    assert sc["low"]["cost_usd"] < sc["expected"]["cost_usd"] < sc["high"]["cost_usd"]
    assert sc["pert"]["cost_usd"] == pytest.approx(
        (sc["low"]["cost_usd"] + 4 * sc["expected"]["cost_usd"] + sc["high"]["cost_usd"]) / 6, abs=1e-4)
    trad, ia = result["stories"]
    assert ia["iterations"] == pytest.approx(trad["iterations"] * 1.5, abs=1)
    no_cache = finops.estimate(stories, FinopsParams(cache_hit_ratio=0))
    assert no_cache["totals"]["cost_usd"] > result["totals"]["cost_usd"]
    assert finops.estimate(stories, FinopsParams(scenario="high"))["totals"]["cost_usd"] == sc["high"]["cost_usd"]


def test_window_cap_is_flagged():
    params = FinopsParams(base_context_tokens=500_000, context_read_ratio=1.0, loc_per_fp=1000, tokens_per_loc=100)
    result = finops.estimate([story(1, 20), story(2, 20)], params)
    capped = result["stories"][1]
    assert capped["exceeds_window"] is True
    assert capped["context_read_tokens"] < capped["context_before_tokens"]
    assert result["totals"]["stories_exceeding_window"] == ["P-2"] and len(result["warnings"]) == 1


def test_models_prices_and_overrides():
    sonnet = finops.estimate([story(1, 20)], FinopsParams(model="claude-sonnet-5-5"))
    opus = finops.estimate([story(1, 20)])
    assert sonnet["totals"]["cost_usd"] < opus["totals"]["cost_usd"]
    assert opus["prices"]["as_of"] == finops_pricing.AS_OF and opus["prices"]["input"] == 4.0
    assert "cache_write" in opus["prices"]["derived"]
    custom = finops.estimate([story(1, 20)], FinopsParams(price_output=0, price_input=0, price_cache_read=0, price_cache_write=0))
    assert custom["totals"]["cost_usd"] == 0 and custom["params"]["price_output"] == 0
    with pytest.raises(finops.FinopsError, match="desconocido"):
        finops.estimate([story(1, 1)], FinopsParams(model="claude-3-5-sonnet"))
    with pytest.raises(ValidationError):
        FinopsParams(context_read_ratio=2)


def test_haiku_long_context_price_applies_above_threshold():
    model = finops_pricing.get_model("claude-haiku-5-5")
    assert finops_pricing.prices_for_request(model, 100_000)["input"] == 0.10
    assert finops_pricing.prices_for_request(model, 100_001)["input"] == 0.50


# ---------- API ----------

@pytest.fixture
def sized_demo(client, service_key):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    login(client, "po@demo.co")
    client.patch("/api/v1/projects/DEMO/stories/DEMO-1", json={
        "priority_rank": 1, "fp_components": [{"name": "Usuario", "type": "ILF", "det": 5, "ret": 1},
                                              {"name": "Iniciar sesión", "type": "EI", "det": 3, "ftr": 1}],
    })
    return client


def test_finops_endpoint_csv_and_models(sized_demo):
    res = sized_demo.get("/api/v1/projects/DEMO/finops")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["project"] == "DEMO" and body["method"] == "finops_context_tax_v1"
    assert [r["code"] for r in body["stories"]] == ["DEMO-1", "DEMO-2"]
    assert body["totals"]["function_points"] == 10 and body["totals"]["unsized_stories"] == ["DEMO-2"]

    custom = sized_demo.get("/api/v1/projects/DEMO/finops?model=claude-haiku-5-5&scenario=low&loc_per_fp=30").json()
    assert custom["model"]["id"] == "claude-haiku-5-5" and custom["scenario"] == "low"
    assert custom["params"]["loc_per_fp"] == 30
    assert custom["totals"]["cost_usd"] < body["totals"]["cost_usd"]
    assert sized_demo.get("/api/v1/projects/DEMO/finops?context_read_ratio=3").status_code == 422
    assert sized_demo.get("/api/v1/projects/DEMO/finops?model=gpt").status_code == 422

    csv = sized_demo.get("/api/v1/projects/DEMO/finops/export?format=csv")
    assert csv.status_code == 200, csv.text
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("order,code,title")
    assert 'filename="DEMO-finops-claude-opus-5-5-expected.csv"' in csv.headers["content-disposition"]

    models = sized_demo.get("/api/v1/finops/models").json()
    assert models["as_of"] == finops_pricing.AS_OF and "claude-opus-5-5" in models["models"]


def test_reports_page_shows_finops_table(sized_demo):
    from tests.test_agents import web_login

    web_login(sized_demo, "po@demo.co")
    page = sized_demo.get("/p/DEMO/reports")
    assert page.status_code == 200
    assert "FinOps · generación autónoma" in page.text and "Impuesto de contexto" in page.text
    assert "Claude Opus 5.5" in page.text and "Sin dimensionar: no se costea" in page.text
