"""Cambios integrados desde SmartBacklogIA sobre la base GCP del proyecto:

- estimación por factores (agente LLM que puntúa + cálculo en código + ajuste humano),
- concurrencia: candado por proyecto, decisiones que no se pierden, versión de historia (409),
- lector BPMN seguro, tokens y costo por ejecución, modo demostración (HU_MODEL=simulado).

Todo con el modelo simulado: sin red ni credenciales.
"""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
import time
from pathlib import Path

import hu_fakes
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adk_ing.documentos import EXTENSIONES, extraer
from hu_multiagent import api
from hu_multiagent.agents.aggregator.agent import aggregate
from hu_multiagent.agents.hitl_evaluator.policy import evaluate_hitl
from hu_multiagent.config import HuSettings, resolve_model
from hu_multiagent.models.hitl import HitlLevel
from hu_multiagent.models.project import DocumentCategory
from hu_multiagent.models.state import WorkflowState as S
from hu_multiagent.services import estimation
from hu_multiagent.services.estimation import EstimationError, estimate, from_agent_output
from hu_multiagent.services.state_service import ConcurrentModification, StateService
from hu_multiagent.simulado import ModeloSimulado
from hu_multiagent.tools.project_tools import classify
from hu_multiagent.workflows.engine import HuEngine, set_engine

EJEMPLOS = Path(__file__).resolve().parents[1] / "ejemplos" / "projects"
IA_BAJO = {"datos": 1, "modelo": 1, "evaluacion": 1, "riesgo": 1, "integracion": 1}
IA_ALTO = {k: 5 for k in IA_BAJO}
TRAD_BAJO = {"complejidad": 1, "esfuerzo": 1, "incertidumbre": 1}


def run(coro):
    return asyncio.run(coro)


class Lento(ModeloSimulado):
    """Simulado con latencia configurable para abrir ventanas de concurrencia."""

    espera: float = 0.0

    async def generate_content_async(self, llm_request, stream=False):
        await asyncio.sleep(self.espera)
        async for r in super().generate_content_async(llm_request, stream):
            yield r


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    for k, v in {"GOOGLE_GENAI_USE_VERTEXAI": "TRUE", "GOOGLE_CLOUD_PROJECT": "t", "GOOGLE_CLOUD_LOCATION": "us-central1"}.items():
        monkeypatch.setenv(k, v)
    entrada = tmp_path / "bucket" / "projects"
    shutil.copytree(EJEMPLOS, entrada)
    hu_fakes.LLAMADAS.clear()
    hu_fakes.FALLAS.clear()
    api._jobs.clear()

    def crear(modelo="simulado", **over):
        cfg = HuSettings(input_uri=str(entrada), results_uri=str(tmp_path / "resultados"), **over)
        eng = HuEngine(cfg, model=ModeloSimulado() if modelo == "simulado" else modelo)
        eng.retry_backoff_s = 0
        eng.audit.emit_logs = False
        set_engine(eng)
        return eng

    yield crear
    set_engine(None)


def cliente(eng) -> TestClient:
    set_engine(eng)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


# ============================================================ método de estimación (puro)
def test_ia_minimo_un_punto_sin_spike():
    r = estimate("HU_IA", IA_BAJO)
    assert (r["method"], r["points"], r["range"]) == (estimation.IA_METHOD, 1, {"min": 1, "max": 2})
    assert r["spike"] == {"required": False, "points": 0}


def test_ia_maximo_se_limita_a_13_y_exige_spike():
    r = estimate("HU_IA", IA_ALTO)
    assert r["points"] == 13 and r["range"]["max"] == 13 and r["spike"] == {"required": True, "points": 2}


def test_datos_altos_exigen_spike_y_riesgo_alto_sube_un_nivel():
    assert estimate("HU_IA", {**IA_BAJO, "datos": 4})["spike"]["required"] is True
    base = {"datos": 2, "modelo": 3, "evaluacion": 3, "riesgo": 3, "integracion": 3}
    sin, con = estimate("HU_IA", base), estimate("HU_IA", {**base, "riesgo": 4})
    assert (sin["points"], sin["raw_score"], sin["spike"]["required"]) == (3, 2.75, False)
    assert (con["points"], con["raw_score"], con["spike"]["required"]) == (5, 2.9, True)


