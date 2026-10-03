"""App de ADK Web: agente que busca, lee y responde sobre los documentos del bucket.

El código vive en src/adk_ing/agente.py para que el orquestador multiagente también
pueda usarlo como herramienta.
"""

from adk_ing.agente import build_bucket_agent

root_agent = build_bucket_agent()
