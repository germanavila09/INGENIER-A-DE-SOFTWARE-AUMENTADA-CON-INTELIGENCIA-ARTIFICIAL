# Ingeniería de Software Aumentada con Inteligencia Artificial

Proyecto base que consume el bucket de Cloud Storage **`gs://adk_ing`** (proyecto GCP `servi-modelos-ia-dev`) con [Google ADK](https://google.github.io/adk-docs/) y Gemini:

- **`orquestador_hu`**: sistema **multiagente** que descubre proyectos en el bucket, analiza sus historias de usuario (INVEST, ambigüedades, impacto de arquitectura, casos de prueba), detecta cuándo se necesita una decisión humana (HITL), se detiene y continúa tras la aprobación, con máquina de estados persistida, versionado y auditoría completa. Arquitectura en [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).
- **`agente_bucket`**: agente que busca, lee y responde preguntas sobre los documentos del bucket (PDF, Word, Excel, PowerPoint, CSV, texto), citando documento y página.
- **`adk-ing`**: CLI y librería Python para listar, leer y sincronizar los objetos del bucket.

```
.
├── adk_web.py              # lanza ADK Web (python adk_web.py)
├── agents/                 # apps que muestra ADK Web
│   ├── orquestador_hu/     # sistema multiagente de historias de usuario (root_agent = orquestador)
│   └── agente_bucket/      # agente de preguntas sobre documentos
├── docs/ARQUITECTURA.md    # arquitectura multiagente, diagramas Mermaid, HITL, estados
├── ejemplos/projects/      # proyectos de ejemplo: PRJ001 (dengue), PRJ002, proyecto sin project.yaml
├── docs_ejemplo/           # documentos ficticios para agente_bucket
├── src/hu_multiagent/      # código del sistema multiagente
│   ├── agents/             # orchestrator, project_discovery, ingestion, story_analyst,
│   │                       # architecture, qa, aggregator, hitl_evaluator (agent.py + prompt.py)
│   ├── models/             # schemas Pydantic: proyecto, historia, contrato, HITL, estados
│   ├── services/           # almacenamiento por proyecto, estado persistido, auditoría
│   ├── tools/              # bucket, manifest/contexto, historias, herramientas HITL
│   └── workflows/          # flujos ADK por proyecto e historia, motor, reportes
├── src/adk_ing/
│   ├── config.py           # variables de entorno / .env
│   ├── bucket.py           # BucketReader (bucket) y CarpetaLocal (pruebas)
│   ├── documentos.py       # extracción de texto por páginas/hojas/diapositivas
│   ├── indice.py           # índice de búsqueda BM25 que detecta documentos nuevos
│   └── cli.py              # adk-ing ls | cat | sync
├── tests/                  # pruebas sin red
├── .env.example
└── pyproject.toml
```

## Requisitos

- Python 3.10+
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
- Una identidad con **`roles/storage.objectViewer`** sobre `gs://adk_ing` (el permiso mínimo es `storage.objects.list` + `storage.objects.get`) y, para el agente, **`roles/aiplatform.user`** en el proyecto.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env               # Windows: copy .env.example .env
gcloud auth application-default login
gcloud auth application-default set-quota-project servi-modelos-ia-dev
```

## Uso

### CLI

```bash
adk-ing ls                          # todo el bucket
adk-ing ls --prefix documentos/     # una carpeta
adk-ing cat documentos/guia.md      # imprime un archivo de texto
adk-ing sync                        # descarga lo nuevo/modificado a data/raw/
```

### Desde Python

```python
from adk_ing import BucketReader

reader = BucketReader()                      # usa GCS_BUCKET / GOOGLE_CLOUD_PROJECT del .env
for obj in reader.list(prefix="datos/"):
    print(obj.name, obj.size)

texto = reader.read_text("documentos/guia.md")
```

### Sistema multiagente de historias de usuario (`orquestador_hu`)

**1. Proyectos en el bucket.** El orquestador recorre `HU_INPUT_URI` (por defecto todo `gs://adk_ing/`) y reconoce tres formas de organizar los proyectos:

- `projects/<carpeta>/`: la recomendada, con `project.yaml` y carpetas como `requirements/`, `user_stories/`, `architecture/` o `decisions/`.
- `<carpeta>/` en la raíz: cada carpeta es un proyecto.
- Archivos sueltos agrupados por el prefijo del nombre: «SERVI _ SINCHI __ Sesión técnica … .docx» → proyecto **SERVI_SINCHI**.

Si un proyecto no tiene historias escritas (solo actas, notas de reunión o propuestas), **`story_generator_agent` genera el backlog** con evidencia citada y luego lo evalúa. Para cargar los ejemplos:

```bash
gcloud storage cp -r ejemplos/projects gs://adk_ing/
```

Para probar sin el bucket, pon `HU_INPUT_URI=ejemplos/projects` en el `.env`.

**2. ADK Web.** `python adk_web.py`, elige **orquestador_hu** y conversa:

| Tú escribes | Qué pasa |
|---|---|
| «¿Qué proyectos hay?» | `descubrir_proyectos`: lista proyectos (carpetas o archivos sueltos agrupados), estado y decisiones pendientes |
| «¿Qué documentos hay de SINCHI en el storage?» | el orquestador le pregunta al agente del bucket (`agente_documentos`) y responde con citas |
| «Evaluemos el proyecto SERVI _ SINCHI» | sin historias escritas: genera el backlog desde las notas (*IA*, con evidencia) y lo evalúa; las historias quedan en revisión |
| «Analiza PRJ001» | flujo completo: manifest → ingesta → contexto → por historia: analista → [arquitectura ‖ QA] → agregador → evaluador HITL |
| «MODIFIED D-PRJ001-US-001-01: la incidencia es casos / población × 100.000 y la población viene de proyecciones DANE» | registra tu corrección y reanaliza la historia automáticamente |
| «APPROVED D-PRJ001-US-003-01, migrar por lotes» | aprueba; se escribe la versión `_human_approved` |
| «¿Por qué US-001 terminó así?» | `explicar_historia`: transiciones, revisiones por agente, HITL, decisiones y auditoría |

Con los ejemplos, PRJ001 se detiene en tres historias: **US-001** por preguntas bloqueantes (fórmula de incidencia, fuente poblacional, definición de caso), **US-003** por migración de datos y retiro de un componente, y **US-004** por proponer cambios a una historia ya aprobada. **US-002** queda lista sin intervención. Las decisiones solo se registran si las escribes tú: el modelo no puede aprobar por su cuenta.

**3. Resultados.** En `HU_RESULTS_URI` (por defecto la carpeta `salidas_hu/`, ignorada por git), dentro de `projects/<ID>/`: `generated/` (manifest, contexto, artefactos por historia y sus versiones, reportes), `state/project_state.json` y `audit/audit_log.jsonl`. Para escribirlos en GCP usa un bucket aparte, p. ej. `HU_RESULTS_URI=gs://adk_ing_resultados`.

**Terminal:** `adk run agents/orquestador_hu`. Arranca ADK Web con `python adk_web.py` (o `adk web agents`). Si ejecutas `adk web` en la raíz del repo, también funciona, pero el selector muestra `agents.orquestador_hu` y la carpeta `src`.

**Cuenta de servicio con mínimo privilegio** (para Cloud Run o para probar localmente con impersonación):

```bash
PROJECT=servi-modelos-ia-dev
SA=hu-agentes@$PROJECT.iam.gserviceaccount.com
gcloud iam service-accounts create hu-agentes --project $PROJECT
gcloud storage buckets add-iam-policy-binding gs://adk_ing --member=serviceAccount:$SA --role=roles/storage.objectViewer
gcloud storage buckets create gs://adk_ing_resultados --project $PROJECT --location=us-central1 --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding gs://adk_ing_resultados --member=serviceAccount:$SA --role=roles/storage.objectUser
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/aiplatform.user
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/logging.logWriter
# Probar localmente con esos permisos (requiere roles/iam.serviceAccountTokenCreator sobre la cuenta):
gcloud auth application-default login --impersonate-service-account=$SA
```

### ADK Web con el agente del bucket

```bash
python adk_web.py                   # o botón Run en VS Code sobre adk_web.py
```

Revisa credenciales y acceso a los documentos, levanta la interfaz en http://127.0.0.1:8000 y abre el navegador. Elige **agente_bucket** en el selector.

Opciones: `--port 8080`, `--no-browser`, `--skip-checks`. También funciona el comando estándar de ADK desde la raíz: `adk web agents` (o `adk run agents/agente_bucket` en la terminal).

### Qué puede hacer el agente con los documentos

| Herramienta | Para qué |
|---|---|
| `buscar_en_documentos` | Busca en el contenido de todos los documentos y devuelve los fragmentos más relevantes con documento y página/hoja/diapositiva. Puede limitarse a un documento. |
| `leer_documento` | Lee un documento por secciones (página, hoja, diapositiva o parte) y pagina los largos. |
| `listar_documentos` | Lista lo que hay en el bucket (o en un prefijo). |
| `estado_del_indice` | Muestra qué está indexado y qué documentos no tienen texto o fallaron. |
| `consultar_documento_con_gemini` | Envía el archivo a Gemini para leerlo visualmente: PDF escaneados, imágenes, tablas o gráficos. |

Formatos con extracción de texto: `.pdf .docx .pptx .xlsx .xlsm .csv .tsv .txt .md .json .html .xml .yaml .py .sql`. PDF escaneados e imágenes (`.png .jpg .webp`) se leen con `consultar_documento_con_gemini`. Los `.doc`, `.ppt` y `.xls` antiguos no se leen: guárdalos como `.docx`, `.pptx` o `.xlsx`.

**Documentos que se ingestan después:** el índice vuelve a revisar el bucket cada 30 s (`INDICE_TTL_SEG`) cuando el agente busca; los documentos nuevos o reemplazados se indexan solos y los borrados desaparecen, sin reiniciar ADK Web. El índice vive en memoria: al reiniciar se reconstruye en la primera búsqueda.

Ejemplos de preguntas: *«¿qué cobertura de pruebas exige la política de calidad?»*, *«¿quién es responsable de desplegar en Cloud Run y para cuándo?»*, *«resume la presentación de arquitectura»*, *«¿qué versión dice la constancia escaneada?»*.

### Probar sin tocar el bucket

En el `.env` pon `DOCS_LOCAL_DIR=docs_ejemplo` y arranca `python adk_web.py`: el agente trabaja con los documentos de ejemplo de esa carpeta (o con cualquier otra carpeta tuya). Déjalo vacío para volver al bucket.

### Ingestar documentos al bucket

Desde la consola de Cloud Storage (botón **Subir**) o con:

```bash
gcloud storage cp docs_ejemplo/* gs://adk_ing/documentos/
```

Subir requiere `roles/storage.objectCreator`; el agente solo necesita lectura.

> **Repo dentro de Google Drive:** crea el entorno virtual fuera de la carpeta sincronizada (o usa el Python global) y pon `ADK_SESSION_URI=memory://` en el `.env`, para que Drive no intente sincronizar miles de archivos ni la base SQLite de sesiones.

## Pruebas

```bash
pytest -q
```

Las pruebas no requieren credenciales: usan los documentos de `docs_ejemplo/` y `ejemplos/projects/`, un cliente de GCS falso y modelos simulados que recorren los flujos reales de ADK. Para el orquestador cubren la política HITL, la máquina de estados, el flujo completo de PRJ001 (pausa, APPROVED, REJECTED, MODIFIED con reanálisis), el versionado, la auditoría, el aislamiento entre proyectos, los reintentos, los errores fatales, los límites y la guardia que impide decisiones no humanas. También corren en GitHub Actions en cada push.

## Variables de entorno

| Variable | Por defecto | Uso |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | `servi-modelos-ia-dev` | Proyecto GCP |
| `GCS_BUCKET` | `adk_ing` | Bucket a consumir |
| `GCS_PREFIX` | *(vacío)* | Limita todo a una carpeta del bucket |
| `LOCAL_DATA_DIR` | `data/raw` | Destino de `adk-ing sync` (ignorado por git) |
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` | Usa Gemini vía Vertex AI |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Región de Vertex AI |
| `ADK_MODEL` | `gemini-2.5-flash` | Modelo del agente |
| `ADK_PORT` | `8000` | Puerto de `adk_web.py` |
| `ADK_ARTIFACT_URI` | *(vacío = local)* | `gs://adk_ing` guarda en el bucket los archivos de las sesiones (requiere escritura) |
| `ADK_SESSION_URI` | *(vacío = SQLite local)* | `memory://` para sesiones en memoria |
| `DOCS_LOCAL_DIR` | *(vacío = bucket)* | Carpeta local con documentos, para pruebas |
| `INDICE_TTL_SEG` | `30` | Cada cuántos segundos se buscan documentos nuevos en el bucket |
| `DOCS_MAX_MB` | `50` | Tamaño máximo de un documento para indexarlo |
| `HU_INPUT_URI` | `gs://adk_ing/` | Dónde busca proyectos el orquestador (o carpeta local) |
| `HU_MAX_HISTORIAS_GENERADAS` | `12` | Máximo de historias que propone el generador por proyecto |
| `HU_GENERADAS_REQUIEREN_APROBACION` | `false` | `true` = cada historia generada por IA exige aprobación (nivel 2) |
| `HU_CONTEXTO_GENERACION_MAX_CARACTERES` | `150000` | Texto de los documentos que recibe el generador |
| `HU_RESULTS_URI` | `salidas_hu` | Resultados, estado y auditoría (carpeta local o `gs://bucket-resultados`) |
| `HU_STATE_URI` | *(= resultados)* | Destino aparte para el estado, si se quiere |
| `HU_MODEL` | `ADK_MODEL` | Modelo de los agentes del orquestador |
| `HU_CONFIDENCE_AUTO` / `HU_CONFIDENCE_REVIEW` | `0.85` / `0.60` | Umbrales HITL de confianza |
| `HU_MAX_HISTORIAS_POR_EJECUCION` | `10` | Historias analizadas por ejecución |
| `HU_MAX_REANALISIS` | `2` | Reanálisis permitidos tras MODIFIED |
| `HU_MAX_REINTENTOS` | `2` | Reintentos ante errores transitorios |
| `HU_MAX_LLAMADAS_LLM_POR_HISTORIA` | `12` | Límite de llamadas al modelo por historia |