def test_tradicional_usa_su_metodo_y_nunca_spike():
    r = estimate("HU-Tradicional", {"complejidad": 5, "esfuerzo": 5, "incertidumbre": 5})
    assert (r["method"], r["points"], r["story_type"]) == (estimation.TRADITIONAL_METHOD, 13, "HU_TRADICIONAL")
    assert r["spike"]["required"] is False


@pytest.mark.parametrize("tipo, factores, mensaje", [
    (None, IA_BAJO, "necesita tipo"),
    ("HU_IA", {**IA_BAJO, "datos": 6}, "entre 1 y 5"),
    ("HU_IA", {**IA_BAJO, "datos": 0}, "entre 1 y 5"),
    ("HU_IA", {**IA_BAJO, "datos": 2.5}, "entero"),
    ("HU_IA", {**IA_BAJO, "datos": True}, "entero"),
    ("HU_IA", {"datos": 1}, "faltan"),
    ("HU_TRADICIONAL", {**TRAD_BAJO, "datos": 1}, "sobran"),
])
def test_factores_invalidos(tipo, factores, mensaje):
    with pytest.raises(EstimationError, match=mensaje):
        estimate(tipo, factores)


def test_salida_del_agente_usa_solo_los_factores_del_tipo():
    salida = {"story_type": "HU_TRADICIONAL", "complejidad": 2, "esfuerzo": 3, "incertidumbre": 2, "datos": 5}
    est, error = from_agent_output(salida)
    assert error == "" and est["factors"] == {"complejidad": 2, "esfuerzo": 3, "incertidumbre": 2}
    est, error = from_agent_output({"story_type": "HU_IA", **IA_BAJO, "riesgo": 7})
    assert est is None and "riesgo" in error            # fuera de rango: no se corrige en silencio
    assert from_agent_output(None) == (None, "")


# ============================================================ agregador y política HITL
def _agg(est_out):
    story = {"story_id": "US-1", "project_id": "P", "acceptance_criteria": ["a", "b"]}
    return aggregate("P", story, hu_fakes.analyst("P", "US-1", False), hu_fakes.architecture("P", "US-1"),
                     hu_fakes.qa("P", "US-1", False), est_out)


def test_estimacion_es_informativa_no_baja_la_confianza_ni_decide():
    base = _agg(None)
    est = {**hu_fakes.estimation("P", "US-1"), "confidence": 0.05}
    agg = _agg(est)
    assert agg.confidence == base.confidence and not agg.conflicts and agg.missing_agents == []
    assert agg.estimation["points"] == 3 and "estimation_agent" in {r.agent for r in agg.responses}
    assert evaluate_hitl(agg).level == HitlLevel.AUTOMATIC


def test_spike_y_estimacion_invalida_piden_revision_sin_detener():
    spike = _agg(hu_fakes.estimation("P", "HU-IA-001"))
    ev = evaluate_hitl(spike)
    assert ev.level == HitlLevel.REVIEW and [r.code for r in ev.reasons] == ["SPIKE_REQUIRED"]
    malo = {**hu_fakes.estimation("P", "US-1"), "esfuerzo": 9}
    ev = evaluate_hitl(_agg(malo))
    assert ev.level == HitlLevel.REVIEW and [r.code for r in ev.reasons] == ["ESTIMATION_INCOMPLETE"]


