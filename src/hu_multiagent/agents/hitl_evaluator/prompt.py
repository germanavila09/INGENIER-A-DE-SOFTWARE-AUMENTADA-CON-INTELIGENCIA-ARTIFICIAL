"""HITL_EVALUATOR — agente determinista (no usa LLM).

Rol: NO analiza historias. Decide AUTO_CONTINUE, CONTINUE_WITH_REVIEW o
STOP_FOR_HUMAN a partir de la revisión agregada, usando severidad, confianza,
tipo de cambio, conflictos, riesgo, impacto y reversibilidad (ver policy.py).

Niveles:
- 0 AUTOMATIC: continúa solo.
- 1 REVIEW: continúa y queda marcado para revisión posterior.
- 2 APPROVAL_REQUIRED: el flujo de esa historia se detiene en WAITING_FOR_HUMAN.

Umbrales: confianza ≥ 0.85 automático; 0.60–0.85 revisión; < 0.60 aprobación.
Los cambios críticos (seguridad, arquitectura, migración, eliminación, API pública,
irreversibles, costos, impacto en otros proyectos) exigen aprobación con cualquier confianza.
"""

DESCRIPTION = "Aplica la política HITL y decide si la historia continúa, se revisa después o espera a un humano (determinista)."
