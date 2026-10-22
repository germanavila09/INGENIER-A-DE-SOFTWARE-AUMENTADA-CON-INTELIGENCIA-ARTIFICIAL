"""Gemini en Vertex AI (credenciales de GCP, sin API key), bucket con su propia organización
(AGENT_DOCS_MAP) y documentos Word. Sin red: modelo falso y cliente de Storage falso."""

import io
import json

import pytest
from docx import Document
from pydantic import ValidationError

from app.agents import registry
from app.agents.ports import AgentNotConfigured, GenerationInput
from app.agents.providers import adk as adk_module
from app.agents.providers.adk import AdkProvider
from app.config import Settings, settings
from app.services.documents import GcsSource, get_document_source, load_document_text
from app.services.errors import NotFound, Unprocessable
from tests.test_adk_provider import GENERATION_JSON, FakeGemini
from tests.test_documents_gcs import FakeClient


def _docx_bytes() -> bytes:
    doc = Document()
    doc.add_heading("Sesión técnica imágenes satelitales", level=1)
    doc.add_paragraph("Se acordó publicar indicadores mensuales de coberturas.")
    doc.add_paragraph("Validar nubosidad antes de procesar", style="List Bullet")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Decisión", "Responsable"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def vertex_env(monkeypatch):
    # El proveedor escribe estas variables en os.environ; monkeypatch las restaura al terminar.
    for name, value in {"GOOGLE_GENAI_USE_VERTEXAI": "FALSE", "GOOGLE_CLOUD_PROJECT": "x", "GOOGLE_CLOUD_LOCATION": "x"}.items():
        monkeypatch.setenv(name, value)


# ---------- Vertex AI ----------

def test_vertex_needs_no_api_key_and_configures_google_genai(vertex_env):
    import os

    fake = FakeGemini(payload=json.dumps(GENERATION_JSON))
    provider = AdkProvider(model="gemini-2.5-flash", api_key=None, llm=fake, vertexai=True,
                           project="servi-modelos-ia-dev", location="us-central1")
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "TRUE"
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "servi-modelos-ia-dev"
    assert provider.backend == "Vertex AI · servi-modelos-ia-dev · us-central1"
    out, usage = provider.generate_stories(GenerationInput(project_key="X", project_name="x", source_name="s", text="t"))
    assert out.stories[0].title == "Pagar con tarjeta" and usage.tokens_in == 120


def test_vertex_requires_project(vertex_env):
    with pytest.raises(AgentNotConfigured, match="GOOGLE_CLOUD_PROJECT"):
        AdkProvider(model="gemini-2.5-flash", api_key=None, vertexai=True, project=None)


def test_vertex_without_credentials_is_not_configured(vertex_env, monkeypatch):
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    def sin_credenciales(**kw):
        raise DefaultCredentialsError("no ADC")

    monkeypatch.setattr(google.auth, "default", sin_credenciales)
    with pytest.raises(AgentNotConfigured, match="application-default login"):
        AdkProvider(model="gemini-2.5-flash", api_key=None, vertexai=True, project="p")


def test_provider_status_shows_vertex_backend(vertex_env, monkeypatch):
    import google.auth

    monkeypatch.setattr(google.auth, "default", lambda **kw: (object(), "p"))
    for name, value in {"agent_provider": "adk", "agent_model": "gemini-2.5-flash", "google_api_key": None,
                        "google_genai_use_vertexai": True, "google_cloud_project": "servi-modelos-ia-dev",
                        "google_cloud_location": "us-central1"}.items():
        monkeypatch.setattr(settings, name, value)
    status = registry.provider_status()
    assert status["configured"] is True and status["backend"].startswith("Vertex AI · servi-modelos-ia-dev")


def test_api_key_mode_still_requires_key(vertex_env):
    with pytest.raises(AgentNotConfigured, match="GOOGLE_API_KEY"):
        AdkProvider(model="gemini-2.5-flash", api_key=None)
    assert adk_module  # el módulo sigue importando sin credenciales


