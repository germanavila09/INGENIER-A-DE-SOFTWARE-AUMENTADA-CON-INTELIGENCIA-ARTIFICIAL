"""Grafo de contexto (validación, ciclo HITL), tamaño no funcional, violaciones y bloqueo de la etapa 3."""

import pytest

from app.config import settings
from app.services import context_graph, finops, nfr
from app.services.finops import StoryInput
from tests.conftest import INGEST_PAYLOAD, login, hitl_approve_all

GRAPH = {
    "nodes": [
        {"id": "auth-jwt", "kind": "feature", "name": "Autenticación JWT", "status": "existente"},
        {"id": "recomendaciones", "kind": "feature", "name": "Recomendaciones en inicio"},
        {"id": "autenticacion", "kind": "entity", "name": "Autenticación", "status": "existente"},
        {"id": "perfil", "kind": "entity", "name": "Perfil de comportamiento", "status": "planificado"},
        {"id": "privacidad-datos", "kind": "constraint", "name": "Datos locales", "category": "privacidad",
         "severity": "critica", "rule": "Los datos de usuarios no salen de servidores propios",
         "keywords": ["perfil", "comportamiento", "API externa"]},
        {"id": "carga-inicio", "kind": "constraint", "name": "Inicio en 1.2 s", "category": "rendimiento",
         "severity": "media", "rule": "La pantalla de inicio renderiza en menos de 1.2 segundos",
         "keywords": ["pantalla de inicio"]},
    ],
    "edges": [
        {"source": "recomendaciones", "target": "perfil", "type": "uses"},
        {"source": "recomendaciones", "target": "auth-jwt", "type": "depends_on"},
        {"source": "privacidad-datos", "target": "perfil", "type": "applies_to"},
        {"source": "carga-inicio", "target": "recomendaciones", "type": "applies_to"},
    ],
}


# ---------- grafo (puro) ----------

def test_validate_normalizes_graph():
    g = context_graph.validate(GRAPH["nodes"], GRAPH["edges"] + [GRAPH["edges"][0]])  # arista repetida
    assert len(g["edges"]) == 4
    privacy = context_graph.node_map(g)["privacidad-datos"]
    assert privacy["keywords"] == ["api externa", "comportamiento", "perfil"]
    assert context_graph.node_map(g)["recomendaciones"]["status"] == "planificado"  # por defecto
    assert context_graph.existing_names(g, "entity") == ["Autenticación"]
    assert context_graph.targets_of(g, "privacidad-datos") == ["perfil"]
    text = context_graph.render(g)
    assert "[privacidad-datos]" in text and "aplica a: Perfil de comportamiento" in text
    assert context_graph.summary(g) == {"feature": 2, "entity": 2, "constraint": 2, "edges": 4}


@pytest.mark.parametrize("nodes,edges,message", [
    ([{"id": "Mal Id", "kind": "feature", "name": "x"}], [], "id de nodo"),
    ([{"id": "a", "kind": "feature", "name": "x"}, {"id": "a", "kind": "entity", "name": "y"}], [], "repetido"),
    ([{"id": "a", "kind": "otro", "name": "x"}], [], "kind"),
    ([{"id": "c", "kind": "constraint", "name": "x", "category": "privacidad", "severity": "critica"}], [], "rule"),
    ([{"id": "c", "kind": "constraint", "name": "x", "category": "magia", "severity": "alta", "rule": "r"}], [], "category"),
    ([{"id": "a", "kind": "feature", "name": "x", "status": "quizas"}], [], "status"),
    ([{"id": "a", "kind": "feature", "name": "x"}], [{"source": "a", "target": "b", "type": "uses"}], "no existe"),
    ([{"id": "a", "kind": "feature", "name": "x"}, {"id": "e", "kind": "entity", "name": "y"}],
     [{"source": "e", "target": "a", "type": "uses"}], "uses va de feature"),
    ([{"id": "a", "kind": "feature", "name": "x"}], [{"source": "a", "target": "a", "type": "relates"}], "Tipo de arista"),
])
def test_validate_rejects_invalid_graphs(nodes, edges, message):
    with pytest.raises(context_graph.GraphError, match=message):
        context_graph.validate(nodes, edges)


