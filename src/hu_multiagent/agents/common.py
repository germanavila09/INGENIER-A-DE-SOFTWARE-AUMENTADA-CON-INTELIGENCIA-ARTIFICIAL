"""Piezas comunes de los agentes: fábrica de agentes LLM y callbacks de control."""

from __future__ import annotations

import json
from typing import Callable

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.genai import types

# Claves del estado de sesión del flujo por historia (SESSION MEMORY)
K_PROJECT_ID = "project_id"
K_RUN_ID = "run_id"
K_STORY = "current_story"            # JSON de la historia (dict)
K_CONTEXT = "project_context"        # texto del contexto del proyecto (solo este proyecto)
K_FEEDBACK = "human_feedback"        # corrección humana tras MODIFIED (texto)
K_ANALYSIS = "story_analysis"
K_ARCH = "architecture_review"
K_QA = "qa_review"
K_AGG = "aggregated_review"
K_HITL = "hitl_evaluation"


def state_json(state, key: str, default: str = "(sin datos)") -> str:
    v = state.get(key)
    if v is None:
        return default
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)


def guardia_de_proyecto(callback_context: CallbackContext) -> types.Content | None:
    """before_agent_callback: impide que un agente trabaje con datos de otro proyecto.

    Si la historia no pertenece al project_id de la sesión, el agente no se ejecuta.
    """
    st = callback_context.state
    pid = st.get(K_PROJECT_ID)
    story = st.get(K_STORY) or {}
    if not pid or not story or story.get("project_id") != pid:
        return types.Content(
            role="model",
            parts=[types.Part.from_text(text=f"BLOQUEADO: la historia no pertenece al proyecto {pid!r}.")],
        )
    return None


def sellar_salida(output_key: str) -> Callable[[CallbackContext], None]:
    """after_agent_callback: normaliza la salida estructurada al contrato común.

    - fija story_id al de la historia en curso (el LLM no puede cambiarlo),
    - acota confidence a [0, 1].
    """

    def _cb(callback_context: CallbackContext) -> None:
        st = callback_context.state
        out = st.get(output_key)
        if not isinstance(out, dict):
            return None
        fixed = dict(out)
        story = st.get(K_STORY) or {}
        if story.get("story_id"):
            fixed["story_id"] = story["story_id"]
        try:
            fixed["confidence"] = max(0.0, min(1.0, float(fixed.get("confidence", 0))))
        except (TypeError, ValueError):
            fixed["confidence"] = 0.0
        if fixed != out:
            st[output_key] = fixed
        return None

    return _cb


def make_llm_agent(
    *,
    name: str,
    description: str,
    model,
    instruction: Callable[[ReadonlyContext], str],
    output_schema,
    output_key: str,
) -> LlmAgent:
    """Agente especialista: sin historial de chat, sin transferencias, salida validada por Pydantic."""
    return LlmAgent(
        name=name,
        description=description,
        model=model,
        instruction=instruction,           # InstructionProvider: no se interpolan llaves del contexto
        output_schema=output_schema,
        output_key=output_key,
        include_contents="none",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        before_agent_callback=guardia_de_proyecto,
        after_agent_callback=sellar_salida(output_key),
    )
