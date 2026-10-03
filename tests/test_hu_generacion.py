"""Proyectos con archivos sueltos (como las notas de SINCHI en la raíz del bucket) y
generación de historias desde sus documentos."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import hu_fakes
import pytest

from hu_multiagent.config import HuSettings
from hu_multiagent.workflows.engine import HuEngine, set_engine

RAIZ = Path(__file__).resolve().parents[1]
NOTAS = "SERVI _ SINCHI __ Sesion tecnica imagenes satelitales - 2026_09_02 - Notas de Gemini.md"
TEXTO = """# SERVI | SINCHI :: Sesión técnica imágenes satelitales (ejemplo ficticio)
Se discutió un agente de IA para publicar indicadores de coberturas de la tierra.
Decisión: los indicadores se publican mensualmente.
Tema abierto: definir si la implementación será en Google Earth Engine o en infraestructura propia.
"""


def run(c):
    return asyncio.run(c)


@pytest.fixture
def bucket(tmp_path, monkeypatch):
    for k, v in {"GOOGLE_GENAI_USE_VERTEXAI": "TRUE", "GOOGLE_CLOUD_PROJECT": "t", "GOOGLE_CLOUD_LOCATION": "us-central1"}.items():
        monkeypatch.setenv(k, v)
    raiz = tmp_path / "adk_ing"
    shutil.copytree(RAIZ / "ejemplos" / "projects", raiz / "projects")
    shutil.copytree(RAIZ / "docs_ejemplo", raiz / "documentos")
    (raiz / NOTAS).write_text(TEXTO, encoding="utf-8")
    hu_fakes.LLAMADAS.clear()
    hu_fakes.FALLAS.clear()

    def crear(**over):
        eng = HuEngine(HuSettings(input_uri=str(raiz), results_uri=str(tmp_path / "out"), **over),
                       model=hu_fakes.ModeloSimulado())
        eng.retry_backoff_s = 0
        eng.audit.emit_logs = False
        set_engine(eng)
        return eng

    yield crear, raiz, tmp_path / "out"
    set_engine(None)


def test_descubre_carpetas_projects_y_archivos_sueltos(bucket):
    crear, *_ = bucket
    eng = crear()
    proyectos = {p["project_id"]: p for p in eng.discover()}
    assert {"PRJ001", "PRJ002", "PROYECTO_003", "DOCUMENTOS", "SERVI_SINCHI"} <= set(proyectos)
    assert proyectos["SERVI_SINCHI"]["organizacion"] == "archivos sueltos agrupados por nombre"
    assert proyectos["SERVI_SINCHI"]["archivos"] == 1
    assert proyectos["PRJ001"]["organizacion"] == "carpeta"
    # el nombre como lo escribe el usuario se reconoce
    assert eng._resolve("SERVI _ SINCHI")[0] == "SERVI_SINCHI"
    assert eng._resolve("sinchi")[0] == "SERVI_SINCHI"


def test_genera_historias_desde_notas_y_las_evalua(bucket):
    crear, raiz, out = bucket
    eng = crear()
    r = run(eng.analyze_project("SERVI _ SINCHI"))
    assert r["historias_generadas_por_ia"] == "generadas"
    assert sorted(r["continuan_con_revision"]) == ["HU-IA-001", "HU-IA-002"]   # nunca automáticas
    gen_calls = [c for c in hu_fakes.LLAMADAS if c["agent"] == "story_generator_agent"]
    assert len(gen_calls) == 1 and "Earth Engine" in gen_calls[0]["instruction"]
    assert "dengue" not in gen_calls[0]["instruction"].lower()                 # solo documentos de SINCHI

    base = out / "projects" / "SERVI_SINCHI" / "generated"
    gen = json.loads((base / "story_generation.json").read_text())
    assert gen["stories"][0]["origin"] == "generated" and gen["stories"][0]["evidence"]
    assert (base / "user_stories" / "HU-IA-001" / "versions" / "HU-IA-001_v1_ai_generated.json").exists()
    hitl = json.loads((base / "user_stories" / "HU-IA-001" / "hitl.json").read_text())
    assert any(x["code"] == "GENERATED_STORY" for x in hitl["reasons"])
    assert "*(IA)*" in (base / "reports" / "project_report.md").read_text()
    estado = {h["story_id"]: h for h in eng.project_status("SERVI_SINCHI")["historias"]}
    assert estado["HU-IA-001"]["origen"] == "generated"

    # Sin cambios en los documentos: se reutilizan y no se reprocesa nada
    n = len(hu_fakes.LLAMADAS)
    r = run(eng.analyze_project("SERVI_SINCHI"))
    assert r["historias_generadas_por_ia"] == "reutilizadas" and r["procesadas"] == [] and len(hu_fakes.LLAMADAS) == n

    # Llega una nota nueva: se regenera conservando IDs y solo se analiza lo nuevo
    (raiz / NOTAS).write_text(TEXTO + "\nSe pidieron alertas de pérdida de bosque.\n", encoding="utf-8")
    r = run(eng.analyze_project("SERVI_SINCHI"))
    assert r["historias_generadas_por_ia"] == "regeneradas"
    assert "Historias generadas previamente" in hu_fakes.LLAMADAS[n]["instruction"]
    assert r["procesadas"] == ["HU-IA-003"]


def test_generadas_pueden_exigir_aprobacion(bucket):
    crear, *_ = bucket
    eng = crear(generated_requires_approval=True)
    r = run(eng.analyze_project("SERVI_SINCHI"))
    assert r["status"] == "WAITING_FOR_HUMAN"
    assert all(any(x["code"] == "GENERATED_STORY" for x in d["reasons"]) for d in r["esperando_decision_humana"])
