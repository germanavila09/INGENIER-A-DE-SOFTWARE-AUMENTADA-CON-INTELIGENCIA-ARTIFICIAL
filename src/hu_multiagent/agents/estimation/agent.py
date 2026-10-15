"""ESTIMATION_AGENT: tipo de historia (HU-IA / HU-Tradicional) y factores de esfuerzo 1-5.

Corre en paralelo con architecture_agent y qa_agent. Los puntos no los decide el modelo:
review_aggregator_agent los calcula con services/estimation.py.
"""

from ...models.user_story import EstimationOutput
from ..common import K_EST, make_llm_agent
from .prompt import instruction


def build_estimation_agent(model) -> object:
    return make_llm_agent(
        name="estimation_agent",
        description="Clasifica la historia como HU-IA o HU-Tradicional y puntúa sus factores de esfuerzo (1-5).",
        model=model,
        instruction=instruction,
        output_schema=EstimationOutput,
        output_key=K_EST,
    )