def test_slugify_is_unique_and_valid():
    taken = set()
    assert context_graph.slugify("Validar tarjeta con la pasarela", taken) == "validar-tarjeta-con-la-pasarela"
    assert context_graph.slugify("Validar tarjeta con la pasarela", taken) == "validar-tarjeta-con-la-pasarela-2"
    assert context_graph.slugify("¿Qué?", set()) == "que"


# ---------- tamaño no funcional (puro) ----------

def test_nfr_points_by_severity_unknown_ids_and_waiver_carry_over():
    g = context_graph.validate(GRAPH["nodes"], GRAPH["edges"])
    report = {
        "impacted_features": [{"node_id": "recomendaciones", "how": "La crea"}, {"node_id": "no-existe"}],
        "existing_entities": ["autenticacion", "perfil"],  # perfil está planificado: no es reuso
        "triggered_constraints": [{"constraint_id": "carga-inicio", "reason": "Pantalla de inicio"},
                                  {"constraint_id": "inventada"}],
        "violations": [{"constraint_id": "privacidad-datos", "reason": "Envía perfiles a OpenAI"}],
        "summary": "Viola privacidad.",
    }
    a = nfr.analyze(g, 3, report)
    assert a["nfr_points"] == 4 + 10  # media + crítica (la violación también cuenta como activada)
    assert [c["constraint_id"] for c in a["constraints"]] == ["carga-inicio", "privacidad-datos"]
    assert [e["id"] for e in a["existing_entities"]] == ["autenticacion"]
    assert a["unknown_ids"] == ["no-existe", "inventada"] and a["graph_version"] == 3
    assert [v["status"] for v in a["violations"]] == ["open"] and nfr.open_violations(a)

    waived = {**a, "violations": [{**a["violations"][0], "status": "waived", "waiver": {"by": "Paula", "justification": "x"}}]}
    again = nfr.analyze(g, 3, report, previous=waived)
    assert again["violations"][0]["status"] == "waived" and not nfr.open_violations(again)


def test_finops_charges_nfr_points():
    base = finops.estimate([StoryInput("P-1", "HU", "HU_TRADICIONAL", "borrador", 20, 1, 1)])
    with_nfr = finops.estimate([StoryInput("P-1", "HU", "HU_TRADICIONAL", "borrador", 20, 1, 1, nfr_points=14)])
    a, b = base["stories"][0], with_nfr["stories"][0]
    assert b["iterations"] == a["iterations"] + 5  # ceil(14 + 0.3·14) = 19 frente a 14
    assert b["generated_tokens"] == a["generated_tokens"] + 14 * 750
    assert b["cost_usd"] > a["cost_usd"] and with_nfr["totals"]["nfr_points"] == 14


# ---------- API: ciclo del grafo (HITL) ----------

MEETING = """# Plataforma de recomendaciones

## Necesidades

- El administrador quiere recomendaciones personalizadas en la pantalla de inicio.

## Restricciones

- Los datos personales y el perfil de comportamiento no salen de servidores propios (privacidad).
- La pantalla de inicio debe cargar en menos de 1.2 segundos.
"""


@pytest.fixture
def ctx_demo(client, service_key, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "agent_provider", "stub")
    monkeypatch.setattr(settings, "agent_execution", "inline")
    monkeypatch.setattr(settings, "agent_docs_dir", str(tmp_path))
    (tmp_path / "DEMO").mkdir()
    (tmp_path / "DEMO" / "reunion.md").write_text(MEETING, encoding="utf-8")
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    return client


