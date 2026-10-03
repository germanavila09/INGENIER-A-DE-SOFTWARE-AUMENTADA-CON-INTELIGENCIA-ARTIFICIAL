"""PROJECT_DISCOVERY_AGENT — agente determinista (no usa LLM).

Rol: descubrir y clasificar los proyectos del bucket de entrada.
- Lista las carpetas de primer nivel bajo la base (p. ej. gs://adk_ing/projects/).
- Usa project.yaml cuando existe; si no, deriva el project_id de la carpeta y lo
  registra como open_question.
- Clasifica cada archivo por carpeta (requirements/, user_stories/, architecture/,
  technical/, decisions/, tests/…) y, si no hay carpeta, por palabras del nombre.
- Ignora generated/ (salidas del propio sistema) para no realimentarse.
- Compara firmas (generación del objeto) con el manifest anterior para detectar
  archivos nuevos, modificados y eliminados.
- Produce el ProjectManifest. Nunca mezcla documentos de distintos project_id:
  cada manifest solo contiene archivos bajo la raíz de su proyecto.
"""

DESCRIPTION = "Descubre proyectos en el bucket, clasifica sus documentos y genera el ProjectManifest (determinista)."
