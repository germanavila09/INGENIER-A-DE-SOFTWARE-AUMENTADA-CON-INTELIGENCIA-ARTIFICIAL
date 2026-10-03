"""ARCHITECTURE_AGENT: impacto técnico y detección de cambios críticos."""

from ...models.user_story import ArchitectureReviewOutput
from ..common import K_ARCH, make_llm_agent
from .prompt import instruction


def build_architecture_agent(model) -> object:
    return make_llm_agent(
        name="architecture_agent",
        description="Evalúa el impacto técnico de la historia y marca cambios críticos que requieren aprobación humana.",
        model=model,
        instruction=instruction,
        output_schema=ArchitectureReviewOutput,
        output_key=K_ARCH,
    )
