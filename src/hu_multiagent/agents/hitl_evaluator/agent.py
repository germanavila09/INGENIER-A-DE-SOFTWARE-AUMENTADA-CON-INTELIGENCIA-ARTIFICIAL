"""HITL_EVALUATOR: decide el nivel de intervención humana (determinista)."""

from __future__ import annotations

from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai import types
from pydantic import Field

from ...models.agent_response import AggregatedReview
from ..common import K_AGG, K_HITL, K_STORY
from .policy import evaluate_hitl
from .prompt import DESCRIPTION


class HitlEvaluatorAgent(BaseAgent):
    confidence_auto: float = Field(0.85)
    confidence_review: float = Field(0.60)
    generated_requires_approval: bool = Field(False)

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        st = ctx.session.state
        agg = AggregatedReview.model_validate(st[K_AGG])
        story = st.get(K_STORY) or {}
        ev = evaluate_hitl(
            agg,
            story_approved=bool(story.get("approved")),
            generated=story.get("origin") == "generated",
            generated_requires_approval=self.generated_requires_approval,
            confidence_auto=self.confidence_auto,
            confidence_review=self.confidence_review,
        )
        texto = f"HITL {agg.story_id}: nivel {int(ev.level)} → {ev.action.value}" + (
            " (" + "; ".join(r.code for r in ev.reasons) + ")" if ev.reasons else "")
        yield Event(
            author=self.name, invocation_id=ctx.invocation_id, branch=ctx.branch,
            content=types.Content(role="model", parts=[types.Part.from_text(text=texto)]),
            actions=EventActions(state_delta={K_HITL: ev.model_dump(mode="json")}),
        )


def build_hitl_evaluator(confidence_auto: float, confidence_review: float,
                         generated_requires_approval: bool = False) -> HitlEvaluatorAgent:
    return HitlEvaluatorAgent(
        name="hitl_evaluator_agent", description=DESCRIPTION,
        confidence_auto=confidence_auto, confidence_review=confidence_review,
        generated_requires_approval=generated_requires_approval,
    )
