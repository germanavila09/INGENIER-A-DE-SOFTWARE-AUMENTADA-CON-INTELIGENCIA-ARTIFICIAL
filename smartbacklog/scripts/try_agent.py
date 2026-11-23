"""Prueba manual del proveedor de IA, sin levantar la API.

Uso (con AGENT_PROVIDER, GOOGLE_API_KEY y AGENT_MODEL en .env o en el entorno):
    python scripts/try_agent.py --list-models
    python scripts/try_agent.py proceso-pago.bpmn --project PAGO            # AGENT_DOCS_DIR/PAGO/ o gs://.../PAGO/
    python scripts/try_agent.py proceso-pago.bpmn --project PAGO --refine-sample

El documento se lee solo de la fuente configurada (AGENT_DOCS_SOURCE=folder o gcs), nunca de otra ruta.
    python scripts/try_agent.py proceso-pago.bpmn --project PAGO --save      # guarda las HU en borrador

Dentro de Docker:
    docker compose exec app python scripts/try_agent.py proceso-pago.bpmn --project PAGO

Por defecto no escribe en la base: solo imprime las HU propuestas, tokens y costo estimado.
Con --save, las HU se guardan en el proyecto (en borrador) con el mismo flujo que la API.
Reprocesar el mismo documento actualiza las HU existentes; no duplica.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.ports import AgentNotConfigured, GenerationInput, RefinementInput  # noqa: E402
from app.agents.registry import get_provider  # noqa: E402
from app.agents.runner import run_out, start_generation  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services.documents import get_document_source, load_document_text  # noqa: E402
from app.services.errors import AppError  # noqa: E402


def list_models() -> None:
    import os

    from google import genai

    key = settings.google_api_key or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise SystemExit("Falta GOOGLE_API_KEY")
    client = genai.Client(api_key=key)
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if "generateContent" in actions or not actions:
            print(model.name)


def read_document(reference: str, project_key: str) -> tuple[str, str]:
    """Devuelve (nombre, texto). Lee SOLO de la fuente configurada (AGENT_DOCS_SOURCE): carpeta O bucket."""
    path = Path(reference)
    # Acepta documents/PAGO/archivo.bpmn o gs://.../PAGO/archivo.bpmn: se usa solo el nombre del archivo.
    if path.parent.name.upper() == project_key.upper():
        reference = path.name
    source = get_document_source(project_key.upper())
    print(f"Fuente de documentos ({settings.agent_docs_source}): {source.location}")
    return reference, load_document_text(reference, source.read_bytes(reference), settings.agent_max_doc_chars)


def save_run(name: str, text: str, project_key: str) -> None:
    """Usa el mismo AgentRunner que la API: crea el run y guarda las HU en la base."""
    from app.models import Project
    from sqlalchemy import select

    db = SessionLocal()
    try:
        project = db.scalar(select(Project).where(Project.key == project_key))
        if project is None:
            raise SystemExit(f"El proyecto {project_key} no existe; créalo antes (ingesta o UI)")
        run = start_generation(
            db, project, source_name=name, text=text, instructions=None,
            requested_by_id=None, background=None,
        )
        result = run_out(run)
        print(f"Run {result['id']} · estado: {result['status']} · proveedor: {result['provider']} / {result['model']}")
        if result["status"] == "failed":
            raise SystemExit(f"Falló: {result['error']}")
        created, updated = result["result"]["created"], result["result"]["updated"]
        print(f"HU creadas: {created or 'ninguna'}")
        print(f"HU actualizadas: {updated or 'ninguna'}")
        print(f"Tokens entrada/salida: {result['tokens_in']}/{result['tokens_out']}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("document", nargs="?", help="nombre del archivo en la fuente del proyecto (carpeta o bucket)")
    parser.add_argument("--project", default="PAGO", help="clave del proyecto (carpeta dentro de documents/)")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--refine-sample", action="store_true", help="además refina una HU de ejemplo, sin guardarla")
    parser.add_argument("--save", action="store_true", help="guarda las HU en el proyecto (borrador) en vez de solo mostrarlas")
    args = parser.parse_args()

    if args.list_models:
        list_models()
        return
    if not args.document:
        parser.error("indica un documento o usa --list-models")

    try:
        name, text = read_document(args.document, args.project)
        provider = get_provider()
    except (AppError, AgentNotConfigured) as exc:
        raise SystemExit(f"Error: {getattr(exc, 'message', exc)}")

    if args.save:
        save_run(name, text, args.project.upper())
        return

    print(f"Proveedor: {provider.name} · modelo: {provider.model} · documento: {name} ({len(text)} caracteres)\n")

    output, usage = provider.generate_stories(
        GenerationInput(project_key=args.project.upper(), project_name=args.project.upper(), source_name=name, text=text)
    )
    print(json.dumps(output.model_dump(), ensure_ascii=False, indent=2))
    print(f"\nHU propuestas: {len(output.stories)} · tokens entrada {usage.tokens_in} / salida {usage.tokens_out}")
    if settings.agent_price_in_per_1m is not None and settings.agent_price_out_per_1m is not None:
        cost = (usage.tokens_in * settings.agent_price_in_per_1m + usage.tokens_out * settings.agent_price_out_per_1m) / 1e6
        print(f"Costo estimado: USD {cost:.6f}")

    if args.refine_sample and output.stories:
        first = output.stories[0]
        story = {"code": "EJEMPLO-1", "epic": first.epic, "title": first.title, "role": first.role,
                 "need": first.need, "benefit": first.benefit,
                 "acceptance_criteria": first.acceptance_criteria, "type": first.type, "version": 1}
        refined, _ = provider.refine_story(RefinementInput(story=story, instructions="Agrega casos de error"))
        print("\nRefinamiento de muestra:\n" + json.dumps(refined.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
