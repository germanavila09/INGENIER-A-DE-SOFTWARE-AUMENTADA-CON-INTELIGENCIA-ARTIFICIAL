"""Puente hacia SmartBacklogIA con un SmartBacklogIA simulado (sin red): mapeo de historias,
qué se publica y qué no, idempotencia, respeto de las ediciones hechas allá, errores y API."""

from __future__ import annotations

import json
import re

import httpx
import pytest

from hu_multiagent import api
from hu_multiagent.integrations import smartbacklog as sb
from test_hu_api import USUARIO, entorno, esperar  # noqa: F401 — fixture compartida

CLAVE_OK = "sbia_test_key"
CLAVE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


class FakeSmartBacklog:
    """Imita la API de servicio de SmartBacklogIA: ingest idempotente por external_id, versiones y etapas."""

    def __init__(self) -> None:
        self.proyectos: dict[str, dict] = {}
        self.historias: dict[str, list[dict]] = {}
        self.ingestas: list[dict] = []
        self.caido = False

    # ---- utilidades para las pruebas
    def editar(self, clave: str, external_id: str, **cambios) -> None:
        h = next(x for x in self.historias[clave] if x["external_id"] == external_id)
        h.update(cambios)
        h["version"] += 1

    # ---- servidor
    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.caido:
            raise httpx.ConnectError("conexión rechazada", request=request)
        ruta = request.url.path
        if ruta == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.headers.get("X-API-Key") != CLAVE_OK:
            return httpx.Response(401, json={"detail": "API key inválida o revocada"})
        if ruta == "/api/v1/agents/status":
            return httpx.Response(200, json={"provider": "adk", "model": "gemini-2.5-flash",
                                             "backend": "Vertex AI · p · us-central1", "configured": True})
        if ruta == "/api/v1/ingest/projects" and request.method == "POST":
            return self._ingest(json.loads(request.content))
        m = re.fullmatch(r"/api/v1/projects/([A-Z0-9]+)(/stories|/pipeline)?", ruta)
        if m and request.method == "GET":
            clave, sufijo = m.groups()
            if clave not in self.proyectos:
                return httpx.Response(404, json={"detail": f"Proyecto {clave} no existe"})
            if sufijo == "/stories":
                return httpx.Response(200, json=self.historias[clave])
            if sufijo == "/pipeline":
                return httpx.Response(200, json=[{"stage_number": n, "name": nombre, "status": "pending"}
                                                 for n, nombre in enumerate(["Generación de HU", "Clasificación",
                                                                             "Estimación", "Priorización",
                                                                             "Smart Product Backlog"], 1)])
            return httpx.Response(200, json=self.proyectos[clave])
        return httpx.Response(404, json={"detail": "Not Found"})

    def _ingest(self, body: dict) -> httpx.Response:
        self.ingestas.append(body)
        if not CLAVE_RE.match(body.get("key", "")) or len(body.get("name", "")) < 2:
            return httpx.Response(422, json={"detail": "clave o nombre inválidos"})
        for s in body["stories"]:   # mismas restricciones que StoryIn
            assert 3 <= len(s["title"]) <= 255 and len(s["external_id"]) <= 120
            assert s["type"] in ("HU_IA", "HU_TRADICIONAL", None)
            assert set(s) <= {"external_id", "epic", "title", "role", "need", "benefit", "acceptance_criteria", "type",
                              "type_justification", "fp_components", "priority_rank", "priority_method", "source_ref",
                              "assignee_email"}
        clave = body["key"]
        nuevo = clave not in self.proyectos
        self.proyectos[clave] = {"key": clave, "name": body["name"], "description": body.get("description")}
        lista = self.historias.setdefault(clave, [])
        creadas = actualizadas = 0
        for s in body["stories"]:
            previa = next((x for x in lista if x["external_id"] == s["external_id"]), None)
            if previa is None:
                lista.append({**s, "code": f"{clave}-{len(lista) + 1}", "status": "borrador", "version": 1})
                creadas += 1
            else:
                previa.update(s)
                previa["version"] += 1
                actualizadas += 1
        return httpx.Response(201, json={"project": clave, "project_created": nuevo, "users_created": 0,
                                         "stories_created": creadas, "stories_updated": actualizadas})