# ---------- AGENT_DOCS_MAP ----------

def test_docs_map_is_parsed_and_validated():
    s = Settings(agent_docs_source="gcs", agent_docs_bucket="adk_ing",
                 agent_docs_map="sinchi=SERVI _ SINCHI __ ; DOCS=/documentos/")
    assert s.docs_map == {"SINCHI": "SERVI _ SINCHI __", "DOCS": "documentos/"}
    with pytest.raises(ValidationError, match="AGENT_DOCS_MAP"):
        Settings(agent_docs_source="gcs", agent_docs_bucket="adk_ing", agent_docs_map="SINCHI")
    with pytest.raises(ValidationError, match="AGENT_DOCS_MAP"):
        Settings(agent_docs_source="gcs", agent_docs_bucket="adk_ing", agent_docs_map="X=../fuera/")
    with pytest.raises(ValidationError, match="AGENT_DOCS_MAP"):
        Settings(agent_docs_source="folder", agent_docs_map="SINCHI=SERVI _ SINCHI __")


BUCKET = {
    "SERVI _ SINCHI __ Sesion tecnica - Notas de Gemini.docx": _docx_bytes(),
    "SERVI _ SINCHI __ Acta.md": b"# Acta\n- Se aprueba el piloto\n",
    "SERVI _ OTRO __ Secreto.md": b"# Otro proyecto\n",
    "documentos/requisitos.md": b"# Requisitos\n- Login\n",
    "documentos/sub/anexo.md": b"# Anexo\n",
    "suelto.md": b"# Suelto\n",
}


def test_mapped_name_prefix_reads_loose_files_of_that_project_only():
    src = GcsSource("adk_ing", "", "SINCHI", client=FakeClient(BUCKET), mapped_prefix="SERVI _ SINCHI __")
    assert [d.name for d in src.list()] == ["SERVI _ SINCHI __ Acta.md", "SERVI _ SINCHI __ Sesion tecnica - Notas de Gemini.docx"]
    assert src.location == "gs://adk_ing/SERVI _ SINCHI __"
    assert src.read_bytes("SERVI _ SINCHI __ Acta.md").startswith(b"# Acta")
    for ajeno in ("SERVI _ OTRO __ Secreto.md", "suelto.md"):
        with pytest.raises(NotFound):
            src.read_bytes(ajeno)


def test_mapped_folder_lists_direct_files():
    src = GcsSource("adk_ing", "", "DOCS", client=FakeClient(BUCKET), mapped_prefix="documentos/")
    assert [d.name for d in src.list()] == ["requisitos.md"]
    with pytest.raises(Unprocessable):
        src.read_bytes("sub/anexo.md")


def test_get_document_source_uses_the_map(monkeypatch):
    for name, value in {"agent_docs_source": "gcs", "agent_docs_bucket": "adk_ing", "agent_docs_prefix": "spb",
                        "agent_docs_map": "SINCHI=SERVI _ SINCHI __"}.items():
        monkeypatch.setattr(settings, name, value)
    assert get_document_source("SINCHI", client=FakeClient(BUCKET)).location == "gs://adk_ing/SERVI _ SINCHI __"
    assert get_document_source("PAGO", client=FakeClient(BUCKET)).location == "gs://adk_ing/spb/PAGO/"


# ---------- Word ----------

def test_docx_is_converted_to_markdown():
    text = load_document_text("notas.docx", _docx_bytes(), 10_000)
    assert "## Sesión técnica imágenes satelitales" in text
    assert "- Validar nubosidad antes de procesar" in text
    assert "Se acordó publicar indicadores mensuales" in text
    assert "| Decisión | Responsable |" in text


def test_invalid_docx_is_rejected():
    with pytest.raises(Unprocessable, match="DOCX"):
        load_document_text("roto.docx", b"no es un zip", 10_000)
