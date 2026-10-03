"""Pruebas del sistema multiagente con modelo simulado (sin red ni credenciales).

Recorre el caso de prueba PRJ001: descubrimiento, ingesta, análisis por historia,
pausa HITL, decisiones humanas, reanudación, versionado, auditoría y aislamiento.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import hu_fakes
import pytest
from google.genai import types

from hu_multiagent.agents.aggregator.agent import aggregate
from hu_multiagent.agents.common import guardia_de_proyecto
from hu_multiagent.agents.hitl_evaluator.policy import evaluate_hitl
from hu_multiagent.config import HuSettings
from hu_multiagent.models.agent_response import AgentStatus
from hu_multiagent.models.hitl import HitlLevel
from hu_multiagent.models.state import InvalidTransition, ProjectState, StoryRecord, WorkflowState as S
from hu_multiagent.services.state_service import StateService
from hu_multiagent.workflows.engine import HuEngine, set_engine

EJEMPLOS = Path(__file__).resolve().parents[1] / "ejemplos" / "projects"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    entrada = tmp_path / "bucket" / "projects"
    shutil.copytree(EJEMPLOS, entrada)
    hu_fakes.LLAMADAS.clear()
    hu_fakes.FALLAS.clear()

    def crear(**over):
        cfg = HuSettings(input_uri=str(entrada), results_uri=str(tmp_path / "resultados"), **over)
        eng = HuEngine(cfg, model=hu_fakes.ModeloSimulado())
        eng.retry_backoff_s = 0
        eng.audit.emit_logs = False
        set_engine(eng)
        return eng

    yield crear, entrada, tmp_path / "resultados"
    set_engine(None)


# =================================================================== política HITL
def _agg(analysis=None, arch=None, qa=None, story=None):
    story = story or {"story_id": "US-1", "project_id": "P", "acceptance_criteria": ["a", "b"]}
    return aggregate("P", story, analysis or hu_fakes.analyst("P", "US-1", False),
                     arch or hu_fakes.architecture("P", "US-1"), qa or hu_fakes.qa("P", "US-1", False))


def test_todo_bien_es_automatico():
    ev = evaluate_hitl(_agg())
    assert ev.level == HitlLevel.AUTOMATIC and ev.action.value == "AUTO_CONTINUE"


def test_cambio_de_seguridad_detiene_aunque_la_confianza_sea_alta():
    arch = {**hu_fakes.architecture("P", "US-1"), "critical_changes": ["SECURITY_MODEL_CHANGE"], "confidence": 0.99}
    ev = evaluate_hitl(_agg(arch=arch))
    assert ev.level == HitlLevel.APPROVAL_REQUIRED
    assert any(r.code == "SECURITY_CHANGE" and r.critical for r in ev.reasons)


def test_conflicto_architecture_aprueba_y_qa_critico():
    qa = {**hu_fakes.qa("P", "US-1", False), "status": "CRITICAL_RISK"}
    agg = _agg(qa=qa)
    assert agg.conflicts and agg.conflicts[0].severity.value == "CRITICAL"
    ev = evaluate_hitl(agg)
    assert ev.level == HitlLevel.APPROVAL_REQUIRED and {"AGENT_CONFLICT", "CRITICAL_RISK"} <= {r.code for r in ev.reasons}


@pytest.mark.parametrize("conf,nivel", [(0.9, 0), (0.7, 1), (0.5, 2)])
def test_umbrales_de_confianza(conf, nivel):
    analysis = {**hu_fakes.analyst("P", "US-1", False), "confidence": conf}
    assert int(evaluate_hitl(_agg(analysis=analysis)).level) == nivel


def test_historia_aprobada_modificada_requiere_aprobacion():
    analysis = hu_fakes.analyst("PRJ001", "US-004", False)
    ev = evaluate_hitl(_agg(analysis=analysis), story_approved=True)
    assert any(r.code == "APPROVED_STORY_MODIFICATION" for r in ev.reasons) and ev.level == 2


def test_maquina_de_estados_rechaza_transiciones_invalidas(tmp_path):
    svc = StateService(str(tmp_path))
    st = ProjectState(project_id="P1", stories={"US-1": StoryRecord(story_id="US-1")})
    with pytest.raises(InvalidTransition):
        svc.transition_story(st, "US-1", S.READY_FOR_IMPLEMENTATION, "test")
    with pytest.raises(InvalidTransition):
        svc.transition_project(st, S.ANALYZING, "test")
    svc.transition_story(st, "US-1", S.ANALYZING, "test")
    assert svc.load("P1").stories["US-1"].state == S.ANALYZING  # persistido


def test_guardia_impide_datos_de_otro_proyecto():
    class Ctx:
        state = {"project_id": "PRJ001", "current_story": {"story_id": "US-001", "project_id": "PRJ002"}}
    assert "BLOQUEADO" in guardia_de_proyecto(Ctx()).parts[0].text
    Ctx.state["current_story"]["project_id"] = "PRJ001"
    assert guardia_de_proyecto(Ctx()) is None


# ============================================================ flujo completo PRJ001
def test_flujo_completo_con_hitl_y_reanudacion(entorno):
    crear, entrada, resultados = entorno
    eng = crear()

    proyectos = {p["project_id"]: p for p in eng.discover()}
    assert set(proyectos) == {"PRJ001", "PRJ002", "PROYECTO_003"}
    assert proyectos["PROYECTO_003"]["tiene_project_yaml"] is False

    # ---------------- 1.ª ejecución: se detiene en las decisiones críticas
    r = run(eng.analyze_project("PRJ001"))
    assert r["status"] == "WAITING_FOR_HUMAN"
    assert r["listas_para_implementacion"] == ["US-002"]
    esperando = {d["story_id"]: d for d in r["esperando_decision_humana"]}
    assert set(esperando) == {"US-001", "US-003", "US-004"}

    d1 = esperando["US-001"]
    assert d1["status"] == "WAITING_FOR_HUMAN" and d1["decision_id"] == "D-PRJ001-US-001-01"
    assert "¿Cuál es la fórmula exacta de incidencia?" in d1["blocking_questions"]
    assert "¿Cuál es la fuente poblacional?" in d1["blocking_questions"]
    assert set(d1["options"]) == {"APPROVED", "REJECTED", "MODIFIED", "NEEDS_MORE_INFORMATION"}
    assert any("MIGRATION" == x["code"] for x in esperando["US-003"]["reasons"])
    assert any("APPROVED_STORY_MODIFICATION" == x["code"] for x in esperando["US-004"]["reasons"])

    # generated/ del bucket nunca se lee como entrada
    assert all("US-999" not in c["instruction"] for c in hu_fakes.LLAMADAS)
    # el análisis paralelo corrió para cada historia
    assert {(c["agent"], c["story_id"]) for c in hu_fakes.LLAMADAS} >= {
        ("story_analyst_agent", "US-001"), ("architecture_agent", "US-001"), ("qa_agent", "US-001")}

    # ---------------- artefactos y versiones
    gen = resultados / "projects" / "PRJ001" / "generated"
    for f in ["manifest.json", "project_context.json", "reports/project_report.md", "reports/risk_report.json",
              "reports/backlog_report.json"]:
        assert (gen / f).exists(), f
    us1 = gen / "user_stories" / "US-001"
    for f in ["original.json", "analysis.json", "architecture_review.json", "tests.json", "aggregated_review.json",
              "hitl.json", "proposed.json", "hitl_request.json"]:
        assert (us1 / f).exists(), f
    assert sorted(p.name for p in (us1 / "versions").iterdir()) == ["US-001_v1_original.json", "US-001_v2_agent_proposal.json"]
    analysis = json.loads((us1 / "analysis.json").read_text())
    assert analysis["agent_response"]["agent"] == "story_analyst_agent"
    assert analysis["agent_response"]["status"] == AgentStatus.NEEDS_CHANGES.value
    assert analysis["agent_response"]["metadata"]["quality_score"] == pytest.approx(70.0)

    # ---------------- auditoría y estado persistido (otra instancia lo lee igual)
    audit = [json.loads(l) for l in (resultados / "projects/PRJ001/audit/audit_log.jsonl").read_text().splitlines()]
    eventos = {e["event"] for e in audit}
    assert {"run_started", "agent_output", "proposal", "hitl_request", "run_finished"} <= eventos
    salida_analista = [e for e in audit if e["event"] == "agent_output" and e.get("agent") == "story_analyst_agent"]
    assert all(e["input_hash"] and e["output_hash"] and e["tokens"]["total_token_count"] > 0 for e in salida_analista)

    eng2 = crear()
    assert eng2.project_status("PRJ001")["estado"] == "WAITING_FOR_HUMAN"

    # ---------------- 2.ª ejecución sin cambios: nada se reprocesa
    n = len(hu_fakes.LLAMADAS)
    r = run(eng2.analyze_project("PRJ001"))
    assert r["procesadas"] == [] and len(hu_fakes.LLAMADAS) == n and r["status"] == "WAITING_FOR_HUMAN"

    # ---------------- decisiones humanas
    r = run(eng2.apply_decision("D-PRJ001-US-003-01", "APPROVED", "Migrar por lotes según ADR-001", decided_by="german"))
    assert r["status"] == "ok" and r["reanudacion"]["status"] == "WAITING_FOR_HUMAN"
    us3 = gen / "user_stories" / "US-003"
    assert (us3 / "approved.json").exists()
    assert sorted(p.name for p in (us3 / "versions").iterdir())[-1] == "US-003_v3_human_approved.json"

    r = run(eng2.apply_decision("D-PRJ001-US-004-01", "REJECTED", "Se replanteará en el próximo trimestre"))
    assert r["status"] == "ok"
    assert run(eng2.apply_decision("D-PRJ001-US-004-01", "APPROVED"))["status"] == "error"  # ya resuelta

    mods = {"answers": {"¿Cuál es la fórmula exacta de incidencia?": "casos / población * 100000",
                        "¿Cuál es la fuente poblacional?": "Proyecciones DANE"},
            "acceptance_criteria": ["Se muestra la tasa de incidencia por 100.000 habitantes",
                                    "filtrar por municipio", "filtrar por semana epidemiológica"]}
    r = run(eng2.apply_decision("D-PRJ001-US-001-01", "MODIFIED", "Respuestas del equipo", mods))
    reanudado = r["reanudacion"]
    assert reanudado["procesadas"] == ["US-001"]          # solo se reanaliza la historia modificada
    assert reanudado["continuan_con_revision"] == ["US-001"]
    assert reanudado["status"] == "COMPLETED"
    ultima = [c for c in hu_fakes.LLAMADAS if c["agent"] == "story_analyst_agent" and c["story_id"] == "US-001"][-1]
    assert "Proyecciones DANE" in ultima["instruction"]      # la corrección humana llega al analista
    assert sorted(p.name for p in (us1 / "versions").iterdir()) == [
        "US-001_v1_original.json", "US-001_v2_agent_proposal.json",
        "US-001_v3_human_modified.json", "US-001_v4_agent_proposal.json"]

    estado = {h["story_id"]: h["estado"] for h in eng2.project_status("PRJ001")["historias"]}
    assert estado == {"US-001": "READY_FOR_IMPLEMENTATION", "US-002": "READY_FOR_IMPLEMENTATION",
                      "US-003": "READY_FOR_IMPLEMENTATION", "US-004": "REJECTED"}

    # ---------------- trazabilidad: ¿por qué terminó así US-001?
    exp = eng2.explain_story("PRJ001", "US-001")
    rutas = [(t["from_state"], t["to_state"]) for t in exp["transiciones"]]
    assert ("VALIDATING", "WAITING_FOR_HUMAN") in rutas and ("WAITING_FOR_HUMAN", "ANALYZING") in rutas
    assert exp["decisiones"][0]["resolution"]["decision"] == "MODIFIED"
    assert exp["evaluacion_hitl"]["level"] == 1

    # ---------------- cambio en la fuente: solo se reanaliza la historia que cambió
    backlog = entrada / "proyecto_001" / "user_stories" / "backlog.md"
    backlog.write_text(backlog.read_text().replace("comité de vigilancia", "comité departamental"), encoding="utf-8")
    r = run(eng2.analyze_project("PRJ001"))
    assert r["procesadas"] == ["US-002"]


def test_aislamiento_entre_proyectos(entorno):
    crear, *_ = entorno
    eng = crear()
    run(eng.analyze_project("PRJ001"))
    run(eng.analyze_project("PRJ002"))
    prj2 = [c for c in hu_fakes.LLAMADAS if c["project_id"] == "PRJ002"]
    assert prj2 and all("dengue" not in c["instruction"].lower() for c in prj2)
    # mismo ID de historia en dos proyectos, estados independientes
    s1 = {h["story_id"]: h for h in eng.project_status("PRJ001")["historias"]}
    s2 = {h["story_id"]: h for h in eng.project_status("PRJ002")["historias"]}
    assert s1["US-001"]["estado"] == "WAITING_FOR_HUMAN" and s2["US-001"]["estado"] == "READY_FOR_IMPLEMENTATION"
    assert eng.pending_decisions("PRJ002") == []


def test_reintento_ante_error_transitorio_y_error_fatal(entorno):
    crear, *_ = entorno
    eng = crear()
    hu_fakes.FALLAS[("qa_agent", "US-001")] = [RuntimeError("503 UNAVAILABLE")]
    r = run(eng.analyze_project("PRJ002"))
    assert r["status"] == "COMPLETED" and not r["errores"]   # se recuperó con un reintento

    hu_fakes.FALLAS[("story_analyst_agent", "US-001")] = [RuntimeError("403 PERMISSION_DENIED en Vertex AI")]
    r = run(eng.analyze_project("PROYECTO_003"))
    assert r["status"] == "ERROR" and r["recuperable"] is False
    assert eng.project_status("PROYECTO_003")["estado"] == "ERROR"
    r = run(eng.analyze_project("PROYECTO_003"))              # se puede volver a ejecutar tras corregir
    assert r["status"] == "COMPLETED"


def test_limite_de_historias_por_ejecucion(entorno):
    crear, *_ = entorno
    eng = crear(max_stories_per_run=1)
    r = run(eng.analyze_project("PRJ001"))
    assert len(r["procesadas"]) == 1 and len(r["omitidas_por_limite"]) == 3
    assert r["procesadas"] == ["US-001"]  # orden por dependencias: habilitadoras primero


# ================================================== guardia de decisiones humanas
class _Tool:
    def __init__(self, name):
        self.name = name


class _ToolCtx:
    def __init__(self, texto):
        self.state = {}
        self.user_content = types.Content(role="user", parts=[types.Part.from_text(text=texto)])


def test_guardia_hitl_exige_decision_explicita_del_usuario(entorno):
    from hu_multiagent.tools.hitl_tools import guardia_orquestador

    crear, *_ = entorno
    eng = crear()
    run(eng.analyze_project("PRJ001"))
    tool = _Tool("registrar_decision_humana")
    args = {"decision_id": "D-PRJ001-US-003-01", "decision": "APPROVED"}
    assert guardia_orquestador(tool, args, _ToolCtx("¿qué opinas de US-003?"))["status"] == "bloqueado"
    assert guardia_orquestador(tool, args, _ToolCtx("aprueba todo"))["status"] == "bloqueado"  # varias pendientes
    assert guardia_orquestador(tool, args, _ToolCtx("APPROVED D-PRJ001-US-003-01")) is None
    assert guardia_orquestador(_Tool("estado_proyecto"), {}, _ToolCtx("hola")) is None
