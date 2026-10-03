# Ingeniería de Software Aumentada con Inteligencia Artificial

Proyecto base que consume el bucket de Cloud Storage **`gs://adk_ing`** (proyecto GCP `servi-modelos-ia-dev`) y lo expone de dos formas:

- **`adk-ing`**: CLI y librería Python para listar, leer y sincronizar los objetos del bucket.
- **`agente_bucket`**: agente de [Google ADK](https://google.github.io/adk-docs/) con Gemini que busca, lee y responde preguntas sobre los documentos ingestados en el bucket (PDF, Word, Excel, PowerPoint, CSV, texto), citando documento y página.

```
.
├── adk_web.py              # lanza ADK Web con el agente (python adk_web.py)
├── agents/
│   └── agente_bucket/      # agente ADK (root_agent) con herramientas sobre los documentos
├── docs_ejemplo/           # documentos ficticios para probar (PDF, DOCX, XLSX, PPTX, MD, PDF escaneado)
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

Las pruebas no requieren credenciales: usan los documentos de `docs_ejemplo/`, un cliente de GCS falso y un modelo simulado que recorre el flujo completo de ADK (buscar → responder citando la fuente). También corren en GitHub Actions en cada push.

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
