"""Herramientas de control del ORCHESTRATOR_AGENT (delegan en el motor determinista)."""

from __future__ import annotations

from ...workflows.engine import ProjectNotFound, get_engine


def _err(exc: Exception) -> dict:
    return {"status": "error", "mensaje": f"{type(exc).__name__}: {exc}"[:600]}


def descubrir_proyectos() -> dict:
    """Descubre los proyectos del bucket de entrada y su estado en el flujo.

    Returns:
        dict con 'proyectos': project_id, nombre, carpeta, si tiene project.yaml,
        estado del flujo, historias registradas y decisiones pendientes.
    """
    try:
        eng = get_engine()
        proyectos = eng.discover()
        out = {"status": "ok", "fuente": eng.input.description, "proyectos": proyectos}
        if not proyectos:
            out["sugerencia"] = (f"No hay archivos en {eng.input.description}. Revisa HU_INPUT_URI en el .env "
                                 "o pregunta a agente_documentos qué hay en el bucket.")
        return out
    except Exception as exc:
        return _err(exc)


async def analizar_proyecto(project_id: str, historias: str = "", forzar: bool = False,
                            regenerar_historias: bool = False) -> dict:
    """Ejecuta el flujo completo sobre un proyecto: manifest, ingesta, contexto y análisis
    multiagente de las historias pendientes, con evaluación HITL. Si el proyecto no tiene
    historias escritas, story_generator_agent las genera desde sus documentos (actas,
    notas, propuestas) y luego se evalúan igual que las demás.

    Args:
        project_id: ID del proyecto (p. ej. PRJ001, SERVI_SINCHI) o el nombre como lo escribió el usuario.
        historias: Opcional. IDs separados por coma para limitar el análisis (p. ej. "US-001,US-003").
        forzar: True para reanalizar también historias ya terminadas.
        regenerar_historias: True para volver a generar el backlog de un proyecto sin historias escritas.

    Returns:
        dict con el estado final, historias listas, en revisión, esperando decisión humana
        (con el detalle de cada solicitud), errores y ubicación de reportes y artefactos.
    """
    try:
        ids = [h for h in historias.split(",") if h.strip()] if historias else None
        return await get_engine().analyze_project(project_id, story_ids=ids, force=forzar,
                                                  regenerate=regenerar_historias)
    except ProjectNotFound as exc:
        return {"status": "error", "mensaje": str(exc)}
    except Exception as exc:
        return _err(exc)


def estado_proyecto(project_id: str) -> dict:
    """Estado persistido del proyecto: máquina de estados, historias, decisiones y última ejecución.

    Args:
        project_id: ID del proyecto o nombre de su carpeta.
    """
    try:
        return get_engine().project_status(project_id)
    except Exception as exc:
        return _err(exc)


def explicar_historia(project_id: str, story_id: str) -> dict:
    """Reconstruye por qué una historia terminó con su estado y recomendación: transiciones,
    revisiones por agente, conflictos, evaluación HITL, decisiones humanas, versiones y auditoría.

    Args:
        project_id: ID del proyecto o nombre de su carpeta.
        story_id: ID de la historia, p. ej. US-001.
    """
    try:
        return get_engine().explain_story(project_id, story_id)
    except Exception as exc:
        return _err(exc)
