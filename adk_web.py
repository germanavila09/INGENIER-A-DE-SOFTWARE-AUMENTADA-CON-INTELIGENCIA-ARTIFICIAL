"""Lanza ADK Web con el agente que consulta el bucket gs://adk_ing.

Uso (desde cualquier carpeta, o con el botón Run de VS Code):
    python adk_web.py
    python adk_web.py --port 8080 --no-browser

Equivale a `adk web agents`, pero además:
  - carga el .env de la raíz del repo aunque VS Code ejecute desde otra carpeta,
  - revisa credenciales y permisos sobre el bucket antes de arrancar,
  - abre el navegador cuando el servidor está listo.
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
    import importlib

    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    from adk_ing.bucket import crear_fuente
    from adk_ing.config import get_settings

    s = get_settings()
    local = os.getenv("DOCS_LOCAL_DIR", "").strip()
    origen = f"carpeta local {local}" if local else f"gs://{s.bucket}/{s.prefix}"
    print(f"Proyecto: {os.environ['GOOGLE_CLOUD_PROJECT']}  |  Documentos: {origen}")

    if not (ROOT / ".env").exists():
        print("  ! No hay .env; se usan valores por defecto (copia .env.example a .env para cambiarlos).")

    faltan = []
    for modulo, paquete in [("pypdf", "pypdf"), ("docx", "python-docx"), ("pptx", "python-pptx"), ("openpyxl", "openpyxl")]:
        try:
            importlib.import_module(modulo)
        except ImportError:
            faltan.append(paquete)
    if faltan:
        print(
            f"  ! Faltan librerías para leer documentos: {', '.join(faltan)}. Ejecuta:\n"
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

    url = f"http://{args.host}:{args.port}"

    @asynccontextmanager
    async def lifespan(app):
        print(f"\nADK Web listo en {url}  (Ctrl+C para detener)\n")
        if not args.no_browser:
            threading.Timer(1.0, webbrowser.open, args=(url,)).start()
        yield

    app = get_fast_api_app(
        agents_dir=str(AGENTS_DIR),
        web=True,
        host=args.host,
        port=args.port,
        lifespan=lifespan,
        # Opcionales: p. ej. ADK_ARTIFACT_URI=gs://adk_ing guarda en el bucket los archivos
        # que generen las sesiones (requiere permiso de escritura); vacío = carpeta local .adk
        artifact_service_uri=os.getenv("ADK_ARTIFACT_URI") or None,
        session_service_uri=os.getenv("ADK_SESSION_URI") or None,
    )
    uvicorn.run(app, host=args.host, port=args.port)  # sin --reload: no funciona en Windows


if __name__ == "__main__":
    main()