# ============================================================ estimación en el flujo
def test_flujo_guarda_estimacion_auditoria_y_costo(entorno):
    eng = entorno()
    r = run(eng.analyze_project("PRJ002"))
    assert r["status"] == "COMPLETED"
    store = eng.results_store("PRJ002")
    est = store.read_json("generated/user_stories/US-001/estimation.json")
    assert est["estimate"]["points"] == 3 and est["output"]["story_type"] == "HU_TRADICIONAL" and est["error"] == ""
    assert store.read_json("generated/user_stories/US-001/proposed.json")["estimation"]["points"] == 3
    salidas = [e for e in store.read_jsonl("audit/audit_log.jsonl") if e.get("event") == "agent_output"]
    estim = [e for e in salidas if e["agent"] == "estimation_agent"]
    assert len(estim) == 2 and estim[0]["decision"].startswith("3 pts")
    # tokens y costo: los de razonamiento cuentan como salida; precios de referencia de gemini-2.5-flash
    ultima = eng.state.load("PRJ002").runs[-1]
    assert ultima.tokens_in > 0 and ultima.tokens_out > 0
    assert ultima.cost_usd == pytest.approx((ultima.tokens_in * 0.30 + ultima.tokens_out * 2.50) / 1e6, rel=1e-3)
    assert all(e.get("cost_usd") for e in salidas if e["agent"] in ("story_analyst_agent", "estimation_agent"))


def test_sin_precio_no_hay_costo(entorno):
    eng = entorno(model="otro-modelo")
    run(eng.analyze_project("PRJ002"))
    ultima = eng.state.load("PRJ002").runs[-1]
    assert ultima.tokens_in > 0 and ultima.cost_usd is None


def test_persona_ajusta_la_estimacion_por_api(entorno):
    eng = entorno()
    run(eng.analyze_project("PRJ002"))
    with cliente(eng) as c:
        h = {x["id"]: x for x in c.get("/api/projects/PRJ002/backlog").json()["historias"]}["US-001"]
        assert h["estimacion"]["origen"] == "ia" and h["tipo_origen"] == "ia"
        url = "/api/projects/PRJ002/stories/US-001/estimate"
        assert c.post(url, json={"tipo": "HU_IA", "factores": TRAD_BAJO}).status_code == 400
        assert c.post(url, json={"tipo": "HU_IA", "factores": IA_ALTO, "version": h["version"] - 1}).status_code == 409
        r = c.post(url, json={"tipo": "HU_IA", "factores": {**IA_BAJO, "datos": 4},
                              "comentario": "Hay que etiquetar datos", "version": h["version"]},
                   headers={"X-Usuario": "ana"})
        assert r.status_code == 200 and r.json()["estimacion"]["spike"]["required"] is True
        h2 = {x["id"]: x for x in c.get("/api/projects/PRJ002/backlog").json()["historias"]}["US-001"]
        assert h2["tipo"] == "HU-IA" and h2["tipo_origen"] == "humano" and h2["version"] == h["version"] + 1
        assert h2["estimacion"]["origen"] == "humano" and h2["estimacion"]["agente"]["puntos"] == 3
        assert h2["estimacion"]["decidido_por"] == "ana"
        filas = c.get("/api/projects/PRJ002/export?formato=csv").text.lstrip("﻿").splitlines()
        col = filas[0].split(";")
        us1 = next(f.split(";") for f in filas[1:] if f.startswith("US-001;"))
        assert us1[col.index("puntos")] == str(h2["estimacion"]["puntos"]) and us1[col.index("estimacion_por")] == "persona"
        # al aprobar, la versión aprobada lleva la estimación de la persona
        assert c.post("/api/projects/PRJ002/stories/US-001/decision", json={"decision": "APPROVED"}).status_code == 200
    store = eng.results_store("PRJ002")
    assert store.read_json("generated/user_stories/US-001/approved.json")["estimation"]["decided_by"] == "ana"
    assert any(v.endswith("_human_estimate.json") for v in store.list("generated/user_stories/US-001/versions/"))
    assert any(e.get("event") == "human_estimate" for e in store.read_jsonl("audit/audit_log.jsonl"))


