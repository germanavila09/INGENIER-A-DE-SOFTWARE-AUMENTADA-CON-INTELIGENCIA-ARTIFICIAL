"""HITL como función central: revisión multiagente (stub), niveles, permisos por nivel, decisiones y etapa 1."""

import pytest

from app.agents.ports import (
    ReviewAnalysis,
    ReviewArchitecture,
    ReviewEstimation,
    ReviewOutput,
    ReviewQA,
)
from app.config import settings
from app.services import hitl
from tests.conftest import INGEST_PAYLOAD, PASSWORD, login

API = "/api/v1/projects/DEMO"


@pytest.fixture
def demo(client, service_key, monkeypatch):
    monkeypatch.setattr(settings, "agent_provider", "stub")
    monkeypatch.setattr(settings, "agent_execution", "inline")
    monkeypatch.setattr(settings, "agent_auto_refine", False)
    payload = {**INGEST_PAYLOAD,
               "users": INGEST_PAYLOAD["users"] + [{"email": "sm@demo.co", "name": "Sam SM", "password": PASSWORD}],
               "members": INGEST_PAYLOAD["members"] + [{"email": "sm@demo.co", "role": "scrum_master"}]}
    assert client.post("/api/v1/ingest/projects", json=payload, headers={"X-API-Key": service_key}).status_code == 201
    return client


def decide(client, code, decision, **body):
    return client.post(f"{API}/stories/{code}/hitl/decision", json={"decision": decision, **body})


def story_hitl(client, code):
    return client.get(f"{API}/stories/{code}/hitl").json()


def test_revision_multiagente_y_bandeja(demo):
    login(demo, "po@demo.co")
    box = demo.get(f"{API}/hitl").json()
    assert [s["code"] for s in box["agentes"]] == ["DEMO-1", "DEMO-2"] and box["decidir"] == []

    run = demo.post(f"{API}/hitl/review-pending").json()
    assert run["kind"] == "review_backlog" and run["status"] == "succeeded"
    assert {r["story"]: r["level"] for r in run["result"]["reviewed"]} == {"DEMO-1": 0, "DEMO-2": 2}

    box = demo.get(f"{API}/hitl").json()
    assert [s["code"] for s in box["decidir"]] == ["DEMO-2", "DEMO-1"]  # mayor riesgo primero
    assert box["agentes"] == []

    h2 = story_hitl(demo, "DEMO-2")
    codes = {r["code"] for r in h2["review"]["reasons"]}
    assert {"NO_ACCEPTANCE_CRITERIA", "CRITICAL_AMBIGUITY", "LOW_CONFIDENCE"} <= codes
    assert h2["review"]["questions"][0]["blocking"] is True
    h1 = story_hitl(demo, "DEMO-1")
    assert h1["hitl_state"] == "pendiente" and h1["review"]["estimation"]["method"] == "fibonacci_tradicional_v1"
    assert h1["review"]["agents"]["qa"]["test_cases"]

    # nada que revisar de nuevo
    assert demo.post(f"{API}/hitl/review-pending").status_code == 422


def test_permisos_por_nivel_y_solo_personas(demo, service_key):
    login(demo, "po@demo.co")
    demo.post(f"{API}/hitl/review-pending")

    login(demo, "sm@demo.co")
    res = decide(demo, "DEMO-2", "aprobar")
    assert res.status_code == 403 and "product owner" in res.json()["detail"]
    assert decide(demo, "DEMO-1", "aprobar").status_code == 200   # nivel 0: el scrum master puede

    login(demo, "dev@demo.co")
    assert decide(demo, "DEMO-2", "descartar", comment="no aplica").status_code == 403
    assert decide(demo, "DEMO-2", "comentario", comment="El usuario es el analista de calidad").status_code == 200
    login(demo, "stake@demo.co")
    assert decide(demo, "DEMO-2", "comentario", comment="Se usan las categorías del SINCHI").status_code == 200

    # un servicio con API key nunca decide
    demo.cookies.clear()
    res = demo.post(f"{API}/stories/DEMO-2/hitl/decision", json={"decision": "aprobar"}, headers={"X-API-Key": service_key})
    assert res.status_code == 403


