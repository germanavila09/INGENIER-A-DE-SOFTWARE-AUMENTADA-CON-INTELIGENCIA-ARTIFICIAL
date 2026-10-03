"""App de ADK Web: orquestador multiagente de historias de usuario.

El código vive en src/hu_multiagent; aquí solo se expone root_agent para que ADK Web
muestre una única app (y no una por cada agente especialista).
"""

from hu_multiagent.agents.orchestrator.agent import root_agent  # noqa: F401
