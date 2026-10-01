"""Agente ADK que consulta el contenido del bucket gs://adk_ing.

Ejecutar desde la raíz del repo:
    adk web agents        # interfaz web
    adk run agents/agente_bucket
"""

from __future__ import annotations

import os

from google.adk.agents import Agent

from adk_ing.bucket import BucketReader

_reader: BucketReader | None = None


def _get_reader() -> BucketReader:
    # Se crea al primer uso para que importar el agente no exija credenciales.
    global _reader
    if _reader is None:
        _reader = BucketReader()
    return _reader


def listar_archivos(prefijo: str = "", maximo: int = 200) -> dict:
    """Lista los archivos del bucket.

    Args:
        prefijo: Carpeta o prefijo a filtrar, por ejemplo "documentos/". Vacío = todo el bucket.
        maximo: Número máximo de archivos a devolver.

    Returns:
        dict con 'status' y 'archivos' (nombre, tamaño, tipo, fecha de actualización).
    """
    try:
        objs = _get_reader().list(prefix=prefijo or None, max_results=maximo)
        return {"status": "ok", "total": len(objs), "archivos": [o.as_dict() for o in objs]}
    except Exception as exc:  # el error se devuelve al modelo para que lo explique
        return {"status": "error", "mensaje": str(exc)}


def leer_archivo(nombre: str, max_caracteres: int = 20000) -> dict:
    """Lee el contenido de un archivo de texto del bucket (txt, md, csv, json, py, sql...).

    Args:
        nombre: Ruta completa del objeto dentro del bucket, tal como la devuelve listar_archivos.
        max_caracteres: Límite de caracteres a devolver; el resto se trunca.

    Returns:
        dict con 'status', 'contenido' y 'truncado'.
    """
    try:
        data = _get_reader().read_bytes(nombre)
    except Exception as exc:
        return {"status": "error", "mensaje": str(exc)}
    try:
        texto = data.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "status": "error",
            "mensaje": f"'{nombre}' no es texto UTF-8 ({len(data)} bytes); probablemente es binario.",
        }
    return {
        "status": "ok",
        "nombre": nombre,
        "contenido": texto[:max_caracteres],
        "truncado": len(texto) > max_caracteres,
    }


root_agent = Agent(
    name="agente_bucket",
    model=os.getenv("ADK_MODEL", "gemini-2.5-flash"),
    description="Responde preguntas usando los archivos del bucket gs://adk_ing.",
    instruction=(
        "Eres un asistente que responde en español usando los archivos del bucket de Cloud Storage. "
        "Usa listar_archivos para ver qué hay y leer_archivo para abrir los que sean relevantes "
        "antes de responder. Cita el nombre del archivo del que sale cada dato. "
        "Si un archivo es binario o la herramienta devuelve un error, dilo con claridad."
    ),
    tools=[listar_archivos, leer_archivo],
)
