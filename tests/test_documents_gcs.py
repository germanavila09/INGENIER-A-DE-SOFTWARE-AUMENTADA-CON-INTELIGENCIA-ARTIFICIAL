"""Fuente de documentos en Cloud Storage (AGENT_DOCS_SOURCE=gcs) con un cliente falso, sin red."""

import pytest

from app.agents.document_tools import make_document_tools
from pydantic import ValidationError

from app.config import Settings, settings
from app.services.documents import FolderSource, GcsSource, get_document_source
from app.services.documents import gcs as gcs_module
from app.services.documents.folder import MAX_FILE_BYTES
from app.services.errors import NotFound, ServiceUnavailable, Unprocessable
from tests.conftest import INGEST_PAYLOAD, login


class FakeBlob:
    def __init__(self, name: str, data: bytes, size: int | None = None):
        self.name, self._data = name, data
        self.size = len(data) if size is None else size

    def download_as_bytes(self) -> bytes:
        return self._data


class FakeBucket:
    def __init__(self, blobs: dict[str, FakeBlob]):
        self._blobs = blobs

    def get_blob(self, name: str):
        return self._blobs.get(name)


class FakeClient:
    """Imita lo que usa GcsSource de google.cloud.storage.Client."""

    def __init__(self, files: dict[str, bytes | FakeBlob], fail: bool = False):
        self.blobs = {k: v if isinstance(v, FakeBlob) else FakeBlob(k, v) for k, v in files.items()}
        self.fail = fail

    def bucket(self, name: str) -> FakeBucket:
        return FakeBucket(self.blobs)

    def list_blobs(self, bucket_name: str, prefix: str = "", delimiter: str | None = None):
        if self.fail:
            raise RuntimeError("403 Forbidden")
        for name, blob in self.blobs.items():
            rest = name[len(prefix):] if name.startswith(prefix) else None
            if rest is None or (delimiter and delimiter in rest):
                continue
            yield blob


FILES = {
    "spb/DEMO/reunion.md": b"# Pagos\n## Requisitos\n- El cliente paga con tarjeta de credito\n",
    "spb/DEMO/notas.pptx": b"no soportado",
    "spb/DEMO/sub/oculto.md": b"# Sub\n- No debe verse desde DEMO\n",
    "spb/DEMO/": b"",  # marcador de carpeta creado desde la consola
    "spb/OTRO/secreto.md": b"# Otro\n- Documento de otro proyecto\n",
}


def source(prefix="spb", files=FILES, **kw) -> GcsSource:
    return GcsSource("bucket-spb", prefix, "DEMO", client=FakeClient(files, **kw))


def test_lists_only_direct_supported_files_of_the_project():
    src = source()
    assert [d.name for d in src.list()] == ["reunion.md"]
    assert src.location == "gs://bucket-spb/spb/DEMO/"
    assert src.kind == "gcs"


@pytest.mark.parametrize("prefix,expected", [("", "gs://b/DEMO/"), ("/spb/", "gs://b/spb/DEMO/"), ("a/b", "gs://b/a/b/DEMO/")])
def test_prefix_is_normalized(prefix, expected):
    assert GcsSource("b", prefix, "DEMO", client=FakeClient({})).location == expected


def test_reads_bytes_of_a_project_file():
    assert source().read_bytes("reunion.md").startswith(b"# Pagos")


@pytest.mark.parametrize("name", ["../OTRO/secreto.md", "sub/oculto.md", "", "..", "a\\b.md", "x\x00.md"])
def test_paths_are_rejected(name):
    with pytest.raises(Unprocessable):
        source().read_bytes(name)


def test_unsupported_missing_and_oversized_files():
    with pytest.raises(Unprocessable, match="Formato"):
        source().read_bytes("notas.pptx")
    with pytest.raises(NotFound):
        source().read_bytes("secreto.md")  # existe, pero en OTRO
    big = {"spb/DEMO/grande.pdf": FakeBlob("spb/DEMO/grande.pdf", b"x", size=MAX_FILE_BYTES + 1)}
    with pytest.raises(Unprocessable, match="20 MB"):
        source(files=big).read_bytes("grande.pdf")


