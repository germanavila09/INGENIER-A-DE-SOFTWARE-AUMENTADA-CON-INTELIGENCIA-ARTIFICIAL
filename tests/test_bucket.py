"""Pruebas sin red: se usa un cliente de GCS falso."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from adk_ing.bucket import BucketReader, _safe_target

FILES = {
    "documentos/": b"",  # marcador de carpeta
    "documentos/guia.md": "# Guía\nhola ñandú".encode("utf-8"),
    "datos/tabla.csv": b"a,b\n1,2\n",
    "imagenes/logo.png": b"\x89PNG\r\n\x1a\n\xff\xfe",
}


class FakeBlob:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data
        self.size = len(data)
        self.content_type = "application/octet-stream"
        self.updated = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def download_as_bytes(self) -> bytes:
        return self._data

    def download_to_filename(self, path: str) -> None:
        Path(path).write_bytes(self._data)


class FakeBucket:
    def __init__(self, files):
        self.files = files

    def blob(self, name):
        return FakeBlob(name, self.files[name])


class FakeClient:
    def __init__(self, files=FILES):
        self.files = files

    def bucket(self, name):
        return FakeBucket(self.files)

    def list_blobs(self, bucket_name, prefix=None):
        for name, data in sorted(self.files.items()):
            if prefix is None or name.startswith(prefix):
                yield FakeBlob(name, data)


@pytest.fixture
def reader():
    return BucketReader(bucket_name="adk_ing", prefix="", client=FakeClient())


def test_list_omite_carpetas(reader):
    names = [o.name for o in reader.list()]
    assert "documentos/" not in names
    assert names == ["datos/tabla.csv", "documentos/guia.md", "imagenes/logo.png"]


def test_list_con_prefijo_y_maximo(reader):
    assert [o.name for o in reader.list(prefix="documentos/")] == ["documentos/guia.md"]
    assert len(reader.list(max_results=2)) == 2


def test_read_text_utf8(reader):
    assert "ñandú" in reader.read_text("documentos/guia.md")


def test_sync_descarga_y_es_idempotente(reader, tmp_path):
    first = reader.sync(dest_dir=tmp_path)
    assert len(first) == 3
    assert (tmp_path / "datos" / "tabla.csv").read_bytes() == FILES["datos/tabla.csv"]
    assert reader.sync(dest_dir=tmp_path) == []


def test_safe_target_bloquea_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        _safe_target(tmp_path, "../fuera.txt")


def test_indice_sobre_el_bucket(monkeypatch):
    """El agente funciona igual sobre el bucket (cliente falso) que sobre una carpeta."""
    from adk_ing.indice import IndiceDocumentos
    from agente_bucket import agent as mod

    reader = BucketReader(bucket_name="adk_ing", prefix="", client=FakeClient())
    monkeypatch.setattr(mod, "_indice", IndiceDocumentos(reader, ttl_segundos=0))

    listado = mod.listar_documentos()
    assert listado["fuente"] == "gs://adk_ing/" and listado["total"] == 3
    assert {d["name"]: d["legible"] for d in listado["documentos"]}["imagenes/logo.png"] is False

    r = mod.buscar_en_documentos("ñandú")
    assert r["resultados"][0]["documento"] == "documentos/guia.md"
    assert set(r["documentos_recien_indexados"]) == {"documentos/guia.md", "datos/tabla.csv"}

    assert mod.root_agent.name == "agente_bucket"
