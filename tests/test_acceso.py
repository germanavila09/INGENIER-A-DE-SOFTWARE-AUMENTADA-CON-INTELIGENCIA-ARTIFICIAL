"""Acceso al publicar «Ruta de historias»: abierto (local), usuario y clave, o cuenta de Google vía IAP."""

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hu_multiagent import api
from hu_multiagent.acceso import AccesoMiddleware, usuarios_desde_texto


def _app(**kw):
    app = FastAPI()
    app.include_router(api.router)
    app.add_middleware(AccesoMiddleware, **kw)
    return TestClient(app)


def _basic(u, c):
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{c}".encode()).decode()}


def test_abierto_deja_el_nombre_libre():
    c = _app(modo_="abierto", usuarios={})
    assert c.get("/api/yo", headers={"X-Usuario": "Germ%C3%A1n"}).json() == {"acceso": "abierto", "usuario": None}


def test_clave_exige_usuario_y_firma_con_el():
    c = _app(modo_="clave", usuarios={"ana": "clave-larga-1", "pedro.r@uv.co": "otra-clave-2"})
    r = c.get("/api/yo")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert c.get("/api/yo", headers=_basic("ana", "mala-clave")).status_code == 401
    assert c.get("/api/yo", headers=_basic("nadie", "clave-larga-1")).status_code == 401
    # el nombre que mande el navegador no cuenta: firma el usuario autenticado
    r = c.get("/api/yo", headers={**_basic("ana", "clave-larga-1"), "X-Usuario": "Otro", "X-Acceso": "abierto"})
    assert r.json() == {"acceso": "clave", "usuario": "ana"}
    assert c.get("/api/yo", headers=_basic("pedro.r@uv.co", "otra-clave-2")).json()["usuario"] == "pedro.r@uv.co"


def test_iap_usa_el_correo_verificado():
    class Falso:
        def correo(self, token):
            return "tester@gmail.com" if token == "jwt-bueno" else None

    c = _app(modo_="iap", usuarios={}, verificador=Falso())
    assert c.get("/api/yo").status_code == 401
    assert c.get("/api/yo", headers={"X-Goog-IAP-JWT-Assertion": "jwt-falso"}).status_code == 401
    # sin JWT válido, un correo puesto a mano en el encabezado no sirve
    assert c.get("/api/yo", headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jefe@x.co"}).status_code == 401
    r = c.get("/api/yo", headers={"X-Goog-IAP-JWT-Assertion": "jwt-bueno", "X-Usuario": "Otro"})
    assert r.json() == {"acceso": "iap", "usuario": "tester@gmail.com"}


def test_configuracion_invalida():
    with pytest.raises(ValueError, match="al menos 8"):
        usuarios_desde_texto("ana:corta")
    with pytest.raises(ValueError, match="usuario:clave"):
        usuarios_desde_texto("ana")
    assert usuarios_desde_texto("ana:clave-larga-1\npedro:otra-clave-2;") == {"ana": "clave-larga-1", "pedro": "otra-clave-2"}
    with pytest.raises(ValueError, match="HU_USUARIOS"):
        AccesoMiddleware(lambda *a: None, modo_="clave", usuarios={})
