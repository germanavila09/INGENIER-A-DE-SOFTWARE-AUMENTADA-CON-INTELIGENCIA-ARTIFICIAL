# Ingeniería de Software Aumentada con Inteligencia Artificial

Agentes de IA para ingeniería de software: generan historias de usuario (HU) desde documentos, las revisan con
varios agentes, las estiman y las llevan a un backlog **con revisión humana (HITL) en cada paso**.

La aplicación es **SmartBacklogIA**, en [`smartbacklog/`](smartbacklog/): corre en tu computador con Docker,
guarda todo en Postgres y lee los documentos de una carpeta. Sus agentes están hechos con
[Google ADK](https://google.github.io/adk-docs/) y usan Gemini con una API key de
[AI Studio](https://aistudio.google.com/apikey). **No necesita Google Cloud** (ni bucket, ni Vertex AI, ni permisos de IAM).

```powershell
cd smartbacklog\windows
.\INICIAR.cmd
```

Guía completa en [SmartBacklogIA](#smartbacklogia-smartbacklog) y, para probadores, en
[smartbacklog/windows/LEEME.md](smartbacklog/windows/LEEME.md).

> La raíz del repositorio conserva la **versión anterior**: un motor multiagente con ADK que leía el bucket
> `gs://adk_ing` con Gemini en Vertex AI, con sus interfaces `/flujo` y `/spb`. Ya no es la forma de uso; queda
> como referencia y como origen de la política HITL y del método de estimación que hoy usa SmartBacklogIA. Ver
> [Versión anterior](#versión-anterior-motor-adk-con-bucket-y-vertex-ai).

```
.
├── smartbacklog/           # SmartBacklogIA: la aplicación
│   ├── app/                # FastAPI + plantillas; services/hitl.py (política HITL), estimation.py, fpa.py
│   ├── app/agents/         # agentes ADK: generación, revisión (analista, arquitectura, QA, estimación), FPA
│   ├── windows/            # INICIAR.cmd, CONFIGURAR_LOCAL.cmd, DETENER.cmd y LEEME.md (guía para probadores)
│   ├── tests/              # 180 pruebas (SQLite y agentes simulados)
│   └── docker-compose.yml  # Postgres + app en local
├── docs_ejemplo/           # documentos ficticios para la demo (acta del sprint 3, glosario, política de calidad)
├── docs/                   # metodo_estimacion.md, ARQUITECTURA.md, integracion_smartbacklog.md, diagramas
├── .github/workflows/      # CI: pruebas de smartbacklog/ y de la versión anterior
│
│   Versión anterior (bucket + Vertex AI), como referencia:
├── adk_web.py              # ADK Web + API (/api) + Ruta de historias (/flujo) + SPB (/spb)
├── web/                    # fronts /flujo y /spb
├── agents/                 # orquestador_hu (multiagente de HU) y agente_bucket (preguntas sobre documentos)
├── src/hu_multiagent/      # código del orquestador: agentes, modelos, servicios, flujos, API
├── src/adk_ing/            # lectura del bucket, extracción de texto, índice BM25, CLI adk-ing
├── ejemplos/projects/      # proyectos de ejemplo del orquestador (PRJ001 dengue, PRJ002…)
├── deploy/                 # despliegue en Cloud Run (desplegar_gcp.ps1, pausar_gcp.ps1)
├── tests/                  # pruebas del motor anterior
├── .env.example
└── pyproject.toml
```

## SmartBacklogIA (`smartbacklog/`)

[SmartBacklogIA](https://github.com/diegodgt02-ai/SmartBacklogIA)
vive en `smartbacklog/` como componente, con un solo login, roles por proyecto y su base de datos. Le agregamos la
revisión multiagente y la política HITL del motor de este repositorio, adaptadas a cada historia:

- **Revisión humana (HITL) como función central:** analista → arquitectura ∥ QA ∥ estimación revisan cada HU; una
  política en código le asigna nivel 0/1/2; el nivel 2 solo lo decide el product owner y los niveles 1 y 0 el
  product owner o el scrum master. Decisiones: aprobar (con o sin la propuesta), pedir cambios, falta
  información, descartar, comentar. Nada se aprueba solo.
- **Cinco etapas:** generación desde documentos, clasificación HU-IA / HU-Tradicional, estimación (puntos de
  historia por factores junto a puntos de función IFPUG), priorización y backlog final.
- **Local, sin GCP:** Postgres en Docker, documentos en una carpeta y Gemini con API key. (El modo con bucket y
  Vertex AI sigue disponible como opción, `INICIAR.cmd -Gcp`, pero no es necesario.)

Requisitos: Docker Desktop y una API key de [AI Studio](https://aistudio.google.com/apikey). En Windows:

```powershell
cd smartbacklog\windows
.\INICIAR.cmd
```

La primera vez pide la API key (no se muestra y queda en `smartbacklog\.env`, que git ignora), construye la app,
crea usuarios de prueba y abre http://localhost:8000. Allí: **Agentes IA** genera HU desde los documentos,
**Revisión humana** es la bandeja de decisiones y **Estimación** muestra los puntos de historia por factores junto
a los puntos de función. En Linux o macOS: crea `smartbacklog/.env` con la sección «Modo local» de
`smartbacklog/.env.example`, luego `cd smartbacklog && docker compose up --build -d` y, una vez,
`docker compose exec app python scripts/seed.py` (usuarios de prueba).

Guía para probadores, usuarios de prueba y demo con `docs_ejemplo`: [smartbacklog/windows/LEEME.md](smartbacklog/windows/LEEME.md).
Detalle técnico: [smartbacklog/README.md](smartbacklog/README.md).

## Versión anterior: motor ADK con bucket y Vertex AI

> Ya no es la forma de uso del proyecto. Se conserva como referencia: aquí nacieron la política HITL, la
> revisión multiagente y el método de estimación que SmartBacklogIA usa hoy. Lo que sigue solo aplica si
> quieres correr este motor sobre el bucket `gs://adk_ing` con Gemini en Vertex AI.

### Requisitos

- Python 3.10+
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
- Una identidad con **`roles/storage.objectViewer`** sobre `gs://adk_ing` (el permiso mínimo es `storage.objects.list` + `storage.objects.get`) y, para el agente, **`roles/aiplatform.user`** en el proyecto.

### Instalación

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env               # Windows: copy .env.example .env
gcloud auth application-default login
gcloud auth application-default set-quota-project servi-modelos-ia-dev
```

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
| «Analiza PRJ001» | flujo completo: manifest → ingesta → contexto → por historia: analista → [arquitectura ‖ QA ‖ estimación] → agregador → evaluador HITL |
| «MODIFIED D-PRJ001-US-001-01: la incidencia es casos / población × 100.000 y la población viene de proyecciones DANE» | registra tu corrección y reanaliza la historia automáticamente |
| «APPROVED D-PRJ001-US-003-01, migrar por lotes» | aprueba; se escribe la versión `_human_approved` |
| «¿Por qué US-001 terminó así?» | `explicar_historia`: transiciones, revisiones por agente, HITL, decisiones y auditoría |

**Estimación.** `estimation_agent` clasifica cada historia como HU-IA o HU-Tradicional y puntúa sus factores de 1 a 5; los puntos (Fibonacci), el rango y el spike los calcula el código (`services/estimation.py`), así el modelo no puede inventar un número fuera del método. Es informativa: no baja la confianza ni detiene el flujo; un spike requerido pide revisión (nivel 1). Una persona puede ajustar tipo y factores desde el SPB; su estimación prevalece y queda versionada y auditada. Método y referencias en [docs/metodo_estimacion.md](docs/metodo_estimacion.md) (adoptado de [SmartBacklogIA](https://github.com/diegodgt02-ai/SmartBacklogIA)).

**Concurrencia.** Cada proyecto tiene su candado: análisis, decisiones, aprobación de épicas y estimaciones de un proyecto no se cruzan, y proyectos distintos avanzan en paralelo. Si llega una decisión mientras corre un análisis, se rechaza con «ocupado» (409) en vez de perderse. Cada historia lleva una `version`; la interfaz la envía con la decisión y, si la historia cambió entretanto, recibe 409 y recarga. El estado guardado también lleva versión: si otro proceso (otro servidor sobre el mismo bucket) lo modificó, el guardado falla y el proyecto queda en `ERROR` con el motivo, en lugar de pisar cambios en silencio.

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

### Smart Product Backlog (interfaz web)

```bash
python adk_web.py                   # o botón Run en VS Code sobre adk_web.py
```

Abre http://127.0.0.1:8000/spb. El mismo servidor sigue ofreciendo ADK Web en `/dev-ui`.

| Paso | Qué hace | Con qué parte del backend |
|---|---|---|
| 1 · Insumos | Elegir o crear proyecto, subir PDF, DOCX, PPTX, XLSX, CSV, TXT/MD, modelos de proceso BPMN (se leen con `defusedxml`, sin riesgo de XXE) y audios MP3/WAV/M4A (hasta 20 MB se transcriben con Gemini y la transcripción se guarda junto al audio) | `POST /api/projects`, `POST /api/projects/{id}/files` |
| 2 · Comprensión | Avance en vivo: fuentes, documentos normalizados, historias (escritas o generadas por IA) y evaluación multiagente historia por historia; al terminar, tokens y costo estimado de la ejecución | `POST /api/projects/{id}/analyze` (en segundo plano), `GET …/status` |
| 3 · Refinamiento | Tarjetas por épica con propuesta mejorada, original, evidencia, calidad, nivel HITL, tipo HU-IA o HU-Tradicional (del agente de estimación), puntos con su rango y spike, y prioridad sugerida. Aprobar, Refinar (los agentes reanalizan con tu indicación), Descartar, Estimar, Aprobar épica (solo historias sin decisión crítica) y «¿Por qué?» | `GET …/backlog`, `POST …/stories/{sid}/decision`, `POST …/stories/{sid}/estimate`, `POST …/epics/approve`, `GET …/stories/{sid}/explain` |
| 4 · Validación | Decisiones críticas (nivel 2) con motivo, riesgo, recomendación y preguntas bloqueantes; calidad INVEST; casos de prueba Dado/Cuando/Entonces; estimación por historia con sus factores (ajustable); preguntas abiertas del proyecto | mismos endpoints |
| 5 · Backlog | Resumen, orden por dependencias, puntos por historia y en total, avance por épica y exportación CSV (`;`, abre en Excel en español; incluye puntos, rango, spike, método y quién estimó) o JSON | `GET …/export?formato=csv|json` |

El botón **Asistente** conversa con `orquestador_hu` usando la API estándar de ADK (`/apps/orquestador_hu/users/{u}/sessions` y `/run_sse`), y muestra cuándo consulta al agente del bucket. Quien decide se registra con el nombre del campo «Decide» (encabezado `X-Usuario`) en la auditoría y en las versiones aprobadas.

**Permisos:** subir documentos o crear proyectos desde la interfaz escribe en el bucket de entrada, así que la cuenta necesita `roles/storage.objectCreator` sobre `gs://adk_ing`. Sin ese permiso la interfaz muestra el error y se puede seguir subiendo con `gcloud storage cp`. Si abres `web/spb/index.html` directamente (sin servidor) funciona en **modo demo** con datos de ejemplo.

**Modo demostración del backend (sin Gemini):** con `HU_MODEL=simulado` y `HU_INPUT_URI=ejemplos/projects`, el servidor recorre el flujo completo con respuestas fijas (las mismas de las pruebas), sin credenciales ni costo. Sirve para mostrar el SPB o probar la interfaz; el chat responde un aviso fijo. Para trabajar con los proyectos reales vuelve a `HU_MODEL=gemini-2.5-flash` y `HU_INPUT_URI=gs://adk_ing/`.

### ADK Web con el agente del bucket

`python adk_web.py` revisa credenciales y acceso a los documentos, levanta el servidor en http://127.0.0.1:8000 y abre el navegador en **Ruta de historias** (`/flujo`); el Smart Product Backlog sigue en `/spb`. Para el chat de ADK entra a http://127.0.0.1:8000/dev-ui y elige **agente_bucket** u **orquestador_hu** en el selector.

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

### Integración con SmartBacklogIA por API

`python adk_web.py` abre **Ruta de historias** (`/flujo`): una sola interfaz para documentos, análisis
multiagente, revisión humana, estimación, priorización y publicación en
[SmartBacklogIA](https://github.com/diegodgt02-ai/SmartBacklogIA), cuyas cinco etapas se consultan desde
ahí y se aprueban allá. Las historias aprobadas se publican con su estimación, tipo y orden; lo editado en
SmartBacklogIA no se pisa. Configura `SBIA_URL`, `SBIA_API_KEY` y `SBIA_CLAVES` en `.env`.
Detalle en [docs/integracion_smartbacklog.md](docs/integracion_smartbacklog.md).

### Despliegue en Cloud Run

`deploy\desplegar_gcp.ps1` publica en Cloud Run (`servi-modelos-ia-dev`) esta versión anterior: Ruta de historias y
SmartBacklogIA (rama `gcp-vertex`, desde la carpeta que indica `-RutaSmartBacklog`), con usuario y clave por tester.
No incluye la revisión humana de `smartbacklog/`. Cloud SQL tiene costo fijo mientras existe:
`deploy\pausar_gcp.ps1 -Accion pausar` lo detiene. Detalle y costos en [deploy/README.md](deploy/README.md).
Para que otras personas prueben hoy, cada una corre `smartbacklog/` en su computador
([smartbacklog/windows/LEEME.md](smartbacklog/windows/LEEME.md)).

## Pruebas

```bash
cd smartbacklog && pip install -r requirements.txt && pytest -q        # SmartBacklogIA (180 pruebas)
pytest -q                                                              # versión anterior (desde la raíz)
```

Las de `smartbacklog/` usan SQLite y agentes simulados (sin credenciales ni red) y cubren la revisión multiagente,
los niveles y permisos HITL, las decisiones, la etapa 1, la estimación, la priorización y las cinco etapas.

Las de la versión anterior tampoco requieren credenciales (incluidas las de la API del Smart Product Backlog: proyectos, carga y transcripción de audio, análisis en segundo plano, refinamiento, aprobación por épica, decisiones críticas y exportación): usan los documentos de `docs_ejemplo/` y `ejemplos/projects/`, un cliente de GCS falso y modelos simulados que recorren los flujos reales de ADK. Para el orquestador cubren la política HITL, la máquina de estados, el método de estimación y su ajuste humano, la concurrencia (decisiones durante un análisis, aprobación de épicas, proyectos en paralelo, versión de historia y escrituras de otro proceso), el lector BPMN, el costo por ejecución, el modo demostración, el flujo completo de PRJ001 (pausa, APPROVED, REJECTED, MODIFIED con reanálisis), el versionado, la auditoría, el aislamiento entre proyectos, los reintentos, los errores fatales, los límites y la guardia que impide decisiones no humanas. Las dos suites corren en GitHub Actions en cada push (jobs `pytest` y `smartbacklog`).

## Variables de entorno

Las de SmartBacklogIA están documentadas en [smartbacklog/.env.example](smartbacklog/.env.example) y las escribe
`CONFIGURAR_LOCAL.cmd` (lo mínimo: `GOOGLE_API_KEY`). La tabla siguiente es de la **versión anterior** (`.env` en la raíz).

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
| `HU_MODEL` | `ADK_MODEL` | Modelo de los agentes del orquestador; `simulado` = modo demostración sin Gemini |
| `HU_CONFIDENCE_AUTO` / `HU_CONFIDENCE_REVIEW` | `0.85` / `0.60` | Umbrales HITL de confianza |
| `HU_MAX_HISTORIAS_POR_EJECUCION` | `10` | Historias analizadas por ejecución |
| `HU_MAX_REANALISIS` | `2` | Reanálisis permitidos tras MODIFIED |
| `HU_MAX_REINTENTOS` | `2` | Reintentos ante errores transitorios |
| `HU_MAX_LLAMADAS_LLM_POR_HISTORIA` | `12` | Límite de llamadas al modelo por historia |
| `HU_PRECIO_ENTRADA_1M` / `HU_PRECIO_SALIDA_1M` | *(vacío = precio de lista de `gemini-2.5-flash`: 0.30 / 2.50 USD)* | USD por millón de tokens para estimar el costo; los tokens de razonamiento se cuentan como salida. Con otro modelo y sin estas variables no se calcula costo |