def test_aprobar_con_propuesta_version_y_etapa_1(demo):
    login(demo, "po@demo.co")
    demo.post(f"{API}/hitl/review-pending")
    assert demo.post(f"{API}/pipeline/1/approve").status_code == 422   # HU sin aprobar

    v = story_hitl(demo, "DEMO-2")["version"]
    assert decide(demo, "DEMO-2", "aprobar", expected_version=v - 1).status_code == 409
    assert decide(demo, "DEMO-2", "descartar").status_code == 422      # falta el motivo
    assert decide(demo, "DEMO-2", "descartar", comment="Duplicada con DEMO-1", expected_version=v).status_code == 200
    assert story_hitl(demo, "DEMO-2")["hitl_state"] == "descartada"

    res = decide(demo, "DEMO-1", "aprobar", apply_proposal=True)
    assert res.status_code == 200 and res.json()["status"] == "refinada" and res.json()["hitl_state"] == "aprobada"
    assert decide(demo, "DEMO-1", "aprobar").status_code == 409

    detail = demo.get(f"{API}/stories/DEMO-1").json()
    assert detail["type"] == "HU_TRADICIONAL"
    events = [e["event_type"] for e in demo.get(f"{API}/stories/DEMO-1/events").json()]
    assert "hitl_aprobar" in events and "hitl_reviewed" in events

    assert demo.post(f"{API}/pipeline/1/approve").status_code == 200

    # editar la HU aprobada deja la revisión desactualizada: vuelve a la cola de agentes
    demo.post("/p/DEMO/stories/DEMO-1/edit", data={"title": "Iniciar sesión con correo institucional",
                                                  "criteria": detail["acceptance_criteria"], "type": "HU_TRADICIONAL"})
    assert story_hitl(demo, "DEMO-1")["hitl_state"] == "desactualizada"
    assert [s["code"] for s in demo.get(f"{API}/hitl").json()["agentes"]] == ["DEMO-1"]


def test_pedir_cambios_refina_y_revisa_de_nuevo(demo):
    login(demo, "po@demo.co")
    demo.post(f"{API}/hitl/review-pending")
    first = story_hitl(demo, "DEMO-1")["review"]["id"]
    res = decide(demo, "DEMO-1", "pedir_cambios", comment="Agrega el bloqueo tras 5 intentos fallidos")
    assert res.status_code == 200 and res.json()["refinement_run"]["status"] == "succeeded"
    h = story_hitl(demo, "DEMO-1")
    assert h["review"]["id"] != first and h["hitl_state"] == "pendiente" and h["status"] == "refinada"


def test_falta_info_y_respuesta_bajan_el_nivel(demo):
    login(demo, "po@demo.co")
    demo.post(f"{API}/hitl/review-pending")
    assert story_hitl(demo, "DEMO-2")["level"] == 2
    assert decide(demo, "DEMO-2", "falta_info", comment="¿Quién usa la clasificación?").status_code == 200
    assert story_hitl(demo, "DEMO-2")["hitl_state"] == "falta_info"
    login(demo, "stake@demo.co")
    decide(demo, "DEMO-2", "comentario", comment="La usa el analista de calidad para priorizar")
    login(demo, "po@demo.co")
    assert demo.post(f"{API}/stories/DEMO-2/review").status_code == 202
    h = story_hitl(demo, "DEMO-2")
    assert h["hitl_state"] == "pendiente" and not [q for q in h["review"]["questions"] if q["blocking"]]
    assert "CRITICAL_AMBIGUITY" not in {r["code"] for r in h["review"]["reasons"]}


def test_sin_proveedor_no_se_revisa(demo, monkeypatch):
    monkeypatch.setattr(settings, "agent_provider", "none")
    login(demo, "po@demo.co")
    assert demo.post(f"{API}/hitl/review-pending").status_code == 503


# ---------- política (pura) ----------

def _out(**over):
    base = dict(
        analysis=ReviewAnalysis(summary="ok", invest=dict(independent=5, negotiable=5, valuable=5, estimable=5, small=5, testable=5),
                                improved_criteria=["Muestra error"], confidence=0.95),
        architecture=ReviewArchitecture(summary="ok", status="APPROVED_WITH_OBSERVATIONS", confidence=0.95),
        qa=ReviewQA(summary="ok", status="APPROVED", confidence=0.95),
        estimation=ReviewEstimation(story_type="HU_TRADICIONAL", type_justification="t", complejidad=2, esfuerzo=2,
                                    incertidumbre=1, rationale="r", confidence=0.9),
    )
    base.update(over)
    return ReviewOutput(**base)


