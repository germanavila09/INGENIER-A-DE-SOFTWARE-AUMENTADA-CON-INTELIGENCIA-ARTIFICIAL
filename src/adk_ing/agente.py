"""Agente del bucket: busca, lee y responde sobre los documentos de gs://adk_ing.

Vive en el paquete (no en agents/) para poder usarse de dos formas:
- como app propia de ADK Web (agents/agente_bucket/agent.py → root_agent);
- como herramienta del orquestador multiagente (AgentTool), para que ambos «se hablen».

Para probar con una carpeta local en lugar del bucket: DOCS_LOCAL_DIR=docs_ejemplo
"""

from __future__ import annotations

import os

from google.adk.agents import Agent

from .bucket import crear_fuente
from .documentos import MIME_GEMINI, es_soportado, extension
from .indice import IndiceDocumentos

MAX_MB_GEMINI = 18  # límite para enviar un archivo completo a Gemini en la misma petición

_indice: IndiceDocumentos | None = None


def _get_indice() -> IndiceDocumentos:
    # Se crea al primer uso para que importar el agente no exija credenciales.
    global _indice
    if _indice is None:
        _indice = IndiceDocumentos(crear_fuente())
    return _indice


def _error(exc: Exception) -> dict:
    return {"status": "error", "mensaje": f"{type(exc).__name__}: {exc}"[:500]}


# ------------------------------------------------------------------ herramientas
def listar_documentos(prefijo: str = "", maximo: int = 200) -> dict:
    """Lista los documentos disponibles en el bucket con su tipo, tamaño y fecha.

    Args:
        prefijo: Carpeta o prefijo a filtrar, por ejemplo "contratos/". Vacío = todo el bucket.
        maximo: Número máximo de documentos a devolver.

    Returns:
        dict con 'documentos'. 'legible' indica si se puede extraer su texto; los que no,
        pueden consultarse con consultar_documento_con_gemini si son PDF o imágenes.
    """
    try:
        fuente = _get_indice().fuente
        objs = fuente.list(prefix=prefijo or None, max_results=maximo)
        docs = []
        for o in objs:
            d = o.as_dict()
            d["legible"] = es_soportado(o.name)
            d["gemini"] = extension(o.name) in MIME_GEMINI
            docs.append(d)
        return {"status": "ok", "fuente": fuente.descripcion, "total": len(docs), "documentos": docs}
    except Exception as exc:
        return _error(exc)


def buscar_en_documentos(consulta: str, max_resultados: int = 8, documento: str = "") -> dict:
    """Busca en el contenido de todos los documentos del bucket y devuelve los fragmentos más relevantes.

    Úsala primero para cualquier pregunta sobre el contenido de los documentos.
    Los documentos ingestados recientemente se incorporan solos.

    Args:
        consulta: Palabras clave o pregunta en lenguaje natural.
        max_resultados: Número de fragmentos a devolver (por defecto 8).
        documento: Opcional. Nombre exacto de un documento para buscar solo dentro de él.

    Returns:
        dict con 'resultados': documento, ubicación (página, hoja, diapositiva o parte),
        sección y el fragmento de texto.
    """
    try:
        indice = _get_indice()
        cambios = indice.refrescar()
        resultados = indice.buscar(consulta, k=max_resultados, documento=documento or None)
        respuesta = {"status": "ok", "consulta": consulta, "resultados": resultados}
        if cambios["nuevos"] or cambios["actualizados"]:
            respuesta["documentos_recien_indexados"] = cambios["nuevos"] + cambios["actualizados"]
        if not resultados:
            respuesta["nota"] = (
                "Sin coincidencias. Prueba con sinónimos u otras palabras clave, o revisa "
                "estado_del_indice por si hay documentos escaneados sin texto."
            )
        return respuesta
    except Exception as exc:
        return _error(exc)


def leer_documento(nombre: str, desde_seccion: int = 1, max_caracteres: int = 15000) -> dict:
    """Lee el texto de un documento (PDF, Word, Excel, PowerPoint, CSV, texto) por secciones.

    Una sección es una página (PDF), diapositiva (PowerPoint), hoja (Excel) o parte (resto).
    Si el documento es largo, la respuesta indica desde qué sección continuar.

    Args:
        nombre: Ruta completa del documento en el bucket, tal como la devuelve listar_documentos.
        desde_seccion: Primera sección a leer (empieza en 1).
        max_caracteres: Máximo de caracteres a devolver en esta llamada.

    Returns:
        dict con las secciones leídas y 'continuar_desde' si quedó texto por leer.
    """
    try:
        doc = _get_indice().documento(nombre)
    except Exception as exc:
        return _error(exc)

    total = len(doc.secciones)
    salida, usados, siguiente = [], 0, None
    for sec in doc.secciones[max(desde_seccion, 1) - 1 :]:
        if salida and usados + len(sec.texto) > max_caracteres:
            siguiente = sec.numero
            break
        texto = sec.texto
        if len(texto) > max_caracteres:  # una sola sección enorme
            texto = texto[:max_caracteres] + " […truncado]"
        salida.append({"seccion": sec.numero, "ubicacion": sec.etiqueta, "texto": texto})
        usados += len(texto)

    respuesta = {
        "status": "ok",
        "documento": nombre,
        "formato": doc.formato,
        "total_secciones": total,
        "secciones": salida,
    }
    if siguiente:
        respuesta["continuar_desde"] = siguiente
    if doc.aviso:
        respuesta["aviso"] = doc.aviso
    if not doc.tiene_texto and extension(nombre) in MIME_GEMINI:
        respuesta["sugerencia"] = "Usa consultar_documento_con_gemini para leerlo como imagen."
    return respuesta


