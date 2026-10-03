"""Herramientas HITL del orquestador y la guardia que impide decisiones no humanas."""

from __future__ import annotations

import json
import unicodedata

from google.adk.tools.tool_context import ToolContext

from ..workflows.engine import get_engine

# Palabras que deben aparecer en el MENSAJE DEL USUARIO para aceptar cada decisión.
DECISION_WORDS = {
    "APPROVED": ("approved", "approve", "aprob", "apruebo", "apruebe", "aprueba", "acepto", "aceptad"),
    "REJECTED": ("rejected", "reject", "rechaz"),
    "MODIFIED": ("modified", "modific", "cambi", "ajust", "corrig", "respuesta", "respond"),
    "NEEDS_MORE_INFORMATION": ("needs_more_information", "more information", "mas informacion", "mas info",
                               "falta informacion", "necesito informacion", "needs more"),
}
MAX_TOOL_CALLS_PER_TURN = 10


def _plain(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _user_text(tool_context) -> str:
    content = getattr(tool_context, "user_content", None)
    if not content or not content.parts:
        return ""
    return _plain(" ".join(p.text or "" for p in content.parts))


def guardia_orquestador(tool, args: dict, tool_context) -> dict | None:
    """before_tool_callback del orquestador.

    1. Límite de llamadas a herramientas por turno (evita loops del LLM).
    2. registrar_decision_humana solo procede si la decisión está escrita por el
       usuario en su último mensaje: el modelo no puede aprobar por su cuenta.
    """
    n = int(tool_context.state.get("temp:llamadas_herramientas", 0)) + 1
    tool_context.state["temp:llamadas_herramientas"] = n
    if n > MAX_TOOL_CALLS_PER_TURN:
        return {"status": "bloqueado", "mensaje": f"Límite de {MAX_TOOL_CALLS_PER_TURN} herramientas por turno. "
                                                  "Resume lo obtenido y pregunta al usuario cómo seguir."}
    if tool.name != "registrar_decision_humana":
        return None

    texto = _user_text(tool_context)
    decision = str(args.get("decision", "")).strip().upper()
    if decision not in DECISION_WORDS:
        return {"status": "error", "mensaje": f"Decisión inválida. Usa una de: {', '.join(DECISION_WORDS)}."}
    if not any(w in texto for w in DECISION_WORDS[decision]):
        return {"status": "bloqueado",
                "mensaje": f"La decisión {decision} no está en el mensaje del usuario. Las decisiones HITL solo "
                           "las toma un humano: muéstrale la solicitud y pídele que responda explícitamente."}
    did = str(args.get("decision_id", "")).strip()
    if _plain(did) not in texto:
        pendientes = get_engine().pending_decisions()
        if not (len(pendientes) == 1 and pendientes[0].decision_id == did):
            return {"status": "bloqueado",
                    "mensaje": "Hay varias decisiones pendientes o el usuario no indicó cuál. Pídele el decision_id."}
    return None


def listar_decisiones_pendientes(project_id: str = "") -> dict:
    """Lista las decisiones humanas pendientes (historias en WAITING_FOR_HUMAN).

    Args:
        project_id: Opcional. Limita a un proyecto (id o carpeta). Vacío = todos.

    Returns:
        dict con 'decisiones': cada una con decision_id, historia, motivo, recomendación,
        alternativas, preguntas bloqueantes, fuentes, riesgo y confianza.
    """
    try:
        pend = get_engine().pending_decisions(project_id or None)
        return {"status": "ok", "total": len(pend), "decisiones": [p.model_dump(mode="json") for p in pend]}
    except Exception as exc:
        return {"status": "error", "mensaje": f"{type(exc).__name__}: {exc}"}


async def registrar_decision_humana(
    decision_id: str,
    decision: str,
    comentario: str = "",
    modificaciones_json: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Registra la decisión del humano sobre una solicitud WAITING_FOR_HUMAN y reanuda el flujo.

    Úsala SOLO cuando el usuario haya escrito explícitamente su decisión en su último mensaje.

    Args:
        decision_id: ID de la decisión, p. ej. D-PRJ001-US-001-01.
        decision: APPROVED, REJECTED, MODIFIED o NEEDS_MORE_INFORMATION.
        comentario: Justificación o comentario del usuario.
        modificaciones_json: Para MODIFIED: JSON con los cambios del usuario. Claves posibles:
            "title", "narrative", "acceptance_criteria" (lista), "business_rules" (lista),
            "answers" (objeto pregunta → respuesta).

    Returns:
        dict con el resultado y, si aplica, el resultado de la reanudación automática.
    """
    mods = {}
    if modificaciones_json.strip():
        try:
            mods = json.loads(modificaciones_json)
            if not isinstance(mods, dict):
                raise ValueError("debe ser un objeto JSON")
        except ValueError as exc:
            return {"status": "error", "mensaje": f"modificaciones_json inválido: {exc}"}
    user = getattr(tool_context, "user_id", None) or "usuario"
    try:
        return await get_engine().apply_decision(decision_id, decision, comentario, mods, decided_by=user)
    except Exception as exc:
        return {"status": "error", "mensaje": f"{type(exc).__name__}: {exc}"}