# ============================================================ concurrencia
def test_decision_durante_un_analisis_no_se_pierde_se_rechaza(entorno):
    """Hallazgo 2: antes la decisión se aplicaba y el análisis en curso la pisaba al guardar."""
    lento = Lento()
    eng = entorno(modelo=lento)
    run(eng.analyze_project("PRJ001"))
    did = eng.state.load("PRJ001").stories["US-003"].pending_decision_id

    async def escenario():
        lento.espera = 0.05
        job = asyncio.create_task(eng.analyze_project("PRJ001", force=True))
        await asyncio.sleep(0.02)
        assert eng.busy("PRJ001")
        r = await eng.apply_decision(did, "APPROVED", "desde el chat", decided_by="chat")
        await job
        return r

    r = run(escenario())
    assert r["status"] == "error" and r["codigo"] == "ocupado"
    st = eng.state.load("PRJ001")
    assert st.decisions[did].pending and not st.stories["US-003"].approved_by_human
    # terminado el análisis, la misma decisión se aplica normalmente
    eng.model = ModeloSimulado()
    assert run(eng.apply_decision(did, "APPROVED", "ahora sí", decided_by="chat"))["status"] == "ok"


def test_aprobar_epica_nunca_aprueba_una_historia_de_nivel_2(entorno, monkeypatch):
    """Hallazgo 1: approve_epic con una foto vieja del estado podía aprobar una historia crítica."""
    eng = entorno()
    run(eng.analyze_project("PRJ002"))
    original = hu_fakes.architecture

    def critica(pid, sid):
        out = original(pid, sid)
        if sid == "US-002":
            out.update(critical_changes=["DATA_MIGRATION"], requires_hitl=True, hitl_reason="migración")
        return out

    monkeypatch.setattr("hu_multiagent.simulado.architecture", critica)

    async def escenario():
        job = asyncio.create_task(eng.analyze_project("PRJ002", story_ids=["US-002"], force=True))
        await asyncio.sleep(0)
        mientras = await eng.approve_epic("PRJ002", "Sin épica", "bulk")     # el análisis tiene el candado
        await job
        despues = await eng.approve_epic("PRJ002", "Sin épica", "bulk")
        return mientras, despues

    mientras, despues = run(escenario())
    assert mientras["codigo"] == "ocupado"
    assert despues["aprobadas"] == ["US-001"] and despues["requieren_decision_individual"] == ["US-002"]
    st = eng.state.load("PRJ002")
    assert st.stories["US-002"].state == S.WAITING_FOR_HUMAN and not st.stories["US-002"].approved_by_human
    assert st.pending_decisions()[0].request.story_id == "US-002"


def test_un_proyecto_ocupado_no_bloquea_a_otro(entorno):
    """Hallazgo 8: con el candado global, decidir en PRJ002 esperaba al análisis de PRJ001."""
    lento = Lento()
    eng = entorno(modelo=lento)
    run(eng.analyze_project("PRJ002"))

    async def escenario():
        lento.espera = 0.2
        job = asyncio.create_task(eng.analyze_project("PRJ001"))
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        r = await eng.human_action("PRJ002", "US-001", "APPROVED", decided_by="ana")
        dur = time.monotonic() - t0
        ocupado = eng.busy("PRJ001") and not eng.busy("PRJ002")
        job.cancel()
        try:
            await job
        except asyncio.CancelledError:
            pass
        return r, dur, ocupado

    r, dur, ocupado = run(escenario())
    assert r["status"] == "ok" and dur < 0.15 and ocupado


def test_version_vieja_de_la_historia_se_rechaza(entorno):
    eng = entorno()
    run(eng.analyze_project("PRJ002"))
    v = eng.state.load("PRJ002").stories["US-001"].version
    r = run(eng.human_action("PRJ002", "US-001", "APPROVED", expected_version=v - 1))
    assert r["codigo"] == "conflicto" and r["version_actual"] == v
    with cliente(eng) as c:
        url = "/api/projects/PRJ002/stories/US-001/decision"
        assert c.post(url, json={"decision": "APPROVED", "version": v - 1}).status_code == 409
        assert c.post(url, json={"decision": "APPROVED", "version": v}).status_code == 200
    assert eng.state.load("PRJ002").stories["US-001"].version == v + 1


