"""REVIEW_AGGREGATOR_AGENT: combina revisiones y detecta conflictos (determinista)."""

from __future__ import annotations

from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai import types

from ...models.agent_response import AgentResponse, AgentStatus, AggregatedReview
from ...models.common import Finding, Severity, Statement, StatementType
from ...models.user_story import ArchitectureReviewOutput, QATestOutput, ReviewStatus, StoryAnalysisOutput
from ..common import K_AGG, K_ANALYSIS, K_ARCH, K_PROJECT_ID, K_QA, K_STORY
from .prompt import DESCRIPTION

ORDER = [AgentStatus.OK, AgentStatus.APPROVED, AgentStatus.APPROVED_WITH_OBSERVATIONS,
         AgentStatus.NEEDS_CHANGES, AgentStatus.BLOCKED, AgentStatus.CRITICAL_RISK, AgentStatus.ERROR]
APPROVING = {AgentStatus.APPROVED, AgentStatus.APPROVED_WITH_OBSERVATIONS}


def _sources(*groups) -> list[str]:
    out = []
    for g in groups:
        for s in g:
            src = getattr(s, "source", None) or ""
            srcs = getattr(s, "sources", None) or ([src] if src else [])
            for x in srcs:
                if x and x not in out:
                    out.append(x)
    return out


def analyst_response(pid: str, a: StoryAnalysisOutput) -> AgentResponse:
    blocking = [q for q in a.missing_information if q.blocking]
    if blocking or a.requires_hitl:
        status = AgentStatus.NEEDS_CHANGES
    elif a.ambiguities or a.suggested_changes or a.missing_information:
        status = AgentStatus.APPROVED_WITH_OBSERVATIONS
    else:
        status = AgentStatus.APPROVED
    findings = [Finding(category="ambiguity", description=s.statement, severity=Severity.MEDIUM, type=s.type,
                        sources=[s.source] if s.source else []) for s in a.ambiguities]
    findings += [Finding(category="missing_information", description=q.question,
                         severity=Severity.HIGH if q.blocking else Severity.MEDIUM,
                         type=StatementType.UNKNOWN, sources=q.sources) for q in a.missing_information]
    return AgentResponse(
        agent="story_analyst_agent", project_id=pid, story_id=a.story_id, status=status,
        confidence=a.confidence, requires_hitl=a.requires_hitl, summary=a.summary, findings=findings,
        recommendations=[Statement(statement=f"{c.field}: {c.after} ({c.reason})", type=StatementType.RECOMMENDATION)
                         for c in a.suggested_changes],
        risks=a.risks, dependencies=a.dependencies,
        sources=_sources(a.ambiguities, a.business_rules, a.dependencies, a.constraints, a.missing_information, a.risks),
        next_agent="parallel_review",
        metadata={"invest_score": a.invest_score.clamp().model_dump(), "quality_score": a.invest_score.quality_score,
                  "hitl_reason": a.hitl_reason, "improved_story": a.improved_story.model_dump()},
    )


def architecture_response(pid: str, r: ArchitectureReviewOutput) -> AgentResponse:
    findings = [Finding(category=f"impact:{i.area.value}", description=i.description, severity=Severity.LOW,
                        type=i.type, sources=[i.source] if i.source else []) for i in r.impacts]
    findings += [Finding(category="critical_change", description=c.value, severity=Severity.CRITICAL,
                         type=StatementType.INFERENCE) for c in r.critical_changes]
    return AgentResponse(
        agent="architecture_agent", project_id=pid, story_id=r.story_id, status=AgentStatus(r.status.value),
        confidence=r.confidence, requires_hitl=r.requires_hitl or bool(r.critical_changes), summary=r.summary,
        findings=findings, recommendations=r.recommendations, risks=r.risks,
        sources=_sources(r.recommendations, r.risks, findings), next_agent="review_aggregator_agent",
        metadata={"critical_changes": [c.value for c in r.critical_changes], "hitl_reason": r.hitl_reason},
    )


def qa_response(pid: str, q: QATestOutput) -> AgentResponse:
    findings = [Finding(category="untestable_criterion", description=s.statement, severity=Severity.HIGH,
                        type=StatementType.UNKNOWN, sources=[s.source] if s.source else []) for s in q.untestable_criteria]
    findings += [Finding(category="coverage_gap", description=g, severity=Severity.MEDIUM,
                         type=StatementType.INFERENCE) for g in q.coverage_gaps]
    return AgentResponse(
        agent="qa_agent", project_id=pid, story_id=q.story_id, status=AgentStatus(q.status.value),
        confidence=q.confidence, summary=q.summary, findings=findings,
        sources=_sources(q.untestable_criteria), next_agent="review_aggregator_agent",
        metadata={"test_cases": len(q.test_cases), "coverage_gaps": q.coverage_gaps},
    )