@pytest.fixture
def sbia(entorno, monkeypatch):  # noqa: F811
    client, raiz, eng = entorno
    fake = FakeSmartBacklog()
    monkeypatch.setenv("SBIA_URL", "http://sbia.test")
    monkeypatch.setenv("SBIA_API_KEY", CLAVE_OK)
    monkeypatch.setenv("SBIA_CLAVES", "SERVI_SINCHI=SINCHI")
    monkeypatch.setattr(api, "SBIA_TRANSPORT", httpx.MockTransport(fake.handler))
    yield client, eng, fake


def _analizar_y_aprobar(client):
    assert client.post("/api/projects/SERVI_SINCHI/analyze", json={}, headers=USUARIO).status_code == 202
    assert esperar(client, "SERVI_SINCHI")["trabajo"]["estado"] == "terminado"
    r = client.post("/api/projects/SERVI_SINCHI/epics/approve", json={"epica": "Indicadores"}, headers=USUARIO)
    assert r.json()["aprobadas"] == ["HU-IA-001"]


# ------------------------------------------------------------------ unidades
def test_partir_narrativa():
    assert sb.partir_narrativa("Como analista SIG quiero filtrar escenas por nubosidad para descartar las inservibles.") \
        == ("analista SIG", "filtrar escenas por nubosidad", "descartar las inservibles")
    assert sb.partir_narrativa("como PO, quiero ver el tablero") == ("PO", "ver el tablero", None)
    assert sb.partir_narrativa("Texto libre sin formato") == (None, "Texto libre sin formato", None)
    assert sb.partir_narrativa("") == (None, None, None)


def test_clave_para():
    cfg = sb.SmartBacklogConfig(api_key="x", claves={"SERVI_SINCHI": "SINCHI"})
    assert cfg.clave_para("SERVI_SINCHI") == "SINCHI"
    assert cfg.clave_para("servi_sinchi") == "SINCHI"
    assert cfg.clave_para("MONITOREO_AMAZONIA") == "AMAZONIA"
    assert cfg.clave_para("PRJ001") == "PRJ001"
    assert cfg.clave_para("PRJ001", "pago") == "PAGO"
    with pytest.raises(sb.SmartBacklogError):
        cfg.clave_para("PRJ001", "1MALA")
    with pytest.raises(ValueError, match="SBIA_CLAVES"):
        sb._mapa("SERVI_SINCHI")
    with pytest.raises(ValueError, match="no es una clave"):
        sb._mapa("SERVI_SINCHI=demasiado_larga")


def test_sin_api_key_no_hay_cliente():
    with pytest.raises(sb.SmartBacklogError, match="SBIA_API_KEY"):
        sb.SmartBacklogClient(sb.SmartBacklogConfig(api_key=""))


