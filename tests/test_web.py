"""La app de ADK Web arranca y expone el agente del bucket (sin red ni credenciales)."""

from pathlib import Path

from fastapi.testclient import TestClient
from google.adk.cli.fast_api import get_fast_api_app

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


def test_adk_web_lista_el_agente(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    app = get_fast_api_app(
        agents_dir=str(AGENTS_DIR),
        web=True,
        session_service_uri="memory://",
        artifact_service_uri="memory://",
    )
    with TestClient(app) as client:
        assert client.get("/list-apps").json() == ["agente_bucket"]
        assert client.get("/dev-ui/").status_code == 200
        s = client.post("/apps/agente_bucket/users/u1/sessions", json={})
        assert s.status_code == 200 and s.json()["appName"] == "agente_bucket"