def test_context_lifecycle_extract_edit_approve(ctx_demo, service_key):
    c = ctx_demo
    login(c, "po@demo.co")
    run = c.post("/api/v1/projects/DEMO/agents/extract-context", json={}).json()
    assert run["status"] == "succeeded", run
    assert run["kind"] == "extract_context" and run["result"]["version"] == 1
    assert run["result"]["documents_read"] == ["reunion.md"]

    overview = c.get("/api/v1/projects/DEMO/context").json()
    assert overview["approved"] is None and overview["draft"]["status"] == "draft"
    draft = overview["draft"]
    kinds = {n["kind"] for n in draft["nodes"]}
    assert kinds == {"feature", "constraint"} and draft["created_by"] == "agent:stub"
    privacy = next(n for n in draft["nodes"] if n["kind"] == "constraint" and n["category"] == "privacidad")
    assert privacy["severity"] == "critica"

    # Solo una persona aprueba; developer y stakeholder no editan ni aprueban.
    headers = {"X-API-Key": service_key}
    assert c.post("/api/v1/projects/DEMO/context/draft/approve", json={}, headers=headers).status_code == 403
    for user in ("dev@demo.co", "stake@demo.co"):
        login(c, user)
        assert c.post("/api/v1/projects/DEMO/context/draft/approve", json={}).status_code == 403
        assert c.put("/api/v1/projects/DEMO/context/draft", json=GRAPH).status_code == 403
    login(c, "po@demo.co")

    bad = c.put("/api/v1/projects/DEMO/context/draft", json={"nodes": [{"id": "x", "kind": "feature"}], "edges": []})
    assert bad.status_code == 422
    edited = c.put("/api/v1/projects/DEMO/context/draft", json={**GRAPH, "notes": "Ajuste del arquitecto"}).json()
    assert edited["version"] == 1 and edited["log"][-1]["action"] == "edited"

    approved = c.post("/api/v1/projects/DEMO/context/draft/approve", json={"notes": "Revisado"}).json()
    assert approved["status"] == "approved" and approved["reviewed_by"] == "Paula PO"
    assert c.post("/api/v1/projects/DEMO/context/draft/approve", json={}).status_code == 422  # sin borrador

    # Una extracción nueva no toca la aprobada hasta que se apruebe; luego la reemplaza.
    c.post("/api/v1/projects/DEMO/agents/extract-context", json={})
    overview = c.get("/api/v1/projects/DEMO/context").json()
    assert overview["approved"]["version"] == 1 and overview["draft"]["version"] == 2
    c.post("/api/v1/projects/DEMO/context/draft/reject", json={"notes": "No"})
    assert c.get("/api/v1/projects/DEMO/context/versions/2").json()["status"] == "rejected"
    assert c.get("/api/v1/projects/DEMO/context").json()["approved"]["version"] == 1


def _approve_graph(c):
    c.put("/api/v1/projects/DEMO/context/draft", json=GRAPH)
    c.post("/api/v1/projects/DEMO/context/draft/approve", json={})


VIOLATING = {
    "title": "Recomendaciones con OpenAI",
    "need": "enviar el perfil de comportamiento de los usuarios a una API externa (OpenAI) en la nube",
    "benefit": "mostrar recomendaciones en la pantalla de inicio",
    "acceptance_criteria": "- [ ] Muestra recomendaciones al cargar",
    "priority_rank": 2,
}


def test_sizing_with_graph_flags_violation_blocks_stage_3_until_waived(ctx_demo, service_key):
    c = ctx_demo
    login(c, "po@demo.co")
    _approve_graph(c)
    c.patch("/api/v1/projects/DEMO/stories/DEMO-1", json={"priority_rank": 1})
    c.patch("/api/v1/projects/DEMO/stories/DEMO-2", json=VIOLATING)

    run = c.post("/api/v1/projects/DEMO/agents/size-backlog", json={}).json()
    assert run["status"] == "succeeded", run
    assert run["result"]["context_graph_version"] == 1
    assert run["result"]["open_violations"] == {"DEMO-2": ["privacidad-datos"]}
    assert run["result"]["registry"][0] == "Autenticación"  # sembrado con lo existente

    first = c.get("/api/v1/projects/DEMO/stories/DEMO-1").json()
    ilf = next(f for f in first["fp_analysis"]["functions"] if f["type"] == "ILF")
    assert ilf["name"] == "Autenticación" and ilf["reused"] is True  # ya existe en el sistema: 0 FP

    story = c.get("/api/v1/projects/DEMO/stories/DEMO-2").json()
    assert story["open_violations"] == ["privacidad-datos"]
    assert story["nfr_points"] == 10 + 4  # privacidad crítica + rendimiento media
    assert story["context_analysis"]["violations"][0]["rule"].startswith("Los datos de usuarios")
    events = [e["event_type"] for e in c.get("/api/v1/projects/DEMO/stories/DEMO-2/events").json()]
    assert "constraint_violations" in events

    hitl_approve_all(c)
    for n in (1, 2):
        assert c.post(f"/api/v1/projects/DEMO/pipeline/{n}/approve", json={}).status_code == 200
    blocked = c.post("/api/v1/projects/DEMO/pipeline/3/approve", json={})
    assert blocked.status_code == 422 and "DEMO-2 (privacidad-datos)" in blocked.json()["detail"]
    assert c.get("/api/v1/projects/DEMO/reports/summary").json()["open_violations"][0]["story"] == "DEMO-2"

    url = "/api/v1/projects/DEMO/stories/DEMO-2/violations/privacidad-datos/waive"
    assert c.post(url, json={"justification": "corto"}).status_code == 422
    assert c.post(url, json={"justification": "Excepción aprobada por el comité"},
                  headers={"X-API-Key": service_key}).status_code == 403
    waived = c.post(url, json={"justification": "Excepción aprobada por el comité de privacidad"}).json()
    assert waived["open_violations"] == []
    assert c.post(url, json={"justification": "Otra vez lo mismo, igual"}).status_code == 422

    # Volver a dimensionar conserva la excepción.
    c.post("/api/v1/projects/DEMO/agents/size-backlog", json={"resize": True})
    again = c.get("/api/v1/projects/DEMO/stories/DEMO-2").json()
    assert again["context_analysis"]["violations"][0]["status"] == "waived" and again["open_violations"] == []
    assert c.post("/api/v1/projects/DEMO/pipeline/3/approve", json={}).status_code == 200

    finops_body = c.get("/api/v1/projects/DEMO/finops").json()
    assert finops_body["totals"]["nfr_points"] == 14


