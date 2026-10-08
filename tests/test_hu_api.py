"""API del Smart Product Backlog (front web/spb): proyectos, carga de documentos con
transcripción de audio, análisis en segundo plano, backlog, decisiones humanas y exportación."""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import io
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

import hu_fakes
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hu_multiagent import api
from hu_multiagent.config import HuSettings
from hu_multiagent.workflows.engine import HuEngine, set_engine

RAIZ = Path(__file__).resolve().parents[1]
NOTAS = "SERVI _ SINCHI __ Sesion tecnica imagenes satelitales - 2026_09_02 - Notas de Gemini.md"
TEXTO = """# SERVI | SINCHI :: Sesión técnica imágenes satelitales (ejemplo ficticio)
Se discutió un agente de IA para publicar indicadores de coberturas de la tierra.
Decisión: los indicadores se publican mensualmente.
"""
USUARIO = {"X-Usuario": quote("Germán Ávila")}   # como lo envía el front (encodeURIComponent)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    for k, v in {"GOOGLE_GENAI_USE_VERTEXAI": "TRUE", "GOOGLE_CLOUD_PROJECT": "t", "GOOGLE_CLOUD_LOCATION": "us-central1"}.items():
        monkeypatch.setenv(k, v)
    raiz = tmp_path / "adk_ing"
    shutil.copytree(RAIZ / "ejemplos" / "projects", raiz / "projects")
    (raiz / NOTAS).write_text(TEXTO, encoding="utf-8")
    hu_fakes.LLAMADAS.clear()
    hu_fakes.FALLAS.clear()
    api._jobs.clear()
    eng = HuEngine(HuSettings(input_uri=str(raiz), results_uri=str(tmp_path / "out")), model=hu_fakes.ModeloSimulado())
    eng.retry_backoff_s = 0
    eng.audit.emit_logs = False
    set_engine(eng)

    async def transcriptor(data, mime, nombre):
        assert mime == "audio/mpeg"
        return "Hablante 1: acordamos alertas tempranas de deforestación cada semana."

    monkeypatch.setattr(api, "TRANSCRIPTOR", transcriptor)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, raiz, eng
    set_engine(None)


def esperar(client, pid, timeout=30):
    fin = time.time() + timeout
    while time.time() < fin:
        st = client.get(f"/api/projects/{pid}/status").json()
        if st["trabajo"]["estado"] != "en_curso":
            return st
        time.sleep(0.05)
    raise AssertionError("el trabajo no terminó")


def test_crear_proyecto_y_subir_documentos(entorno):
    client, raiz, _ = entorno
    r = client.post("/api/projects", json={"nombre": "Monitoreo Amazonía", "descripcion": "Piloto"}, headers=USUARIO)
    assert r.status_code == 201 and r.json()["project_id"] == "MONITOREO_AMAZONIA"
    assert "project_name: Monitoreo Amazonía" in (raiz / "projects/monitoreo_amazonia/project.yaml").read_text("utf-8")
    assert client.post("/api/projects", json={"nombre": "monitoreo amazonia"}).status_code == 409
    ids = {p["project_id"] for p in client.get("/api/projects").json()["proyectos"]}
    assert {"MONITOREO_AMAZONIA", "SERVI_SINCHI", "PRJ001"} <= ids

    files = [("files", ("Acta comité.md", b"# Acta\nSe aprueba el piloto.", "text/markdown")),
             ("files", ("reunion.mp3", b"ID3audio-falso", "audio/mpeg")),
             ("files", ("virus.exe", b"MZ", "application/octet-stream")),
             ("files", ("../../fuera.txt", b"hola", "text/plain"))]
    r = client.post("/api/projects/MONITOREO_AMAZONIA/files", files=files).json()
    estados = {x["nombre"]: x["estado"] for x in r["resultados"]}
    assert estados == {"Acta comité.md": "subido", "reunion.mp3": "transcrito", "virus.exe": "error",
                       "../../fuera.txt": "subido"}
    docs = raiz / "projects/monitoreo_amazonia/documentos"
    assert (docs / "Acta comité.md").exists() and (docs / "fuera.txt").exists()
    assert not (raiz.parent / "fuera.txt").exists()
    assert "alertas tempranas" in (docs / "reunion.transcripcion.txt").read_text("utf-8")
    audio = next(a for a in r["archivos"] if a["nombre"] == "reunion.mp3")
    assert audio["audio"] and audio["transcrito"] and not audio["soportado"]

    # proyecto de archivos sueltos: el nombre lleva el prefijo que lo agrupa
    r = client.post("/api/projects/SERVI_SINCHI/files", files=[("files", ("acta.md", b"Acta SINCHI", "text/markdown"))])
    assert r.json()["resultados"][0]["estado"] == "subido"
    assert (raiz / "SERVI_SINCHI __ acta.md").exists()
    r = client.post("/api/projects/SERVI_SINCHI/files", files=[("files", ("acta.md", b"otra", "text/markdown"))])
    assert (raiz / "SERVI_SINCHI __ acta (2).md").exists()
    assert len(client.get("/api/projects/SERVI_SINCHI/files").json()["archivos"]) == 3
    assert client.get("/api/projects/NO_EXISTE/files").status_code == 404


