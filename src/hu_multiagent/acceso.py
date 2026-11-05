"""Control de acceso para publicar «Ruta de historias» a otras personas.

``HU_ACCESO`` elige el modo:

- ``abierto`` (por defecto, uso local): sin autenticación; quien decide escribe su nombre en el front.
- ``clave``: usuario y clave por persona (HTTP Basic). ``HU_USUARIOS`` = ``"ana:clave1;pedro:clave2"``
  (en Cloud Run viene de Secret Manager). El usuario autenticado firma las decisiones.
- ``iap``: detrás de Identity-Aware Proxy (Cloud Run ``--iap``). Se verifica la firma del JWT de IAP
  (``X-Goog-IAP-JWT-Assertion``) y el correo de la cuenta de Google firma las decisiones.

En ``clave`` e ``iap`` el encabezado ``X-Usuario`` que envía el navegador se reemplaza por la identidad
autenticada, así nadie puede decidir a nombre de otro.
"""

from __future__ import annotations

import base64
import hmac
import os
import time
from urllib.parse import quote

MODOS = ("abierto", "clave", "iap")
IAP_CERTS = "https://www.gstatic.com/iap/verify/public_key-jwk"
LIBRES = ("/favicon.ico",)


def modo() -> str:
    m = (os.getenv("HU_ACCESO") or "abierto").strip().lower()
    if m not in MODOS:
        raise ValueError(f"HU_ACCESO={m!r}: usa uno de {', '.join(MODOS)}")
    return m


def usuarios_desde_texto(texto: str) -> dict[str, str]:
    """``"ana:clave1;pedro:clave2"`` → ``{"ana": "clave1", ...}``. Usuario sin ``:`` ni ``;``."""
    out: dict[str, str] = {}
    for par in filter(None, (p.strip() for p in (texto or "").replace("\n", ";").split(";"))):
        if ":" not in par:
            raise ValueError("HU_USUARIOS: cada entrada debe ser usuario:clave")
        u, c = par.split(":", 1)
        u, c = u.strip(), c.strip()
        if not u or len(c) < 8:
            raise ValueError(f"HU_USUARIOS: «{u or '?'}» necesita una clave de al menos 8 caracteres")
        out[u] = c
    return out


class _VerificadorIap:
    """Verifica el JWT de IAP con las llaves públicas de Google (con caché corta por token)."""

    def __init__(self, audiencia: str | None) -> None:
        self.audiencia = audiencia or None
        self._cache: dict[str, tuple[float, str]] = {}

    def correo(self, token: str) -> str | None:
        ahora = time.time()
        hit = self._cache.get(token)
        if hit and hit[0] > ahora:
            return hit[1]
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token

        try:
            info = id_token.verify_token(token, g_requests.Request(), audience=self.audiencia, certs_url=IAP_CERTS)
        except Exception:  # noqa: BLE001 — firma, vencimiento o audiencia inválidos
            return None
        if info.get("iss") != "https://cloud.google.com/iap" or not info.get("email"):
            return None
        if len(self._cache) > 500:
            self._cache.clear()
        self._cache[token] = (min(ahora + 300, float(info.get("exp", ahora))), info["email"])
        return info["email"]


class AccesoMiddleware:
    """Middleware ASGI: autentica y fija ``X-Usuario`` y ``X-Acceso`` con la identidad verificada."""

    def __init__(self, app, modo_: str | None = None, usuarios: dict[str, str] | None = None,
                 audiencia_iap: str | None = None, verificador=None) -> None:
        self.app = app
        self.modo = modo_ or modo()
        self.usuarios = usuarios if usuarios is not None else usuarios_desde_texto(os.getenv("HU_USUARIOS", ""))
        if self.modo == "clave" and not self.usuarios:
            raise ValueError("HU_ACCESO=clave exige HU_USUARIOS con al menos un usuario:clave")
        self.iap = verificador or (_VerificadorIap(audiencia_iap or os.getenv("HU_IAP_AUDIENCIA"))
                                   if self.modo == "iap" else None)

    def _clave(self, headers: dict[bytes, bytes]) -> str | None:
        auth = headers.get(b"authorization", b"").decode("latin-1")
        if not auth.lower().startswith("basic "):
            return None
        try:
            u, _, c = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
        except Exception:  # noqa: BLE001
            return None
        esperada = self.usuarios.get(u)
        # compare_digest también con una clave falsa para no revelar qué usuarios existen
        ok = hmac.compare_digest(c.encode(), (esperada or "\0" * 16).encode())
        return u if ok and esperada is not None else None

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or self.modo == "abierto":
            if scope["type"] == "http":
                scope = _con_encabezados(scope, None, "abierto")
            return await self.app(scope, receive, send)
        if scope.get("path") in LIBRES:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        if self.modo == "clave":
            quien = self._clave(headers)
        else:
            token = headers.get(b"x-goog-iap-jwt-assertion", b"").decode("latin-1")
            quien = self.iap.correo(token) if token else None
        if not quien:
            return await _rechazar(scope, send, self.modo)
        return await self.app(_con_encabezados(scope, quien, self.modo), receive, send)


def _con_encabezados(scope, quien: str | None, modo_: str):
    quitar = {b"x-acceso"} | ({b"x-usuario"} if quien else set())
    headers = [(k, v) for k, v in scope.get("headers") or [] if k.lower() not in quitar]
    headers.append((b"x-acceso", modo_.encode()))
    if quien:
        headers.append((b"x-usuario", quote(quien, safe="@.").encode()))
    return {**scope, "headers": headers}


async def _rechazar(scope, send, modo_: str):
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 4401})
        return
    cuerpo = ("Necesitas iniciar sesión con tu usuario y clave de pruebas." if modo_ == "clave"
              else "Acceso denegado: entra con una cuenta de Google autorizada.").encode()
    headers = [(b"content-type", b"text/plain; charset=utf-8")]
    if modo_ == "clave":
        headers.append((b"www-authenticate", b'Basic realm="Ruta de historias", charset="UTF-8"'))
    await send({"type": "http.response.start", "status": 401, "headers": headers})
    await send({"type": "http.response.body", "body": cuerpo})
