# Ingeniería de Software Aumentada con Inteligencia Artificial

Proyecto base que consume el bucket de Cloud Storage **`gs://adk_ing`** (proyecto GCP `servi-modelos-ia-dev`) y lo expone de dos formas:

- **`adk-ing`**: CLI y librería Python para listar, leer y sincronizar los objetos del bucket.
- **`agente_bucket`**: agente de [Google ADK](https://google.github.io/adk-docs/) con Gemini que responde preguntas usando los archivos del bucket.

```
.
├── agents/
│   └── agente_bucket/      # agente ADK (root_agent) con herramientas sobre el bucket
├── src/adk_ing/
│   ├── config.py           # variables de entorno / .env
│   ├── bucket.py           # BucketReader: list, read_text, download, sync
│   └── cli.py              # adk-ing ls | cat | sync
├── tests/                  # pruebas sin red (cliente GCS falso)
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

### Agente ADK

Desde la raíz del repo:

```bash
adk web agents                      # interfaz web en http://localhost:8000
adk run agents/agente_bucket        # en la terminal
```

El agente tiene dos herramientas, `listar_archivos` y `leer_archivo`; lee solo archivos de texto UTF-8 y trunca a 20 000 caracteres por archivo. El modelo se cambia con `ADK_MODEL` en el `.env`.

## Pruebas

```bash
pytest -q
```

Las pruebas usan un cliente de GCS falso, así que no requieren credenciales; también corren en GitHub Actions en cada push.

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