def aggregate(
    project_id: str, story: dict, analysis: dict | None, arch: dict | None, qa: dict | None
) -> AggregatedReview:
    responses: list[AgentResponse] = []
    missing = []
    a = StoryAnalysisOutput.model_validate(analysis) if analysis else None
    r = ArchitectureReviewOutput.model_validate(arch) if arch else None
    q = QATestOutput.model_validate(qa) if qa else None
    if a:
        responses.append(analyst_response(project_id, a))
    else:
        missing.append("story_analyst_agent")
    if r:
        responses.append(architecture_response(project_id, r))
    else:
        missing.append("architecture_agent")
    if q:
        responses.append(qa_response(project_id, q))
    else:
        missing.append("qa_agent")

    conflicts: list[Finding] = []
    approving = [x.agent for x in responses if x.status in APPROVING]
    critical = [x.agent for x in responses if x.status == AgentStatus.CRITICAL_RISK]
    if approving and critical:
        conflicts.append(Finding(
            category="agent_conflict", severity=Severity.CRITICAL, type=StatementType.INFERENCE,
            description=f"{', '.join(approving)} aprueba(n) mientras {', '.join(critical)} marca(n) CRITICAL_RISK."))
    if r and r.status in (ReviewStatus.APPROVED,) and r.critical_changes:
        conflicts.append(Finding(
            category="agent_conflict", severity=Severity.HIGH, type=StatementType.INFERENCE,
            description="architecture_agent aprueba sin observaciones pero declara cambios críticos: "
                        + ", ".join(c.value for c in r.critical_changes)))
    if a and q and a.invest_score.clamp().testable >= 4 and q.untestable_criteria:
        conflicts.append(Finding(
            category="agent_conflict", severity=Severity.MEDIUM, type=StatementType.INFERENCE,
            description=f"story_analyst_agent califica la historia como testeable ({a.invest_score.testable}/5) "
                        f"pero qa_agent encontró {len(q.untestable_criteria)} criterio(s) no verificable(s)."))

    overall = max((x.status for x in responses), key=ORDER.index, default=AgentStatus.ERROR)
    if missing:
        overall = AgentStatus.ERROR
    agent_requests = [f"{x.agent}: {x.metadata.get('hitl_reason') or 'solicita revisión humana'}"
                      for x in responses if x.requires_hitl]
    return AggregatedReview(
        project_id=project_id,
        story_id=story.get("story_id", ""),
        responses=responses,
        conflicts=conflicts,
        overall_status=overall,
        confidence=min((x.confidence for x in responses), default=0.0),
        critical_changes=[c.value for c in (r.critical_changes if r else [])],
        blocking_questions=[x for x in (a.missing_information if a else []) if x.blocking],
        criteria_count=len(story.get("acceptance_criteria") or []),
        untestable_count=len(q.untestable_criteria) if q else 0,
        suggested_changes_count=len(a.suggested_changes) if a else 0,
        agent_hitl_requests=agent_requests,
        sources=sorted({s for x in responses for s in x.sources}),
        missing_agents=missing,
    )


class ReviewAggregatorAgent(BaseAgent):
    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        st = ctx.session.state
        agg = aggregate(st.get(K_PROJECT_ID, ""), st.get(K_STORY) or {}, st.get(K_ANALYSIS), st.get(K_ARCH), st.get(K_QA))
        texto = (f"Revisión agregada de {agg.story_id}: estado {agg.overall_status.value}, "
                 f"confianza {agg.confidence:.2f}, {len(agg.conflicts)} conflicto(s).")
        yield Event(
            author=self.name, invocation_id=ctx.invocation_id, branch=ctx.branch,
            content=types.Content(role="model", parts=[types.Part.from_text(text=texto)]),
            actions=EventActions(state_delta={K_AGG: agg.model_dump(mode="json")}),
        )


def build_review_aggregator() -> ReviewAggregatorAgent:
    return ReviewAggregatorAgent(name="review_aggregator_agent", description=DESCRIPTION)
