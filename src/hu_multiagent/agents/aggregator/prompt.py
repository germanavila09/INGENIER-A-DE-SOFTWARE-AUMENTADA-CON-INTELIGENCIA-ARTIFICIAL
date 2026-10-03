"""REVIEW_AGGREGATOR_AGENT — agente determinista (no usa LLM).

Rol: combinar las evaluaciones de los agentes especialistas de una historia en el
contrato común (AgentResponse) y detectar conflictos entre ellos. Ejemplos de conflicto:
- un agente aprueba (APPROVED) y otro marca CRITICAL_RISK;
- architecture_agent aprueba pero declara cambios críticos;
- el analista califica la historia como testeable y QA encuentra criterios no verificables.

La confianza agregada es el mínimo de las confianzas (criterio conservador).
Por ser una regla de negocio auditable, se implementa en código y no con un LLM.
"""

DESCRIPTION = "Combina las revisiones de los especialistas y detecta conflictos entre agentes (determinista)."
