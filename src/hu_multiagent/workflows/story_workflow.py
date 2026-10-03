"""Flujo por historia (agentes ADK nativos):

    story_workflow (SequentialAgent)
    ├── story_analyst_agent            (LLM)
    ├── parallel_review (ParallelAgent)
    │   ├── architecture_agent         (LLM)
    │   └── qa_agent                   (LLM)
    ├── review_aggregator_agent        (determinista)
    └── hitl_evaluator_agent           (determinista)

Fase 2: agregar security, requirements_validator y estimation a parallel_review
(una línea cada uno) y dependency a nivel de proyecto.
"""

from __future__ import annotations

from google.adk.agents import ParallelAgent, SequentialAgent

from ..agents.aggregator.agent import build_review_aggregator
from ..agents.architecture.agent import build_architecture_agent
from ..agents.hitl_evaluator.agent import build_hitl_evaluator
from ..agents.qa.agent import build_qa_agent
from ..agents.story_analyst.agent import build_story_analyst

PARALLEL_AGENTS = ("architecture_agent", "qa_agent")


def build_story_workflow(model, confidence_auto: float = 0.85, confidence_review: float = 0.60,
                         generated_requires_approval: bool = False) -> SequentialAgent:
    parallel = ParallelAgent(
        name="parallel_review",
        description="Evaluaciones independientes de la historia en paralelo.",
        sub_agents=[build_architecture_agent(model), build_qa_agent(model)],
    )
    return SequentialAgent(
        name="story_workflow",
        description="Analiza, revisa en paralelo, agrega y evalúa HITL una historia de usuario.",
        sub_agents=[
            build_story_analyst(model),
            parallel,
            build_review_aggregator(),
            build_hitl_evaluator(confidence_auto, confidence_review, generated_requires_approval),
        ],
    )
