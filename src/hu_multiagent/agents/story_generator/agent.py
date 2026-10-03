"""USER_STORY_GENERATOR_AGENT: propone el backlog inicial a partir de actas, notas y propuestas.

Se ejecuta solo cuando el proyecto no tiene historias escritas. Sus historias quedan
con origin="generated" y pasan por el mismo flujo de análisis y evaluación HITL que
cualquier otra; nunca quedan aprobadas sin revisión humana.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from ...models.user_story import StoryGenerationOutput
from ..common import K_PROJECT_ID
from .prompt import K_GEN_CONTEXT, K_GEN_OUT, instruction


def guardia_generacion(callback_context: CallbackContext) -> types.Content | None:
    """before_agent_callback: el contexto debe pertenecer al project_id de la sesión."""
    st = callback_context.state
    pid = st.get(K_PROJECT_ID)
    ctx = st.get(K_GEN_CONTEXT) or ""
    if not pid or not ctx.startswith(f"PROYECTO: {pid} "):
        return types.Content(role="model", parts=[types.Part.from_text(
            text=f"BLOQUEADO: el contexto no pertenece al proyecto {pid!r}.")])
    return None


def acotar_confianza(callback_context: CallbackContext) -> None:
    out = callback_context.state.get(K_GEN_OUT)
    if isinstance(out, dict):
        fixed = dict(out)
        fixed["confidence"] = max(0.0, min(1.0, float(fixed.get("confidence") or 0)))
        fixed["stories"] = [{**s, "confidence": max(0.0, min(1.0, float(s.get("confidence") or 0)))}
                            for s in fixed.get("stories", [])]
        if fixed != out:
            callback_context.state[K_GEN_OUT] = fixed
    return None


def build_story_generator(model) -> LlmAgent:
    return LlmAgent(
        name="story_generator_agent",
        description="Genera historias de usuario con evidencia a partir de los documentos de un proyecto sin backlog.",
        model=model,
        instruction=instruction,
        output_schema=StoryGenerationOutput,
        output_key=K_GEN_OUT,
        include_contents="none",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
        before_agent_callback=guardia_generacion,
        after_agent_callback=acotar_confianza,
    )
