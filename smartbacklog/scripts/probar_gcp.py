"""Prueba de punta a punta contra GCP, con la app corriendo: Vertex AI, bucket y generación con Gemini.

Uso (dentro del contenedor, con la configuración de .env y docker-compose.gcp.yml):
    docker compose exec app python scripts/probar_gcp.py [CLAVE ...]

Para cada clave (por defecto, las de AGENT_DOCS_MAP): crea el proyecto si no existe, lista sus
documentos en el bucket y pide al agente que genere HU leyendo esos documentos. Las HU quedan en
«Borrador», como siempre; ninguna etapa HITL se aprueba. Sale con código distinto de 0 si algo falla.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

BASE = os.getenv("SPB_URL", "http://localhost:8000")
EMAIL = os.getenv("SEED_ADMIN_EMAIL", "admin@spb.local")
PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "cambiar-me-ya")
MAX_WAIT_S = int(os.getenv("PROBAR_MAX_SEGUNDOS", "900"))


def probar(c: httpx.Client, key: str) -> bool:
    print(f"\n=== {key} ===")
    if c.get(f"/api/v1/projects/{key}").status_code == 404:
        r = c.post("/api/v1/projects", json={"key": key, "name": f"{key} (bucket {settings.agent_docs_bucket})",
                                             "description": "Creado por scripts/probar_gcp.py"})
        print("proyecto creado:", r.status_code, r.text[:200] if r.status_code >= 400 else "")
        if r.status_code >= 400:
            return False
    docs = c.get(f"/api/v1/projects/{key}/agents/documents")
    if docs.status_code != 200:
        print("no se pudo listar el bucket:", docs.status_code, docs.text[:400])
        return False
    data = docs.json()
    print("fuente:", data["source"], data["folder"])
    for d in data["documents"]:
        print(f"  - {d['name']} ({d['size']} bytes)")
    if not data["documents"]:
        print("sin documentos soportados (PDF, DOCX, BPMN, MD, TXT) en ese prefijo")
        return False
    run = c.post(f"/api/v1/projects/{key}/agents/generate-stories/from-documents", json={})
    if run.status_code >= 400:
        print("no se pudo lanzar la generación:", run.status_code, run.text[:400])
        return False
    run = run.json()
    print(f"ejecución {run['id']} · {run['provider']} · {run['model']}")
    t0 = time.monotonic()
    while run["status"] in ("queued", "running") and time.monotonic() - t0 < MAX_WAIT_S:
        time.sleep(5)
        run = c.get(f"/api/v1/agents/runs/{run['id']}").json()
    print(f"estado: {run['status']} en {int(time.monotonic() - t0)} s · tokens {run['tokens_in']}/{run['tokens_out']}"
          f" · costo USD {run['cost_usd']}")
    if run["status"] != "succeeded":
        print("error:", run["error"])
        return False
    print("resultado:", run["result"])
    stories = c.get(f"/api/v1/projects/{key}/stories").json()
    items = stories["items"] if isinstance(stories, dict) and "items" in stories else stories
    for s in items[:20]:
        print(f"  {s.get('code')} [{s.get('status')}] {s.get('title')}")
    return True


def main() -> int:
    keys = [k.upper() for k in sys.argv[1:]] or list(settings.docs_map)
    if not keys:
        print("Indica una clave (python scripts/probar_gcp.py CLAVE) o define AGENT_DOCS_MAP")
        return 2
    with httpx.Client(base_url=BASE, timeout=60) as c:
        r = c.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        if r.status_code != 200:
            print("login falló:", r.status_code, "(¿corriste scripts/seed.py?)")
            return 2
        status = c.get("/api/v1/agents/status").json()
        print("proveedor:", {k: status.get(k) for k in ("provider", "model", "backend", "configured", "reason", "docs_source")})
        if not status.get("configured"):
            return 2
        resultados = {key: probar(c, key) for key in keys}
    print("\nresumen:", resultados)
    return 0 if all(resultados.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