STORY = {"title": "x", "acceptance_criteria": "- [ ] Muestra error"}


def test_politica_nivel_0_sin_observaciones():
    r = hitl.evaluate(_out(), STORY)
    assert r["level"] == 0 and r["reasons"] == [] and r["quality"] == 100.0 and r["estimation"]["points"] == 2


def test_cambio_critico_es_nivel_2_aunque_la_confianza_sea_alta():
    arch = ReviewArchitecture(summary="m", critical_changes=["DATA_MIGRATION"], status="APPROVED", confidence=0.99)
    r = hitl.evaluate(_out(architecture=arch), STORY)
    assert r["level"] == 2
    assert {"MIGRATION", "AGENT_CONFLICT"} <= {x["code"] for x in r["reasons"]}


def test_agente_sin_salida_detiene_y_spike_hu_ia():
    est = ReviewEstimation(story_type="HU_IA", type_justification="modelo", datos=5, modelo=3, evaluacion=3, riesgo=4,
                           integracion=2, rationale="r", confidence=0.8)
    r = hitl.evaluate(_out(qa=None, estimation=est, errors={"qa": "ValidationError"}), STORY)
    assert r["level"] == 2 and r["story_type"] == "HU_IA"
    codes = {x["code"] for x in r["reasons"]}
    assert {"MISSING_AGENT_OUTPUT", "SPIKE_REQUIRED"} <= codes
    assert r["estimation"]["spike"]["required"] and r["estimation"]["points"] == 8


def test_historia_generada_y_confianza_media_son_nivel_1():
    a = _out().analysis.model_copy(update={"confidence": 0.7})
    r = hitl.evaluate(_out(analysis=a), STORY, generated=True)
    assert r["level"] == 1 and {"GENERATED_STORY", "MEDIUM_CONFIDENCE"} <= {x["code"] for x in r["reasons"]}


# ---------- interfaz ----------

def test_ui_bandeja_y_decisiones_por_formulario(demo):
    login(demo, "po@demo.co")
    page = demo.get("/p/DEMO/revision")
    assert page.status_code == 200 and "Revisión humana" in page.text and "Revisar con agentes" in page.text

    res = demo.post("/p/DEMO/hitl/review-pending", follow_redirects=False)
    assert res.status_code == 303
    page = demo.get("/p/DEMO/revision").text
    assert "Esperan una decisión" in page and "Nivel 2" in page and "Aprobar" in page
    assert 'class="nav-count"' in page  # contador del menú

    detail = demo.get("/p/DEMO/stories/DEMO-1").text
    assert 'id="revision"' in detail and "caso" in detail and "Estimación" in detail

    assert "Aprobar con la propuesta" in demo.get("/p/DEMO/stories/DEMO-2").text
    before = demo.get(f"{API}/stories/DEMO-2").json()["acceptance_criteria"]
    v = story_hitl(demo, "DEMO-2")["version"]
    res = demo.post("/p/DEMO/stories/DEMO-2/hitl", data={"decision": "aprobar_propuesta", "expected_version": v,
                                                          "next": "/p/DEMO/stories/DEMO-2"}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/p/DEMO/stories/DEMO-2"
    h = story_hitl(demo, "DEMO-2")
    assert h["hitl_state"] == "aprobada" and h["review"]["decisions"][-1]["applied_proposal"] is True
    assert demo.get(f"{API}/stories/DEMO-2").json()["acceptance_criteria"] != before

    # redirección externa bloqueada; motivo obligatorio al descartar
    res = demo.post("/p/DEMO/stories/DEMO-1/hitl", data={"decision": "descartar", "next": "https://evil.example"},
                    follow_redirects=False)
    assert res.headers["location"] == "/p/DEMO/revision"
    assert story_hitl(demo, "DEMO-1")["hitl_state"] == "pendiente"

def test_ui_desarrollador_solo_comenta(demo):
    login(demo, "po@demo.co")
    demo.post(f"{API}/hitl/review-pending")
    login(demo, "dev@demo.co")
    page = demo.get("/p/DEMO/revision").text
    assert "Comentar" in page and "Aprobar sin cambios" not in page and "Decide solo el product owner" in page