def test_bucket_errors_are_503():
    with pytest.raises(ServiceUnavailable):
        source(fail=True).list()
    with pytest.raises(ServiceUnavailable, match="AGENT_DOCS_BUCKET"):
        GcsSource("", "", "DEMO", client=FakeClient({}))


def test_factory_defaults_to_folder_and_switches_to_gcs(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "agent_docs_dir", str(tmp_path))
    assert isinstance(get_document_source("DEMO"), FolderSource)
    assert get_document_source("DEMO").kind == "folder"

    monkeypatch.setattr(settings, "agent_docs_source", "gcs")
    monkeypatch.setattr(settings, "agent_docs_bucket", "bucket-spb")
    monkeypatch.setattr(settings, "agent_docs_prefix", "spb")
    src = get_document_source("DEMO", client=FakeClient(FILES))
    assert isinstance(src, GcsSource) and src.location == "gs://bucket-spb/spb/DEMO/"

    monkeypatch.setattr(settings, "agent_docs_source", "s3")
    with pytest.raises(ServiceUnavailable, match="AGENT_DOCS_SOURCE"):
        get_document_source("DEMO")


# ---------- carpeta O bucket: la configuración no admite mezclas ----------

def make_settings(**env):
    return Settings(_env_file=None, **env)


def test_settings_accept_exactly_one_source():
    assert make_settings().agent_docs_source == "folder"
    folder = make_settings(agent_docs_source="folder", agent_docs_bucket="", agent_docs_prefix="", gcp_project="")
    assert folder.agent_docs_bucket is None
    gcs = make_settings(agent_docs_source=" GCS ", agent_docs_bucket="bucket-spb", agent_docs_prefix="spb")
    assert gcs.agent_docs_source == "gcs"


@pytest.mark.parametrize("env,message", [
    ({"agent_docs_source": "gcs"}, "requiere AGENT_DOCS_BUCKET"),
    ({"agent_docs_source": "gcs", "agent_docs_bucket": "  "}, "requiere AGENT_DOCS_BUCKET"),
    ({"agent_docs_bucket": "bucket-spb"}, "folder no usa AGENT_DOCS_BUCKET"),
    ({"agent_docs_source": "folder", "agent_docs_prefix": "spb", "gcp_project": "p"},
     "folder no usa AGENT_DOCS_PREFIX, GCP_PROJECT"),
    ({"agent_docs_source": "s3"}, "agent_docs_source"),
])
def test_settings_reject_mixed_or_unknown_sources(env, message):
    with pytest.raises(ValidationError, match=message):
        make_settings(**env)


def test_gcs_mode_never_reads_the_local_folder(monkeypatch, tmp_path):
    """Con gcs, un archivo que solo existe en la carpeta local no se encuentra."""
    (tmp_path / "DEMO").mkdir()
    (tmp_path / "DEMO" / "local.md").write_text("# Local\n- Solo en la carpeta\n", encoding="utf-8")
    monkeypatch.setattr(settings, "agent_docs_dir", str(tmp_path))
    monkeypatch.setattr(settings, "agent_docs_source", "gcs")
    monkeypatch.setattr(settings, "agent_docs_bucket", "bucket-spb")
    monkeypatch.setattr(settings, "agent_docs_prefix", "spb")
    src = get_document_source("DEMO", client=FakeClient(FILES))
    assert [d.name for d in src.list()] == ["reunion.md"]
    with pytest.raises(NotFound, match="gs://bucket-spb/spb/DEMO/"):
        src.read_bytes("local.md")


# ---------- herramientas del agente de documentos ----------

def test_document_tools_list_and_read_in_chunks():
    text = "# Pagos\n" + "- requisito largo de pago\n" * 2000
    tools = make_document_tools(source(files={"spb/DEMO/largo.md": text.encode()}), max_total_chars=100_000)

    listing = tools.listar_documentos()
    assert listing["status"] == "ok" and listing["ubicacion"] == "gs://bucket-spb/spb/DEMO/"
    assert listing["documentos"] == [{"nombre": "largo.md", "bytes": len(text)}]

    parts, start = [], 0
    while start is not None:
        chunk = tools.leer_documento("largo.md", desde_caracter=start, max_caracteres=20_000)
        assert chunk["status"] == "ok" and len(chunk["texto"]) <= 15_000  # tope por llamada
        parts.append(chunk["texto"])
        start = chunk.get("continuar_desde")
    assert "".join(parts) == text.strip()
    assert tools.documents_read == ["largo.md"]
    assert tools.chars_served == len(text.strip())