# --------------------------------------------------------------- publicación
def test_solo_se_publican_las_aprobadas_y_con_trazabilidad(sbia):
    client, eng, fake = sbia
    _analizar_y_aprobar(client)

    plan = client.post("/api/projects/SERVI_SINCHI/smartbacklog/publish", json={"simular": True}).json()
    assert plan["clave"] == "SINCHI" and plan["simulado"] and plan["proyecto_nuevo"]
    acciones = {p["id"]: (p["accion"], p["motivo"]) for p in plan["plan"]}
    assert acciones == {"HU-IA-001": ("crear", ""), "HU-IA-002": ("omitir", "falta la aprobación de una persona")}
    assert fake.ingestas == []                                     # simular no envía nada

    out = client.post("/api/projects/SERVI_SINCHI/smartbacklog/publish", json={}, headers=USUARIO).json()
    assert out["resultado"]["stories_created"] == 1 and out["url"] == "http://sbia.test/p/SINCHI"
    enviada = fake.ingestas[-1]["stories"][0]
    assert enviada["external_id"] == "hu_multiagent:SERVI_SINCHI:HU-IA-001"
    assert enviada["type"] == "HU_IA" and enviada["epic"] == "Indicadores"
    assert enviada["role"] and enviada["need"]
    assert enviada["acceptance_criteria"].startswith("- [ ] ")
    assert enviada["priority_rank"] >= 1 and enviada["priority_method"] == "hu_multiagent:orden_recomendado"
    ref = enviada["source_ref"]
    assert "estimación 5 pts (3–8), ia_factores_v1, por agente, spike 2 pts" in ref and "aprobada por persona" in ref
    hecho = {p["id"]: p for p in out["plan"]}["HU-IA-001"]
    assert hecho["codigo"] == "SINCHI-1" and hecho["estado_remoto"] == "borrador"

    reg = eng.results_store("SERVI_SINCHI").read_json(sb.REGISTRO)
    assert reg["clave"] == "SINCHI" and reg["historias"]["HU-IA-001"]["codigo"] == "SINCHI-1"
    assert reg["por"] == "Germán Ávila"
    ev = [e for e in eng.audit.entries("SERVI_SINCHI") if e.event == "smartbacklog_publish"]
    assert len(ev) == 1 and ev[0].human_approval == "Germán Ávila" and ev[0].details["historias"] == ["HU-IA-001"]


def test_republicar_es_idempotente_y_respeta_lo_editado_alla(sbia):
    client, eng, fake = sbia
    _analizar_y_aprobar(client)
    url = "/api/projects/SERVI_SINCHI/smartbacklog/publish"
    client.post(url, json={})
    assert len(fake.ingestas) == 1

    # sin --actualizar: no se reenvía lo publicado y no se llama a ingest
    again = client.post(url, json={}).json()
    assert {p["id"]: p["motivo"] for p in again["plan"]}["HU-IA-001"] == "ya publicada como SINCHI-1"
    assert again["resultado"] is None and len(fake.ingestas) == 1

    # con --actualizar y sin cambios allá: se actualiza
    up = client.post(url, json={"actualizar": True}).json()
    assert {p["id"]: p["accion"] for p in up["plan"]}["HU-IA-001"] == "actualizar"
    assert up["resultado"]["stories_updated"] == 1

    # alguien la edita en SmartBacklogIA: ya no se pisa
    ext = "hu_multiagent:SERVI_SINCHI:HU-IA-001"
    fake.editar("SINCHI", ext, title="Título ajustado por el PO")
    up = client.post(url, json={"actualizar": True}).json()
    assert {p["id"]: p["motivo"] for p in up["plan"]}["HU-IA-001"] == "alguien la editó en SmartBacklogIA después de publicarla"
    assert next(h for h in fake.historias["SINCHI"] if h["external_id"] == ext)["title"] == "Título ajustado por el PO"

    # y si ya avanzó de estado allá, tampoco
    fake.editar("SINCHI", ext, status="refinada")
    up = client.post(url, json={"actualizar": True}).json()
    assert "ya está en «refinada»" in {p["id"]: p["motivo"] for p in up["plan"]}["HU-IA-001"]


def test_incluir_sin_aprobar_nunca_publica_las_que_esperan_decision(sbia):
    client, eng, fake = sbia
    _analizar_y_aprobar(client)
    out = client.post("/api/projects/SERVI_SINCHI/smartbacklog/publish", json={"incluir_sin_aprobar": True}).json()
    assert out["resumen"] == {"crear": 2, "actualizar": 0, "omitir": 0}
    refs = {s["external_id"]: s["source_ref"] for s in fake.ingestas[-1]["stories"]}
    assert "sin aprobación humana" in refs["hu_multiagent:SERVI_SINCHI:HU-IA-002"]

    plan = sb.planear("P", {"historias": [{"id": "X", "titulo": "t", "estado_ui": "decision"}]}, [],
                      incluir_sin_aprobar=True)
    assert plan[0]["accion"] == "omitir" and plan[0]["motivo"] == "espera una decisión humana"


