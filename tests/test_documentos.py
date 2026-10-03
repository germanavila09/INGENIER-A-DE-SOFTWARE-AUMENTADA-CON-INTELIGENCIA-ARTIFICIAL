"""Extracción, índice y herramientas del agente con los documentos de docs_ejemplo/ (sin red)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from adk_ing.bucket import CarpetaLocal
from adk_ing.documentos import extraer
from adk_ing.indice import IndiceDocumentos, tokens

EJEMPLOS = Path(__file__).resolve().parents[1] / "docs_ejemplo"


def leer(nombre: str):
    return extraer(nombre, (EJEMPLOS / nombre).read_bytes())


# ------------------------------------------------------------------ extracción
def test_pdf_por_paginas():
    d = leer("politica_calidad_software.pdf")
    assert [s.etiqueta for s in d.secciones] == ["página 1", "página 2"]
    assert "80 %" in d.secciones[0].texto
    assert "asistido-por-IA" in d.secciones[1].texto


def test_docx_incluye_tablas():
    d = leer("acta_reunion_sprint3.docx")
    assert "15 de noviembre de 2026" in d.secciones[0].texto
    assert "Analista de QA | 17/10/2026" in d.secciones[0].texto


def test_pptx_por_diapositiva_con_notas():
    d = leer("arquitectura_agente.pptx")
    assert len(d.secciones) == 3
    assert "[Notas] El índice se refresca cada 30 segundos." in d.secciones[1].texto


def test_xlsx_por_hoja():
    d = leer("backlog_sprint.xlsx")
    assert [s.etiqueta for s in d.secciones] == ["hoja Backlog", "hoja Riesgos"]
    assert "HU-03 | Como analista quiero consultar PDF escaneados | 8 | En curso" in d.secciones[0].texto


def test_pdf_escaneado_avisa():
    d = leer("constancia_escaneada.pdf")
    assert not d.tiene_texto and "escaneado" in d.aviso


def test_csv_y_texto_latin1():
    d = extraer("datos.csv", "nombre;ciudad\nAna;Cali\nLuis;Bogotá\n".encode("cp1252"))
    assert "Luis | Bogotá" in d.secciones[0].texto


def test_tokens_sin_tildes_ni_vacias():
    assert tokens("La Política de calidad del código") == ["politica", "calidad", "codigo"]


# ---------------------------------------------------------------------- índice
@pytest.fixture
def carpeta(tmp_path):
    destino = tmp_path / "bucket"
    shutil.copytree(EJEMPLOS, destino)
    return destino


def test_busqueda_encuentra_la_pagina_correcta(carpeta):
    indice = IndiceDocumentos(CarpetaLocal(carpeta), ttl_segundos=0)
    r = indice.buscar("cobertura mínima de pruebas")
    assert r[0]["documento"] == "politica_calidad_software.pdf"
    assert r[0]["ubicacion"] == "página 1"

    r = indice.buscar("etiqueta para código generado con inteligencia artificial")
    assert (r[0]["documento"], r[0]["ubicacion"]) == ("politica_calidad_software.pdf", "página 2")

    r = indice.buscar("fecha de la demo con el cliente")
    assert r[0]["documento"] == "acta_reunion_sprint3.docx"


def test_indice_detecta_documentos_ingestados_y_borrados(carpeta):
    indice = IndiceDocumentos(CarpetaLocal(carpeta), ttl_segundos=0)
    assert indice.buscar("presupuesto aprobado") == []

    sub = carpeta / "documentos"
    sub.mkdir()
    (sub / "presupuesto.txt").write_text("El presupuesto aprobado es de 9 millones.", encoding="utf-8")
    cambios = indice.refrescar(forzar=True)
    assert cambios["nuevos"] == ["documentos/presupuesto.txt"]
    assert indice.buscar("presupuesto aprobado")[0]["documento"] == "documentos/presupuesto.txt"

    (sub / "presupuesto.txt").unlink()
    assert indice.refrescar(forzar=True)["eliminados"] == ["documentos/presupuesto.txt"]
    assert indice.buscar("presupuesto aprobado") == []


def test_archivo_danado_no_rompe_el_indice(carpeta):
    (carpeta / "roto.pdf").write_bytes(b"no soy un pdf")
    indice = IndiceDocumentos(CarpetaLocal(carpeta), ttl_segundos=0)
    cambios = indice.refrescar(forzar=True)
    assert [e["documento"] for e in cambios["errores"]] == ["roto.pdf"]
    assert indice.buscar("cobertura pruebas")


# ------------------------------------------------------- herramientas del agente
@pytest.fixture
def agente(monkeypatch, carpeta):
    import adk_ing.agente as mod

    monkeypatch.setattr(mod, "_indice", IndiceDocumentos(CarpetaLocal(carpeta), ttl_segundos=0))
    return mod


def test_herramientas(agente):
    lista = agente.listar_documentos()
    assert lista["status"] == "ok" and lista["total"] == 6

    r = agente.buscar_en_documentos("riesgos del proyecto mitigación")
    assert r["resultados"][0]["documento"] == "backlog_sprint.xlsx"

    r = agente.buscar_en_documentos("demo", documento="arquitectura_agente.pptx")
    assert all(x["documento"] == "arquitectura_agente.pptx" for x in r["resultados"])

    leido = agente.leer_documento("politica_calidad_software.pdf", max_caracteres=300)
    assert leido["total_secciones"] == 2 and leido["continuar_desde"] == 2
    leido = agente.leer_documento("politica_calidad_software.pdf", desde_seccion=2)
    assert leido["secciones"][0]["ubicacion"] == "página 2" and "continuar_desde" not in leido

    esc = agente.leer_documento("constancia_escaneada.pdf")
    assert "consultar_documento_con_gemini" in esc["sugerencia"]

    estado = agente.estado_del_indice()
    assert [d["documento"] for d in estado["sin_texto_o_error"]] == ["constancia_escaneada.pdf"]

    assert agente.leer_documento("no_existe.pdf")["status"] == "error"
    assert agente.consultar_documento_con_gemini("backlog_sprint.xlsx", "x")["status"] == "error"


def test_consultar_con_gemini_envia_el_pdf(agente, monkeypatch):
    enviado = {}

    class FakeModels:
        def generate_content(self, model, contents):
            enviado["mime"] = contents[0].inline_data.mime_type
            enviado["bytes"] = len(contents[0].inline_data.data)
            return type("R", (), {"text": "Versión 0.2.0, entregada el 3 de octubre de 2026."})()

    class FakeClient:
        models = FakeModels()

    from google import genai

    monkeypatch.setattr(genai, "Client", lambda *a, **k: FakeClient())
    r = agente.consultar_documento_con_gemini("constancia_escaneada.pdf", "¿Qué versión se entregó?")
    assert r["status"] == "ok" and "0.2.0" in r["respuesta"]
    assert enviado["mime"] == "application/pdf" and enviado["bytes"] > 1000
