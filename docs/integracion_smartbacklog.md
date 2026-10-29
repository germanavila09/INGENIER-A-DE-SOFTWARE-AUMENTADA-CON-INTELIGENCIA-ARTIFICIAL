# Integración con SmartBacklogIA

Los dos sistemas comparten el bucket `gs://adk_ing`, Gemini en Vertex AI (`servi-modelos-ia-dev`) y el
método de estimación por factores. Cada uno hace lo que mejor hace y se conectan por la API de
servicio de SmartBacklogIA.

| Etapa | Dónde | Qué pasa |
|---|---|---|
| 1. Documentos | aquí | Actas, notas, requisitos, BPMN y audios del bucket (los audios se transcriben). |
| 2. Análisis multiagente | aquí | Generador, analista, arquitectura, QA y estimación; agregador y evaluador HITL. |
| 3. Revisión humana | aquí | Aprobar, pedir cambios, descartar o pedir información por historia (niveles 0/1/2). |
| 4. Estimación | aquí | Factores 1–5 → puntos Fibonacci, rango y spike (`docs/metodo_estimacion.md`). |
| 5. Priorización | aquí | Orden recomendado según dependencias. |
| 6. Publicación | puente | Las aprobadas van a SmartBacklogIA con `POST /api/v1/ingest/projects`. |
| 7–11. Etapas SBIA | SmartBacklogIA | Generación, clasificación, estimación (puntos de función), priorización y backlog, aprobadas por personas con rol. |

## Qué viaja y cómo

Cada historia aprobada se envía como `StoryIn`:

- `external_id = hu_multiagent:<proyecto>:<historia>`: hace la carga idempotente.
- Título, rol, necesidad y beneficio (de la versión mejorada), y criterios como checklist `- [ ]`.
- `type` (HU_IA o HU_TRADICIONAL) y su justificación.
- `priority_rank` desde el orden recomendado, con `priority_method = hu_multiagent:orden_recomendado`.
- `source_ref`: documentos de origen, estimación por factores (puntos, rango, método, spike, quién la
  hizo), prioridad, calidad, nivel HITL y si la aprobó una persona. Así las dos estimaciones (factores aquí,
  puntos de función allá) quedan lado a lado para compararlas.

## Reglas

- Por defecto solo se publican historias **aprobadas por una persona**. La opción «incluir las que aún no
  apruebas» agrega las listas o por revisar, marcadas como «sin aprobación humana»; las que esperan una
  decisión nunca se envían.
- Una historia ya publicada no se reenvía salvo con «actualizar», y aun así se omite si en SmartBacklogIA
  salió de «borrador» o alguien la editó después de la última publicación (se compara su `version`).
- Las etapas de SmartBacklogIA se **leen** desde aquí, pero solo se aprueban allá: su API rechaza
  aprobaciones hechas por un servicio.
- Cada publicación queda en `projects/<id>/integrations/smartbacklog.json` (código y versión remota de
  cada historia) y en la auditoría como evento `smartbacklog_publish`, con quién la hizo.

## Configuración

En el `.env` de este repo:

```
SBIA_URL=http://localhost:8000
SBIA_API_KEY=<la API key creada en SmartBacklogIA → Administración>
SBIA_CLAVES=SERVI_SINCHI=SINCHI
```

## Uso

- Interfaz: `python adk_web.py` abre **Ruta de historias** en `/flujo`, con las seis etapas de aquí y las
  cinco de SmartBacklogIA. El SPB anterior sigue en `/spb`.
- Consola:
  ```
  python -m hu_multiagent.integrations.smartbacklog estado
  python -m hu_multiagent.integrations.smartbacklog publicar SERVI_SINCHI --simular
  python -m hu_multiagent.integrations.smartbacklog publicar SERVI_SINCHI
  python -m hu_multiagent.integrations.smartbacklog remoto SERVI_SINCHI
  ```
- API: `GET /api/integracion/smartbacklog`, `GET /api/projects/{id}/smartbacklog`,
  `POST /api/projects/{id}/smartbacklog/publish` (`{"simular", "actualizar", "incluir_sin_aprobar", "clave"}`).

## Pruebas

`tests/test_smartbacklog.py` usa un SmartBacklogIA simulado (sin red) que reproduce el ingest idempotente,
las versiones y las etapas. Además se verificó el contrato contra una instancia real de SmartBacklogIA
(rama `gcp-vertex`, SQLite, proveedor stub): proyecto creado, historias en «borrador», reenvío como
actualización y aprobación como servicio rechazada.