def test_estado_remoto_muestra_etapas_e_historias(sbia):
    client, eng, fake = sbia
    _analizar_y_aprobar(client)
    antes = client.get("/api/projects/SERVI_SINCHI/smartbacklog").json()
    assert antes["existe"] is False and antes["etapas"] == [] and antes["resumen"]["crear"] == 1
    client.post("/api/projects/SERVI_SINCHI/smartbacklog/publish", json={})
    r = client.get("/api/projects/SERVI_SINCHI/smartbacklog").json()
    assert r["existe"] and r["clave"] == "SINCHI" and [e["stage_number"] for e in r["etapas"]] == [1, 2, 3, 4, 5]
    assert [h["code"] for h in r["historias"]] == ["SINCHI-1"] and r["resumen"]["crear"] == 0


def test_cambiar_de_clave_se_rechaza(sbia):
    client, eng, fake = sbia
    _analizar_y_aprobar(client)
    client.post("/api/projects/SERVI_SINCHI/smartbacklog/publish", json={})
    r = client.post("/api/projects/SERVI_SINCHI/smartbacklog/publish", json={"clave": "OTRA"})
    assert r.status_code == 409 and "SINCHI" in r.json()["detail"]


def test_errores_de_conexion_y_de_clave(sbia, monkeypatch):
    client, eng, fake = sbia
    st = client.get("/api/integracion/smartbacklog").json()
    assert st["conectado"] and st["agente"]["model"] == "gemini-2.5-flash" and "api_key" not in json.dumps(st)

    monkeypatch.setenv("SBIA_API_KEY", "otra")
    st = client.get("/api/integracion/smartbacklog").json()
    assert not st["conectado"] and "API key" in st["error"]
    r = client.post("/api/projects/SERVI_SINCHI/smartbacklog/publish", json={"simular": True})
    assert r.status_code == 502 and "API key" in r.json()["detail"]

    monkeypatch.setenv("SBIA_API_KEY", CLAVE_OK)
    fake.caido = True
    r = client.get("/api/projects/SERVI_SINCHI/smartbacklog")
    assert r.status_code == 502 and "no responde" in r.json()["detail"]

    monkeypatch.delenv("SBIA_API_KEY")
    assert client.get("/api/integracion/smartbacklog").json()["configurado"] is False
    assert client.post("/api/projects/SERVI_SINCHI/smartbacklog/publish", json={}).status_code == 503


def test_consola(sbia, monkeypatch, capsys):
    client, eng, fake = sbia
    _analizar_y_aprobar(client)
    transport = api.SBIA_TRANSPORT
    original = sb.SmartBacklogClient.__init__
    monkeypatch.setattr(sb.SmartBacklogClient, "__init__",
                        lambda self, cfg, transport_=None: original(self, cfg, transport=transport))
    assert sb.main(["publicar", "SERVI_SINCHI", "--simular"]) == 0
    salida = capsys.readouterr().out
    assert "crear      HU-IA-001" in salida and "(simulación: no se envió nada)" in salida and not fake.ingestas
    assert sb.main(["publicar", "SERVI_SINCHI", "--usuario", "consola"]) == 0
    assert "stories_created" in capsys.readouterr().out and len(fake.ingestas) == 1
    assert sb.main(["publicar", "SERVI_SINCHI", "--clave", "9X"]) == 2


def test_fuentes_por_nombre_de_documento():
    gen = {"origen": "generated", "fuente": "/salidas/generated/story_generation.json",
           "evidencia": [{"source": "gs://adk_ing/SERVI _ SINCHI __ Notas.docx"}, {"source": "gs://adk_ing/SERVI _ SINCHI __ Notas.docx"},
                         {"source": "C:\\docs\\acta.md"}]}
    assert sb._fuentes(gen) == ["SERVI _ SINCHI __ Notas.docx", "acta.md"]
    assert sb._fuentes({"origen": "document", "fuente": "gs://b/projects/p/historias.xlsx", "evidencia": []}) == ["historias.xlsx"]
    assert sb._fuentes({"origen": "generated", "fuente": "/x/story_generation.json", "evidencia": []}) == []
