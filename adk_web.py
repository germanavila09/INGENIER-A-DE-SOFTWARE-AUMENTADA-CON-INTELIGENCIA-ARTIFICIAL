"""Lanza ADK Web con los agentes del bucket gs://adk_ing y el Smart Product Backlog.

Uso (desde cualquier carpeta, o con el botón Run de VS Code):
    python adk_web.py
    python adk_web.py --port 8080 --no-browser

Equivale a `adk web agents`, pero además:
  - carga el .env de la raíz del repo aunque VS Code ejecute desde otra carpeta,
  - revisa credenciales y permisos sobre el bucket antes de arrancar,
  - publica la API del Smart Product Backlog en /api y su interfaz en /spb,
  - abre el navegador en /spb cuando el servidor está listo (/dev-ui sigue disponible).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENTS_DIR = ROOT / "agents"
SPB_HTML = ROOT / "web" / "spb" / "index.html"   # front del Smart Product Backlog

# VS Code ejecuta desde su propia carpeta: se trabaja siempre desde la raíz del repo.
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))  # permite correr sin `pip install -e .`

try:
    import uvicorn
    from dotenv import load_dotenv
    from google.adk.cli.fast_api import get_fast_api_app
except ImportError as exc:
    sys.exit(
        f"Falta una dependencia ({exc.name}). Instala el proyecto con este mismo intérprete:\n"
        f'  "{sys.executable}" -m pip install -e "{ROOT}"'
    )

load_dotenv(ROOT / ".env", override=False)
# Valores por defecto para Gemini en Vertex AI si no hay .env.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "servi-modelos-ia-dev")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")


def verificar_entorno() -> None:
    """Avisa de los problemas típicos sin impedir que arranque la interfaz."""
    import importlib.util

    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    from adk_ing.bucket import crear_fuente
    from adk_ing.config import get_settings

    s = get_settings()
    local = os.getenv("DOCS_LOCAL_DIR", "").strip()
    origen = f"carpeta local {local}" if local else f"gs://{s.bucket}/{s.prefix}"
    print(f"Proyecto: {os.environ['GOOGLE_CLOUD_PROJECT']}  |  Documentos: {origen}")
    print(f"Orquestador HU: proyectos en {os.getenv('HU_INPUT_URI', 'gs://adk_ing/')}"
          f"  →  resultados en {os.getenv('HU_RESULTS_URI', 'salidas_hu')}")

    if not (ROOT / ".env").exists():
        print("  ! No hay .env; se usan valores por defecto (copia .env.example a .env para cambiarlos).")

    if not SPB_HTML.exists():
        print(f"  ! No está {SPB_HTML}: actualiza el repo (git pull) para tener la interfaz /spb.")

    # find_spec solo comprueba que estén instaladas, sin cargarlas: importar openpyxl arrastra
    # numpy y en Windows eso puede tardar mucho la primera vez (antivirus revisando sus DLL).
    faltan = []
    for modulos, paquete in [(("pypdf",), "pypdf"), (("docx",), "python-docx"), (("pptx",), "python-pptx"),
                             (("openpyxl",), "openpyxl"), (("yaml",), "pyyaml"),
                             (("python_multipart", "multipart"), "python-multipart")]:
        if not any(importlib.util.find_spec(m) for m in modulos):
            faltan.append(paquete)
    if faltan:
        print(
            f"  ! Faltan librerías: {', '.join(faltan)}. Ejecuta:\n"
            f'      "{sys.executable}" -m pip install -e "{ROOT}"'
        )

    try:
        google.auth.default()
    except DefaultCredentialsError:
        print(
            "  ! No hay credenciales de Google. Ejecuta:\n"
            "      gcloud auth application-default login\n"
            f"      gcloud auth application-default set-quota-project {os.environ['GOOGLE_CLOUD_PROJECT']}"
        )
        if not local:
            return

    try:
        objs = crear_fuente().list(max_results=50)
        print(f"  OK acceso a los documentos ({len(objs)} archivo(s) en la primera página).")
    except Exception as exc:  # Forbidden, NotFound, carpeta inexistente, etc.
        detalle = str(exc).splitlines()[0][:200]
        print(f"  ! No se pudo listar {origen}: {detalle}")
        if not local:
            print(
                "    Tu cuenta necesita roles/storage.objectViewer sobre el bucket. "
                "La interfaz arrancará igual, pero el agente responderá con este error."
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="ADK Web con el agente del bucket")
    parser.add_argument("--host", default=os.getenv("ADK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ADK_PORT", "8000")))
    parser.add_argument("--no-browser", action="store_true", help="no abrir el navegador")
    parser.add_argument("--skip-checks", action="store_true", help="omitir la verificación inicial")
    args = parser.parse_args()

    if not args.skip_checks:
        verificar_entorno()

    port = puerto_libre(args.host, args.port)
    url = f"http://{args.host}:{port}"

    @asynccontextmanager
    async def lifespan(app):
        print(f"\nADK Web listo en {url}/dev-ui  (Ctrl+C para detener)", flush=True)
        print(f"Smart Product Backlog en {url}/spb\n", flush=True)
        if not args.no_browser:
            threading.Timer(1.0, webbrowser.open, args=(f"{url}/spb",)).start()
        yield

    print("\nArrancando el servidor… (la primera vez puede tardar unos segundos)", flush=True)
    try:
        app = crear_app(host=args.host, port=port, lifespan=lifespan)
    except Exception as exc:  # noqa: BLE001 — mensaje claro en lugar de un traceback largo
        import traceback

        traceback.print_exc()
        sys.exit(f"\nNo se pudo crear la aplicación: {type(exc).__name__}: {exc}\n"
                 f'Reinstala las dependencias con: "{sys.executable}" -m pip install -e "{ROOT}"')
    uvicorn.run(app, host=args.host, port=port)  # sin --reload: no funciona en Windows


def puerto_libre(host: str, port: int) -> int:
    """El puerto pedido o el siguiente libre (p. ej. si quedó abierta otra ejecución)."""
    import socket

    for p in range(port, port + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
            except OSError:
                if p == port:
                    print(f"  ! El puerto {port} está ocupado (¿quedó abierta otra ejecución de adk_web.py "
                          "en otra terminal?). Busco uno libre…")
                continue
        return p
    sys.exit(f"No hay puertos libres entre {port} y {port + 9}. Cierra las otras ejecuciones o usa --port.")


def crear_app(host: str = "127.0.0.1", port: int = 8000, lifespan=None, **kwargs):
    """ADK Web (agentes + /dev-ui) + API del Smart Product Backlog (/api) + front (/spb)."""
    from fastapi.responses import FileResponse, RedirectResponse

    from hu_multiagent.api import router as spb_api

    opciones = dict(
        # Opcionales: p. ej. ADK_ARTIFACT_URI=gs://adk_ing guarda en el bucket los archivos
        # que generen las sesiones (requiere permiso de escritura); vacío = carpeta local .adk
        artifact_service_uri=os.getenv("ADK_ARTIFACT_URI") or None,
        session_service_uri=os.getenv("ADK_SESSION_URI") or None,
    )
    opciones.update(kwargs)
    app = get_fast_api_app(agents_dir=str(AGENTS_DIR), web=True, host=host, port=port, lifespan=lifespan, **opciones)
    app.include_router(spb_api)

    @app.get("/spb", include_in_schema=False)
    def spb():
        return FileResponse(SPB_HTML, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/spb/", include_in_schema=False)
    def spb_slash():
        return RedirectResponse("/spb")

    return app


if __name__ == "__main__":
    main()