def test_guardar_sobre_una_version_mas_nueva_falla(tmp_path):
    svc = StateService(str(tmp_path))
    a, b = svc.load("P1"), svc.load("P1")
    svc.save(a)
    b.project_name = "copia vieja"
    with pytest.raises(ConcurrentModification):
        svc.save(b)
    assert svc.load("P1").version == 1 and svc.load("P1").project_name == ""


def test_otro_proceso_escribe_durante_el_analisis_y_el_proyecto_queda_en_error(entorno):
    eng = entorno()
    run(eng.analyze_project("PRJ002"))
    registrar = eng.audit.record
    hecho = []

    def entrometido(entry):  # simula un segundo servidor que guarda el estado a mitad del flujo
        if entry.event == "proposal" and not hecho:
            externo = StateService(eng.settings.effective_state_uri)
            externo.save(externo.load("PRJ002"))
            hecho.append(1)
        return registrar(entry)

    eng.audit.record = entrometido
    r = run(eng.analyze_project("PRJ002", force=True))
    assert hecho and r["status"] == "ERROR" and "otro proceso" in r["error"]
    assert eng.state.load("PRJ002").state == S.ERROR     # queda a la vista, no se pisa nada en silencio


def test_error_inesperado_no_deja_la_historia_a_medias(entorno):
    """Hallazgo 7: una excepción fuera de los reintentos dejaba la historia en VALIDATING y la ejecución abierta."""
    eng = entorno()

    def falla(*a, **k):
        raise ValueError("dato raro en la propuesta")

    eng._decision_request = falla  # US-001 y US-003 de PRJ001 piden decisión → fallan aquí
    r = run(eng.analyze_project("PRJ001"))
    st = eng.state.load("PRJ001")
    assert "dato raro" in r["errores"]["US-001"] and st.stories["US-001"].state == S.ERROR
    assert st.stories["US-002"].state == S.READY_FOR_IMPLEMENTATION       # las demás siguieron
    assert st.runs[-1].finished_at


def test_descubrir_no_reescribe_el_estado_de_proyectos_existentes(entorno):
    eng = entorno()
    run(eng.analyze_project("PRJ002"))
    antes = eng.state.load("PRJ002").version
    eng.discover()
    assert eng.state.load("PRJ002").version == antes


# ============================================================ BPMN, modo demo
BPMN = b"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="d1">
 <process id="p1" name="Publicar indicadores">
  <laneSet><lane id="l1" name="Analista"><flowNodeRef>t1</flowNodeRef></lane></laneSet>
  <startEvent id="s1" name="Llega imagen"/>
  <userTask id="t1" name="Validar nubosidad"><documentation>Rechazar si supera 30%</documentation></userTask>
  <exclusiveGateway id="g1" name="Nubosidad aceptable?"/>
  <sequenceFlow id="f1" sourceRef="s1" targetRef="t1"/>
  <sequenceFlow id="f2" sourceRef="t1" targetRef="g1" name="validada"/>
 </process>
</definitions>"""


def test_bpmn_se_resume_y_rechaza_xxe():
    assert ".bpmn" in EXTENSIONES
    texto = extraer("proceso.bpmn", BPMN).secciones[0].texto
    assert "[userTask] Validar nubosidad (carril: Analista): Rechazar si supera 30%" in texto
    assert "Llega imagen → Validar nubosidad" in texto and "Nubosidad aceptable?" in texto
    xxe = b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]><definitions>&x;</definitions>'
    doc = extraer("malo.bpmn", xxe)
    assert not doc.tiene_texto and "no permitidas" in doc.aviso
    assert classify("documentos/flujo_atencion.bpmn") == DocumentCategory.FUNCTIONAL


def test_modo_demostracion_sin_gemini(entorno, monkeypatch):
    assert isinstance(resolve_model("simulado"), ModeloSimulado) and resolve_model("gemini-2.5-flash") == "gemini-2.5-flash"
    eng = entorno(modelo=None, model="simulado")
    assert isinstance(eng.model, ModeloSimulado)
    r = run(eng.analyze_project("PRJ001"))
    assert r["status"] == "WAITING_FOR_HUMAN" and {d["story_id"] for d in r["esperando_decision_humana"]} >= {"US-003"}