def estado_del_indice() -> dict:
    """Muestra qué documentos están indexados, cuántas secciones tienen y cuáles dieron error o no tienen texto.

    Returns:
        dict con 'documentos' y totales.
    """
    try:
        indice = _get_indice()
        indice.refrescar(forzar=True)
        docs = indice.estado()
        return {
            "status": "ok",
            "fuente": indice.fuente.descripcion,
            "indexados": sum(1 for d in docs if d["caracteres"]),
            "sin_texto_o_error": [d for d in docs if not d["caracteres"]],
            "documentos": docs,
        }
    except Exception as exc:
        return _error(exc)


def consultar_documento_con_gemini(nombre: str, pregunta: str) -> dict:
    """Envía el archivo completo a Gemini para que lo lea visualmente y responda una pregunta.

    Úsala para PDF escaneados, imágenes (png, jpg, webp) o cuando importen tablas, gráficos
    o el diseño de la página. Es más lenta y costosa que buscar_en_documentos y leer_documento.

    Args:
        nombre: Ruta completa del documento en el bucket.
        pregunta: Qué debe responder o extraer Gemini del documento.

    Returns:
        dict con 'respuesta'.
    """
    mime = MIME_GEMINI.get(extension(nombre))
    if not mime:
        return {
            "status": "error",
            "mensaje": f"Gemini no lee '{extension(nombre)}' directamente. Usa leer_documento.",
        }
    try:
        data = _get_indice().fuente.read_bytes(nombre)
    except Exception as exc:
        return _error(exc)
    if len(data) > MAX_MB_GEMINI * 1024 * 1024:
        return {"status": "error", "mensaje": f"El archivo supera {MAX_MB_GEMINI} MB."}
    try:
        from google import genai
        from google.genai import types

        client = genai.Client()
        resp = client.models.generate_content(
            model=os.getenv("ADK_MODEL", "gemini-2.5-flash"),
            contents=[
                types.Part.from_bytes(data=data, mime_type=mime),
                "Responde en español basándote solo en este documento. Indica la página "
                f"cuando sea posible.\n\nPregunta: {pregunta}",
            ],
        )
        return {"status": "ok", "documento": nombre, "respuesta": resp.text}
    except Exception as exc:
        return _error(exc)


INSTRUCCION = """\
Eres un asistente que responde en español usando únicamente los documentos del bucket \
de Cloud Storage del proyecto.

Cómo trabajar:
1. Para preguntas sobre el contenido, llama primero a buscar_en_documentos con palabras \
clave (prueba sinónimos si no hay resultados).
2. Si necesitas más contexto, usa leer_documento con el nombre del documento y la \
sección donde apareció el fragmento.
3. Para saber qué documentos hay, usa listar_documentos; para revisar errores o \
documentos sin texto, estado_del_indice.
4. Si un documento es escaneado o es una imagen, o la pregunta depende de tablas o \
gráficos, usa consultar_documento_con_gemini.

Reglas:
- Cita siempre la fuente así: (documento, página/hoja/diapositiva).
- Si los documentos no contienen la respuesta, dilo; no inventes.
- Si una herramienta devuelve un error, explícalo en una frase y sugiere qué revisar.
- Para resúmenes de un documento completo, léelo por secciones con leer_documento.
"""

def build_bucket_agent(name: str = "agente_bucket", model=None, description: str | None = None) -> Agent:
    """Crea una instancia nueva del agente (un agente ADK solo puede tener un padre)."""
    return Agent(
        name=name,
        model=model or os.getenv("ADK_MODEL", "gemini-2.5-flash"),
        description=description or "Busca, lee y responde preguntas sobre los documentos ingestados en el bucket gs://adk_ing.",
        instruction=INSTRUCCION,
        tools=[
            buscar_en_documentos,
            leer_documento,
            listar_documentos,
            estado_del_indice,
            consultar_documento_con_gemini,
        ],
    )