def test_flujo_completo_desde_la_interfaz(entorno):
    client, raiz, eng = entorno
    client.post("/api/projects/SERVI_SINCHI/files", files=[("files", ("reunion.mp3", b"ID3", "audio/mpeg"))])
    r = client.post("/api/projects/SERVI_SINCHI/analyze", json={}, headers=USUARIO)
    assert r.status_code == 202
    st = esperar(client, "SERVI_SINCHI")
    assert st["trabajo"]["estado"] == "terminado", st["trabajo"]
    assert st["progreso"]["etapa"] == "terminado" and st["progreso"]["total"] == 2
    gen = [c for c in hu_fakes.LLAMADAS if c["agent"] == "story_generator_agent"][0]["instruction"]
    assert "alertas tempranas" in gen               # la transcripción del audio entra al análisis

    b = client.get("/api/projects/SERVI_SINCHI/backlog").json()
    h = {x["id"]: x for x in b["historias"]}
    assert h["HU-IA-001"]["tipo"] == "HU-IA" and "modelo" in h["HU-IA-001"]["tipo_motivo"]
    assert h["HU-IA-002"]["tipo"] == "HU-Tradicional"
    assert (h["HU-IA-001"]["prioridad"], h["HU-IA-002"]["prioridad"]) == ("Alta", "Media")
    assert h["HU-IA-001"]["prioridad_origen"] == "ia" and h["HU-IA-001"]["evidencia"]
    assert {x["estado_ui"] for x in h.values()} == {"revisar"}   # generadas: nunca automáticas
    assert len(h) == 2
    assert h["HU-IA-001"]["propuesta"]["acceptance_criteria"] and h["HU-IA-001"]["pruebas"]
    assert b["stats"]["hu_ia"] == 1 and b["stats"]["generadas"] == 2 and b["resumen"]

    # aprobar la épica completa
    r = client.post("/api/projects/SERVI_SINCHI/epics/approve", json={"epica": "Indicadores"}, headers=USUARIO).json()
    assert r["aprobadas"] == ["HU-IA-001"]
    b = client.get("/api/projects/SERVI_SINCHI/backlog").json()
    ep = {e["nombre"]: e for e in b["epicas"]}
    assert ep["Indicadores"]["avance"] == 100 and ep["Infraestructura"]["avance"] == 0
    store = eng.results_store("SERVI_SINCHI")
    assert store.read_json("generated/user_stories/HU-IA-001/approved.json")["approved_by"] == "Germán Ávila"

    # refinar: valida antes y reanaliza en segundo plano con el comentario humano
    url = "/api/projects/SERVI_SINCHI/stories/HU-IA-002/decision"
    assert client.post(url, json={"decision": "MODIFIED", "comentario": "  "}).status_code == 400
    r = client.post(url, json={"decision": "MODIFIED", "comentario": "La decide el comité técnico",
                               "modificaciones": {"priority": "Alta"}}, headers=USUARIO)
    assert r.status_code == 202
    assert esperar(client, "SERVI_SINCHI")["trabajo"]["estado"] == "terminado"
    analista = [c for c in hu_fakes.LLAMADAS if c["agent"] == "story_analyst_agent" and c["story_id"] == "HU-IA-002"]
    assert "comité técnico" in analista[-1]["instruction"]
    h2 = {x["id"]: x for x in client.get("/api/projects/SERVI_SINCHI/backlog").json()["historias"]}["HU-IA-002"]
    assert h2["refinamientos"] == 1 and h2["prioridad"] == "Alta" and h2["prioridad_origen"] == "humano"
    assert any(v.endswith("_human_modified.json") for v in store.list("generated/user_stories/HU-IA-002/versions/"))

    # descartar y exportar
    r = client.post(url, json={"decision": "REJECTED", "comentario": "Fuera de alcance"}, headers=USUARIO)
    assert r.status_code == 200
    b = client.get("/api/projects/SERVI_SINCHI/backlog").json()
    assert b["stats"]["descartadas"] == 1 and b["stats"]["total"] == 1
    assert client.post(url, json={"decision": "APPROVED"}).status_code == 400   # ya descartada
    filas = list(csv.reader(io.StringIO(client.get("/api/projects/SERVI_SINCHI/export?formato=csv").text.lstrip("﻿")),
                            delimiter=";"))
    assert filas[0] == api.COLUMNAS and [f[0] for f in filas[1:]] == ["HU-IA-001"]
    js = client.get("/api/projects/SERVI_SINCHI/export?formato=json")
    assert js.headers["content-disposition"].startswith("attachment") and js.json()["historias"][0]["id"] == "HU-IA-001"
    assert client.get("/api/projects/SERVI_SINCHI/stories/HU-IA-001/explain").json()["estado_final"] == "READY_FOR_IMPLEMENTATION"


