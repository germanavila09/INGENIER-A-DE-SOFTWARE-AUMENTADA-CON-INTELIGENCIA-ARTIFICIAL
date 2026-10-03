"""Política HITL: AUTOMATE WHEN SAFE · REVIEW WHEN UNCERTAIN · STOP WHEN CRITICAL.

Función pura y auditable. La confianza NO es el único criterio: los cambios
críticos detienen el flujo aunque la confianza sea alta.
"""

from __future__ import annotations

from ...models.agent_response import AggregatedReview
from ...models.common import Severity
from ...models.hitl import HitlAction, HitlEvaluation, HitlLevel, HitlReason

# Cambio declarado por architecture_agent → (código HITL, descripción)
CRITICAL_CHANGE_CODES = {
    "SECURITY_MODEL_CHANGE": ("SECURITY_CHANGE", "Cambio en el modelo de seguridad"),
    "ARCHITECTURE_CHANGE": ("ARCHITECTURE_CHANGE", "Cambio de arquitectura"),
    "DATA_MIGRATION": ("MIGRATION", "Migración de datos"),
    "COMPONENT_REMOVAL": ("DELETION", "Eliminación de componentes"),
    "DATA_DELETION": ("DELETION", "Eliminación de información"),
    "PUBLIC_API_CHANGE": ("PUBLIC_API_CHANGE", "Cambio de API pública"),
    "IRREVERSIBLE_CHANGE": ("IRREVERSIBLE_CHANGE", "Cambio irreversible"),
    "SIGNIFICANT_COST_INCREASE": ("COST_INCREASE", "Aumento considerable de costos"),
    "CROSS_PROJECT_IMPACT": ("CROSS_PROJECT_IMPACT", "Decisión que puede afectar otros proyectos"),
}
IRREVERSIBLE = {"DATA_MIGRATION", "DATA_DELETION", "COMPONENT_REMOVAL", "IRREVERSIBLE_CHANGE"}


def evaluate_hitl(
    agg: AggregatedReview,
    *,
    story_approved: bool = False,
    confidence_auto: float = 0.85,
    confidence_review: float = 0.60,
) -> HitlEvaluation:
    stop: list[HitlReason] = []
    review: list[HitlReason] = []

    # 0. Salida incompleta de algún agente → no se puede decidir solo.
    for agent in agg.missing_agents:
        stop.append(HitlReason(code="MISSING_AGENT_OUTPUT", agent=agent, severity=Severity.HIGH,
                               description=f"{agent} no produjo una salida válida."))

    # 1. Cambios críticos (nivel 2 aunque la confianza sea alta).
    for change in agg.critical_changes:
        code, desc = CRITICAL_CHANGE_CODES.get(change, (change, change))
        stop.append(HitlReason(code=code, description=desc, agent="architecture_agent",
                               severity=Severity.CRITICAL, critical=True))

    # 2. Conflictos entre agentes.
    for c in agg.conflicts:
        target = stop if c.severity in (Severity.HIGH, Severity.CRITICAL) else review
        target.append(HitlReason(code="AGENT_CONFLICT", description=c.description, agent="review_aggregator_agent",
                                 severity=c.severity, critical=c.severity == Severity.CRITICAL))

    # 3. Riesgo crítico declarado por un agente.
    for r in agg.responses:
        if r.status.value == "CRITICAL_RISK":
            stop.append(HitlReason(code="CRITICAL_RISK", description=r.summary or "Riesgo crítico", agent=r.agent,
                                   severity=Severity.CRITICAL, critical=True))

    # 4. Información funcional crítica ambigua (preguntas bloqueantes).
    if agg.blocking_questions:
        stop.append(HitlReason(
            code="CRITICAL_AMBIGUITY", agent="story_analyst_agent", severity=Severity.HIGH, critical=True,
            description=f"{len(agg.blocking_questions)} pregunta(s) bloqueante(s): "
                        + "; ".join(q.question for q in agg.blocking_questions[:5])))

    # 5. Modificación significativa de una historia ya aprobada por humanos.
    if story_approved and agg.suggested_changes_count:
        stop.append(HitlReason(code="APPROVED_STORY_MODIFICATION", agent="story_analyst_agent",
                               severity=Severity.HIGH, critical=True,
                               description=f"La historia estaba aprobada y se proponen {agg.suggested_changes_count} cambio(s)."))

    # 6. Criterios no verificables: mayoría o sin criterios → aprobación; algunos → revisión.
    if agg.criteria_count == 0:
        stop.append(HitlReason(code="NO_ACCEPTANCE_CRITERIA", agent="qa_agent", severity=Severity.HIGH,
                               description="La historia no tiene criterios de aceptación."))
    elif agg.untestable_count:
        mayoria = agg.untestable_count * 2 > agg.criteria_count
        (stop if mayoria else review).append(HitlReason(
            code="UNVERIFIABLE_CRITERIA", agent="qa_agent", severity=Severity.HIGH if mayoria else Severity.MEDIUM,
            description=f"{agg.untestable_count} de {agg.criteria_count} criterio(s) no son verificables."))

    # 7. Un agente pidió intervención humana explícitamente.
    for req in agg.agent_hitl_requests:
        agent = req.split(":", 1)[0]
        if not any(r.agent == agent for r in stop):
            stop.append(HitlReason(code="AGENT_REQUESTED", description=req, agent=agent, severity=Severity.HIGH))

    # 8. Confianza.
    if agg.confidence < confidence_review:
        stop.append(HitlReason(code="LOW_CONFIDENCE", agent="hitl_evaluator_agent", severity=Severity.HIGH,
                               description=f"Confianza {agg.confidence:.2f} < {confidence_review}"))
    elif agg.confidence < confidence_auto:
        review.append(HitlReason(code="MEDIUM_CONFIDENCE", agent="hitl_evaluator_agent", severity=Severity.MEDIUM,
                                 description=f"Confianza {agg.confidence:.2f} < {confidence_auto}"))

    # 9. Mejoras de redacción propuestas → revisión posterior.
    if agg.suggested_changes_count and not story_approved:
        review.append(HitlReason(code="WORDING_IMPROVEMENT", agent="story_analyst_agent", severity=Severity.LOW,
                                 description=f"{agg.suggested_changes_count} mejora(s) de redacción propuesta(s)."))

    reversible = not any(c in IRREVERSIBLE for c in agg.critical_changes)
    if stop:
        level, action, reasons = HitlLevel.APPROVAL_REQUIRED, HitlAction.STOP_FOR_HUMAN, stop + review
    elif review:
        level, action, reasons = HitlLevel.REVIEW, HitlAction.CONTINUE_WITH_REVIEW, review
    else:
        level, action, reasons = HitlLevel.AUTOMATIC, HitlAction.AUTO_CONTINUE, []
    return HitlEvaluation(story_id=agg.story_id, action=action, level=level, confidence=agg.confidence,
                          reasons=reasons, reversible=reversible)