def test_document_tools_budget_and_errors_are_returned_not_raised():
    tools = make_document_tools(source(), max_total_chars=30)
    assert tools.leer_documento("no-existe.md")["status"] == "error"
    assert tools.leer_documento("../OTRO/secreto.md")["status"] == "error"
    first = tools.leer_documento("reunion.md")
    assert first["status"] == "error" and "máximo" in first["mensaje"]  # documento > presupuesto

    tools = make_document_tools(source(), max_total_chars=1_000)
    tools.chars_served = 1_000
    assert "presupuesto" in tools.leer_documento("reunion.md")["mensaje"]
    assert make_document_tools(source(fail=True), 1_000).listar_documentos()["status"] == "error"


# ---------- API y pantallas con el bucket ----------

@pytest.fixture
def gcs_demo(client, service_key, monkeypatch):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    fake = FakeClient(FILES)
    monkeypatch.setattr(settings, "agent_provider", "stub")
    monkeypatch.setattr(settings, "agent_execution", "inline")
    monkeypatch.setattr(settings, "agent_docs_source", "gcs")
    monkeypatch.setattr(settings, "agent_docs_bucket", "bucket-spb")
    monkeypatch.setattr(settings, "agent_docs_prefix", "spb")
    monkeypatch.setattr(gcs_module, "_client", lambda project: fake)
    return client


def test_api_lists_and_generates_from_bucket(gcs_demo):
    login(gcs_demo, "po@demo.co")
    assert gcs_demo.get("/api/v1/agents/status").json()["docs_source"] == "gcs"
    assert "Documentos · Bucket de Cloud Storage" in gcs_demo.get("/p/DEMO/agents").text
    listed = gcs_demo.get("/api/v1/projects/DEMO/agents/documents").json()
    assert listed["source"] == "gcs"
    assert listed["folder"] == "gs://bucket-spb/spb/DEMO/"
    assert [d["name"] for d in listed["documents"]] == ["reunion.md"]

    run = gcs_demo.post("/api/v1/projects/DEMO/agents/generate-stories", json={"filename": "reunion.md"}).json()
    assert run["status"] == "succeeded", run
    assert gcs_demo.post(
        "/api/v1/projects/DEMO/agents/generate-stories", json={"filename": "../OTRO/secreto.md"}
    ).status_code == 422


def test_api_generates_from_all_bucket_documents(gcs_demo):
    login(gcs_demo, "po@demo.co")
    res = gcs_demo.post("/api/v1/projects/DEMO/agents/generate-stories/from-documents", json={})
    assert res.status_code == 202, res.text
    run = res.json()
    assert run["status"] == "succeeded", run
    assert run["result"]["documents_read"] == ["reunion.md"]
    assert run["input_ref"] == "documentos: gs://bucket-spb/spb/DEMO/"
    story = gcs_demo.get(f"/api/v1/projects/DEMO/stories/{run['result']['created'][0]}").json()
    assert story["source_ref"] == "reunion.md · Requisitos"
    assert story["status"] == "borrador"


def test_unreachable_bucket_keeps_pages_working(gcs_demo, monkeypatch):
    monkeypatch.setattr(gcs_module, "_client", lambda project: FakeClient({}, fail=True))
    login(gcs_demo, "po@demo.co")
    assert gcs_demo.get("/api/v1/projects/DEMO/agents/documents").status_code == 503
    for page in ("/p/DEMO/agents", "/p/DEMO/backlog"):
        res = gcs_demo.get(page)
        assert res.status_code == 200 and "403 Forbidden" in res.text


def test_env_example_loads_as_is(tmp_path):
    """`cp .env.example .env` debe arrancar: las variables vacías cuentan como no definidas."""
    from pathlib import Path

    env = tmp_path / ".env"
    env.write_text((Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8"))
    loaded = Settings(_env_file=env)
    assert loaded.agent_docs_source == "folder" and loaded.agent_docs_bucket is None
    assert loaded.agent_price_in_per_1m is None and loaded.agent_price_out_per_1m is None
    assert make_settings(agent_price_in_per_1m="1.25").agent_price_in_per_1m == 1.25
