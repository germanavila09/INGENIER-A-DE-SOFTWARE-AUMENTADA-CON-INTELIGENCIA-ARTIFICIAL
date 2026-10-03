"""USER_STORY_ANALYST_AGENT: análisis individual de la historia (INVEST, ambigüedades, propuesta)."""

from ...models.user_story import StoryAnalysisOutput
from ..common import K_ANALYSIS, make_llm_agent
from .prompt import instruction


def build_story_analyst(model) -> object:
    return make_llm_agent(
        name="story_analyst_agent",
        description="Analiza una historia de usuario: INVEST, ambigüedades, información faltante y propuesta mejorada.",
        model=model,
        instruction=instruction,
        output_schema=StoryAnalysisOutput,
        output_key=K_ANALYSIS,
    )
