"""QA_TEST_AGENT: casos Given/When/Then y criterios no verificables."""

from ...models.user_story import QATestOutput
from ..common import K_QA, make_llm_agent
from .prompt import instruction


def build_qa_agent(model) -> object:
    return make_llm_agent(
        name="qa_agent",
        description="Convierte criterios de aceptación en casos de prueba Given/When/Then y detecta criterios no verificables.",
        model=model,
        instruction=instruction,
        output_schema=QATestOutput,
        output_key=K_QA,
    )