def test_fixing_the_story_resolves_the_violation(ctx_demo):
    c = ctx_demo
    login(c, "po@demo.co")
    _approve_graph(c)
    c.patch("/api/v1/projects/DEMO/stories/DEMO-2", json=VIOLATING)
    c.post("/api/v1/projects/DEMO/stories/DEMO-2/agents/size", json={})
    assert c.get("/api/v1/projects/DEMO/stories/DEMO-2").json()["open_violations"] == ["privacidad-datos"]

    c.patch("/api/v1/projects/DEMO/stories/DEMO-2", json={
        "title": "Recomendaciones locales",
        "need": "calcular recomendaciones con el perfil de comportamiento en servidores propios"})
    c.post("/api/v1/projects/DEMO/stories/DEMO-2/agents/size", json={})
    fixed = c.get("/api/v1/projects/DEMO/stories/DEMO-2").json()
    assert fixed["open_violations"] == [] and fixed["nfr_points"] == 14  # sigue activando ambas restricciones


def test_without_approved_graph_sizing_has_no_context(ctx_demo):
    c = ctx_demo
    login(c, "po@demo.co")
    c.post("/api/v1/projects/DEMO/agents/extract-context", json={})  # solo borrador: no se usa
    c.post("/api/v1/projects/DEMO/stories/DEMO-1/agents/size", json={})
    story = c.get("/api/v1/projects/DEMO/stories/DEMO-1").json()
    assert story["function_points"] is not None
    assert story["context_analysis"] is None and story["nfr_points"] is None


def test_web_context_review_and_waiver(ctx_demo):
    from tests.test_agents import web_login

    c = ctx_demo
    web_login(c, "po@demo.co")
    assert c.post("/p/DEMO/agents/extract-context", follow_redirects=False).status_code == 303
    page = c.get("/p/DEMO/reports").text
    assert "Grafo de contexto" in page and "Aprobar v1" in page
    assert c.post("/p/DEMO/context/approve", data={"notes": "ok"}, follow_redirects=False).status_code == 303
    assert c.get("/api/v1/projects/DEMO/context").json()["approved"]["version"] == 1

    login(c, "po@demo.co")
    c.put("/api/v1/projects/DEMO/context/draft", json=GRAPH)
    c.post("/api/v1/projects/DEMO/context/draft/approve", json={})
    c.patch("/api/v1/projects/DEMO/stories/DEMO-2", json=VIOLATING)
    web_login(c, "po@demo.co")
    c.post("/p/DEMO/stories/DEMO-2/agents/size", follow_redirects=False)
    story_page = c.get("/p/DEMO/stories/DEMO-2").text
    assert "Restricción violada" in story_page and "Registrar excepción" in story_page
    assert "Restricciones violadas sin resolver (1)" in c.get("/p/DEMO/reports").text
    res = c.post("/p/DEMO/stories/DEMO-2/violations/privacidad-datos/waive",
                 data={"justification": "Aprobado por el comité de privacidad"}, follow_redirects=False)
    assert res.status_code == 303
    assert "Excepción registrada" in c.get("/p/DEMO/stories/DEMO-2").text
    assert "Extraer grafo de contexto" in c.get("/p/DEMO/agents").text
