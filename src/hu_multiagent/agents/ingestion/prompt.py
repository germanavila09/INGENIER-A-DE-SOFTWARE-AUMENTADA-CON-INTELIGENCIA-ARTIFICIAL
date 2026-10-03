"""DOCUMENT_INGESTION_AGENT — agente determinista (no usa LLM).

Rol: leer y normalizar los documentos del manifest y construir el contexto.
- Formatos: JSON, YAML, TXT, Markdown, CSV, XLSX, PDF, DOCX (y PPTX, HTML).
- Extrae: contenido, metadatos, identificadores que el documento define
  (US-001, RF-02, ADR-001…), referencias a otros IDs, versión, fechas y autores.
- Descubre las historias de usuario (JSON/YAML, CSV/XLSX, Markdown con '## US-001').
- Construye el ProjectContext solo con afirmaciones FACT y su fuente. Lo que falta
  (sin project.yaml, sin arquitectura, historias sin criterios, dependencias o
  referencias inexistentes) se registra como open_question; nunca se inventa.
- En el MVP el contexto es determinista; en la fase 2 un PROJECT_CONTEXT_AGENT con
  LLM puede resumirlo, conservando las fuentes.
"""

DESCRIPTION = "Lee y normaliza los documentos del proyecto, descubre historias y construye el ProjectContext (determinista)."