def test_decisiones_criticas_y_aprobacion_por_epica(entorno):
    client, _, _ = entorno
    client.post("/api/projects/PRJ001/analyze", json={})
    assert esperar(client, "PRJ001")["trabajo"]["estado"] == "terminado"
    h = {x["id"]: x for x in client.get("/api/projects/PRJ001/backlog").json()["historias"]}
    criticas = [sid for sid, x in h.items() if x["estado_ui"] == "decision"]
    assert "US-003" in criticas and h["US-003"]["decision"]["decision_id"]
    assert h["US-003"]["cambios_criticos"] and any(m["critico"] for m in h["US-003"]["hitl"]["motivos"])
    assert h["US-001"]["decision"]["preguntas"]

    epica = h["US-003"]["epica"]
    r = client.post("/api/projects/PRJ001/epics/approve", json={"epica": epica}).json()
    assert "US-003" in r["requieren_decision_individual"] and "US-003" not in r["aprobadas"]

    # la decisión crítica la toma el humano, historia por historia
    r = client.post("/api/projects/PRJ001/stories/US-003/decision",
                    json={"decision": "APPROVED", "comentario": "Migración por lotes aprobada"})
    assert r.status_code == 200 and r.json()["decision_id"] == h["US-003"]["decision"]["decision_id"]
    h = {x["id"]: x for x in client.get("/api/projects/PRJ001/backlog").json()["historias"]}
    assert h["US-003"]["estado_ui"] == "aprobada"


def test_no_se_lanzan_dos_trabajos_a_la_vez(entorno):
    client, _, eng = entorno

    async def lento():
        await asyncio.sleep(0.3)
        return {"status": "COMPLETED"}

    async def lanzar():
        api._iniciar("PRJ001", "analisis", lento)
        with pytest.raises(api.HTTPException) as e:
            api._iniciar("PRJ001", "analisis", lento)
        assert e.value.status_code == 409
        await api._jobs["PRJ001"]["task"]

    asyncio.run(lanzar())
    assert api._jobs["PRJ001"]["estado"] == "terminado"


def test_adk_web_publica_spb_y_api(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "t")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    set_engine(HuEngine(HuSettings(input_uri=str(RAIZ / "ejemplos" / "projects"), results_uri=str(tmp_path / "out")),
                        model=hu_fakes.ModeloSimulado()))
    sys.path.insert(0, str(RAIZ))
    import adk_web

    app = adk_web.crear_app(session_service_uri="memory://", artifact_service_uri="memory://")
    with TestClient(app) as client:
        r = client.get("/spb")
        assert r.status_code == 200 and "Smart Product Backlog" in r.text
        assert client.get("/api/health").json()["agente_chat"] == "orquestador_hu"
        assert client.get("/list-apps").json() == ["agente_bucket", "orquestador_hu"]
    set_engine(None)


def test_backlog_muestra_las_generadas_que_faltan_por_evaluar(entorno):
    client, _, eng = entorno
    eng.settings = dataclasses.replace(eng.settings, max_stories_per_run=1)  # solo se evalúa una
    client.post("/api/projects/SERVI_SINCHI/analyze", json={})
    assert esperar(client, "SERVI_SINCHI")["trabajo"]["estado"] == "terminado"
    h = {x["id"]: x for x in client.get("/api/projects/SERVI_SINCHI/backlog").json()["historias"]}
    assert h["HU-IA-001"]["estado_ui"] == "revisar"
    assert h["HU-IA-002"]["estado_ui"] == "pendiente" and h["HU-IA-002"]["titulo"]
