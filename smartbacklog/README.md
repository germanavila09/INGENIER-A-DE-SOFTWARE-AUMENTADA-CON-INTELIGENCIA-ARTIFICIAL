# Smart Product Backlog — MVP

Plataforma de gestión de historias de usuario (HU) para el Smart Product Backlog del curso.
La IA la hacen agentes Google ADK: agentes externos que se conectan a esta API, o los agentes embebidos
(Gemini) que generan, refinan y dimensionan HU (sección 4). Sin agentes configurados (`AGENT_PROVIDER=none`)
la plataforma funciona igual y no llama a ningún LLM.

Incluye:

- Login, roles por proyecto y administración de usuarios y API keys.
- Proyectos, épicas, HU con estado, asignación, auditoría y reportes (CSV/JSON).
- **Revisión humana (HITL) de cada HU**: cuatro agentes (analista → arquitectura ∥ QA ∥ estimación) la revisan, una
  política en código le asigna un nivel de riesgo 0/1/2 y una persona con el rol adecuado decide (aprobar, pedir cambios,
  falta información, descartar). Bandeja en `/p/<CLAVE>/revision`; detalle en la sección «Revisión humana (HITL)».
- Validación HITL obligatoria por etapa (1–5): no se aprueba una etapa sin aprobar la anterior. La etapa 1 exige además
  que todas las HU activas estén aprobadas en la revisión humana.
- Dimensionamiento con puntos de función, tamaño no funcional por restricciones (seguridad, privacidad, rendimiento...)
  con un grafo de contexto aprobado por una persona, y proyección FinOps del costo de generar el backlog con IA (sección 3.2.1).
- Documentos de entrada desde una carpeta local **o** un bucket de Cloud Storage (sección 1.2).
- API para que ADK cree proyectos, usuarios, HU y asignaciones, y para el ciclo de refinamiento.
- UI web: dashboard con pipeline, backlog jerárquico, tablero Kanban, detalle de HU, reportes y miembros.

---

## 1. Puesta en marcha

### 1.1 Requisitos

| Para | Necesitas |
|---|---|
| Correr con Docker (recomendado) | Docker con el plugin `compose` |
| Correr sin Docker | Python 3.12 y un Postgres (puede ser el de `docker compose up -d db`) |
| Agentes con Gemini (opcional) | Una API key de Gemini (Google AI Studio). Ver sección 4.5 |
| Documentos en un bucket (opcional) | Un bucket de Cloud Storage, `gcloud` instalado y una identidad con permiso de lectura sobre el bucket |

Todo se ejecuta desde la carpeta del proyecto (donde está `docker-compose.yml`). La configuración vive en `.env`:

```bash
cd SmartBacklogIA
cp .env.example .env      # luego edita .env: como mínimo SESSION_SECRET
```

### 1.2 Elegir la fuente de documentos: carpeta local **o** bucket

Los agentes leen los documentos de los proyectos (BPMN, PDF, MD, TXT) de **una sola** fuente. Se elige con
`AGENT_DOCS_SOURCE` y no se pueden mezclar: si `.env` combina variables de las dos, la app no arranca y dice cuál sobra.

| | Carpeta local (`folder`, por defecto) | Bucket (`gcs`) |
|---|---|---|
| Dónde van los archivos | `documents/<CLAVE>/archivo` | `gs://<bucket>/<prefijo>/<CLAVE>/archivo` |
| Variables en `.env` | `AGENT_DOCS_SOURCE=folder` y nada más del bucket | `AGENT_DOCS_SOURCE=gcs`, `AGENT_DOCS_BUCKET` (obligatoria), `AGENT_DOCS_PREFIX` y `GCP_PROJECT` (opcionales) |
| Deben quedar **vacías** | `AGENT_DOCS_BUCKET`, `AGENT_DOCS_PREFIX`, `GCP_PROJECT` | — (`AGENT_DOCS_DIR` se ignora) |
| Credenciales | Ninguna | Application Default Credentials o una cuenta de servicio con `roles/storage.objectViewer` |
| Ejemplo incluido | `documents/PAGO/` (BPMN y reunión) | Súbelo con `gcloud storage cp` (sección 1.4, paso 3) |

`<CLAVE>` es la clave del proyecto en mayúsculas (`PAGO`). Un proyecto solo ve su propia carpeta. Detalle de formatos
y reglas: secciones 4.2 y 4.3.

### 1.3 Opción A: Docker + carpeta local

1. **`.env`** (lo que no aparece queda con el valor de `.env.example`):
   ```
   SESSION_SECRET=un-valor-largo-y-aleatorio
   AGENT_DOCS_SOURCE=folder
   AGENT_DOCS_BUCKET=
   AGENT_DOCS_PREFIX=
   GCP_PROJECT=
   # Agentes: stub = sin LLM (para probar el flujo); adk = Gemini con API key
   AGENT_PROVIDER=adk
   AGENT_MODEL=gemini-2.5-flash
   GOOGLE_API_KEY=tu-clave-de-aistudio.google.com/apikey
   GOOGLE_GENAI_USE_VERTEXAI=false
   ```
   Este modo no necesita GCP: ni bucket, ni Vertex AI, ni credenciales de `gcloud`. En Windows,
   `_ejecucion\configurar_local.ps1` deja el `.env` así y pide la API key sin mostrarla.
2. **Documentos:** ponlos en `documents/<CLAVE>/`. El ejemplo `documents/PAGO/` ya existe. Docker monta `./documents`
   en `/data/documents` (solo lectura); no hace falta reconstruir al agregar archivos.
3. **Levantar y cargar datos de demo:**
   ```bash
   docker compose up --build -d                      # Postgres + app; aplica las migraciones al arrancar
   docker compose exec app python scripts/seed.py    # una sola vez: admin + proyecto SPB de demo
   ```
4. **Verificar:** sección 1.6.

### 1.4 Opción B: Docker + bucket de Cloud Storage

1. **Bucket y permisos** (una vez; con tu cuenta de GCP):
   ```bash
   gcloud auth login
   gcloud storage buckets create gs://mi-bucket-spb --project=mi-proyecto --location=us-central1   # si no existe
   # La identidad con la que corre la app solo necesita leer:
   gcloud storage buckets add-iam-policy-binding gs://mi-bucket-spb \
     --member="user:tu-correo@empresa.com" --role="roles/storage.objectViewer"
   ```
   Para subir archivos (paso 3) tu usuario necesita además permiso de escritura (`roles/storage.objectCreator`).
2. **`.env`:**
   ```
   SESSION_SECRET=un-valor-largo-y-aleatorio
   AGENT_DOCS_SOURCE=gcs
   AGENT_DOCS_BUCKET=mi-bucket-spb
   AGENT_DOCS_PREFIX=spb            # opcional; vacío = raíz del bucket
   GCP_PROJECT=mi-proyecto          # opcional; si no, el de las credenciales
   AGENT_PROVIDER=stub
   ```
3. **Subir los documentos** con la misma estructura que la carpeta (`<prefijo>/<CLAVE>/archivo`):
   ```bash
   gcloud storage cp documents/PAGO/* gs://mi-bucket-spb/spb/PAGO/
   gcloud storage ls gs://mi-bucket-spb/spb/PAGO/
   ```
   Solo cuentan los archivos directos de `<CLAVE>/`: las subcarpetas se ignoran.
4. **Credenciales para el contenedor.** Elige una:
   - **Tu usuario (desarrollo):**
     ```bash
     gcloud auth application-default login
     gcloud auth application-default set-quota-project mi-proyecto   # si gcloud lo pide
     ```
     y en `docker-compose.yml` descomenta, en el servicio `app`, el volumen
     `- ~/.config/gcloud:/root/.config/gcloud:ro`.
   - **Cuenta de servicio (servidor):** crea una clave JSON de una cuenta de servicio con `roles/storage.objectViewer`
     sobre el bucket, móntala en el servicio `app` (por ejemplo `- ./secrets/gcp-sa.json:/run/secrets/gcp-sa.json:ro`)
     y agrega a `.env`:
     ```
     GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-sa.json
     ```
     No subas la clave al repositorio (`secrets/` debe estar en `.gitignore`).
5. **Levantar y cargar datos de demo:**
   ```bash
   docker compose up --build -d
   docker compose exec app python scripts/seed.py
   ```
   La carpeta `./documents` sigue montada, pero con `gcs` la app no la lee.
6. **Verificar:** sección 1.6. Si la app no puede leer el bucket, la API responde `503` con el motivo y las pantallas
   lo muestran; el resto de la plataforma sigue funcionando.

### 1.5 Sin Docker

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
docker compose up -d db                              # o tu propio Postgres
```

En `.env` cambia la base al puerto que publica Docker (`5433`) y elige la fuente como en 1.3 o 1.4:

```
DATABASE_URL=postgresql+psycopg://spb:spb@localhost:5433/spb
AGENT_DOCS_SOURCE=folder          # o gcs, con sus variables
AGENT_DOCS_DIR=documents          # solo folder; ruta relativa a esta carpeta
```

```bash
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed.py
.venv/bin/uvicorn app.main:app --reload
```

- Ejecuta los comandos **desde la carpeta del proyecto**: la app lee `.env` de la carpeta actual.
- Con `gcs` y tu usuario basta `gcloud auth application-default login`. Con cuenta de servicio, `GOOGLE_APPLICATION_CREDENTIALS`
  debe estar en el **entorno del proceso** (`export GOOGLE_APPLICATION_CREDENTIALS=/ruta/clave.json` antes de `uvicorn`):
  la app lee `.env` para su configuración, pero no lo copia al entorno, y la librería de Google solo mira el entorno.
  En Docker no hace falta porque `env_file` sí lo pone en el entorno del contenedor.

### 1.6 Verificar que todo funciona

Abre http://localhost:8000/login (usuarios en la tabla de abajo) y Swagger en http://localhost:8000/docs.
Desde la terminal:

```bash
B=http://localhost:8000
curl -s -c cookies.txt -H 'Content-Type: application/json' \
  -d '{"email":"admin@spb.local","password":"cambiar-me-ya"}' $B/api/v1/auth/login

curl -s -b cookies.txt $B/api/v1/agents/status
# → "configured": true, "provider": "stub" | "adk", "docs_source": "folder" | "gcs"

# Los documentos de ejemplo son del proyecto PAGO: créalo (el admin queda como product owner)
curl -s -b cookies.txt -H 'Content-Type: application/json' \
  -d '{"key":"PAGO","name":"Pagos en línea"}' $B/api/v1/projects

curl -s -b cookies.txt $B/api/v1/projects/PAGO/agents/documents
# → "source": "folder" con la ruta de documents/PAGO, o "gcs" con gs://mi-bucket-spb/spb/PAGO/
```

Flujo completo con los agentes (con `AGENT_PROVIDER=stub` no gasta cuota; con `adk` usa Gemini):

```bash
# 1. Generar HU desde todos los documentos del proyecto (agente de documentos como AgentTool)
curl -s -b cookies.txt -H 'Content-Type: application/json' -d '{}' \
  $B/api/v1/projects/PAGO/agents/generate-stories/from-documents
# 2. Extraer el grafo de contexto (funcionalidades, entidades, restricciones) y revisarlo
curl -s -b cookies.txt -H 'Content-Type: application/json' -d '{}' $B/api/v1/projects/PAGO/agents/extract-context
curl -s -b cookies.txt $B/api/v1/projects/PAGO/context            # revisa el borrador; corrígelo con PUT .../context/draft
curl -s -b cookies.txt -H 'Content-Type: application/json' -d '{"notes":"Revisado"}' \
  $B/api/v1/projects/PAGO/context/draft/approve                     # solo una persona
# 3. Dimensionar el backlog: puntos de función + restricciones activadas/violadas del grafo aprobado
curl -s -b cookies.txt -H 'Content-Type: application/json' -d '{}' $B/api/v1/projects/PAGO/agents/size-backlog
# 4. Proyección FinOps (costo, impuesto de contexto, horas) con otro modelo y escenario
curl -s -b cookies.txt "$B/api/v1/projects/PAGO/finops?model=claude-sonnet-5-5&scenario=high"
```

El paso 2 es opcional: sin grafo aprobado, el dimensionamiento mide solo puntos de función. Con grafo, una HU que
viola una restricción bloquea la aprobación de la etapa 3 hasta que se corrija o se registre una excepción.

Con `AGENT_EXECUTION=background` (por defecto) las respuestas `202` traen `"status": "queued"`; consulta el resultado
con `GET /api/v1/agents/runs/{id}` o en la pantalla `/p/PAGO/agents`. El resultado del paso 3 también aparece en
`/p/PAGO/reports`. Las etapas HITL (sección 2) las aprueba siempre una persona.

### 1.7 Cambiar de fuente, reconstruir y reiniciar

| Quiero | Hago |
|---|---|
| Pasar de carpeta a bucket | Opción B pasos 1–4 y `docker compose up -d app` (recrea el contenedor para leer `.env`) |
| Volver a la carpeta | `AGENT_DOCS_SOURCE=folder` **y** vaciar `AGENT_DOCS_BUCKET`, `AGENT_DOCS_PREFIX` y `GCP_PROJECT`; luego `docker compose up -d app` |
| Aplicar cambios de código | `docker compose up --build -d app` |
| Empezar con la base vacía (por ejemplo, tras el cambio a puntos de función o al grafo de contexto) | `docker compose down -v`, `docker compose up --build -d` y de nuevo `seed.py` |

Un contenedor que ya corre no ve los cambios de `.env`: recréalo con `docker compose up -d app`.

| Servicio | Dirección | Notas |
|---|---|---|
| App (UI y API) | http://localhost:8000 | `/login` para la UI, `/docs` para Swagger |
| Postgres | `localhost:5433` | usuario `spb`, clave `spb`, base `spb`. Puerto 5433 para no chocar con un Postgres local en 5432 |

Comandos útiles:

```bash
docker compose ps                 # estado de los servicios
docker compose logs -f app        # logs de la API (aquí aparece el motivo si la app no arranca)
docker compose down               # parar; los datos quedan en el volumen pgdata
docker compose down -v            # parar y borrar la base
```

### Usuarios de prueba (creados por el seed)

| Correo | Rol | Contraseña |
|---|---|---|
| `admin@spb.local` | Administrador del sistema (ve todos los proyectos y `/admin`) | `cambiar-me-ya` |
| `po@spb.local` | Product owner de SPB | `demo-spb-2026` |
| `sm@spb.local` | Scrum master de SPB | `demo-spb-2026` |
| `dev@spb.local` | Developer de SPB | `demo-spb-2026` |
| `stake@spb.local` | Stakeholder de SPB (solo lectura) | `demo-spb-2026` |

El admin se configura con `SEED_ADMIN_EMAIL` y `SEED_ADMIN_PASSWORD`. Cambia todas las contraseñas antes de exponer el servicio.

---

## 2. Documentación de la API

- **Swagger interactivo:** http://localhost:8000/docs
- **Esquema OpenAPI:** http://localhost:8000/openapi.json

Se autentica de dos formas:

| Quién | Cómo | Dónde |
|---|---|---|
| Humano (desde Swagger o terminal) | `POST /api/v1/auth/login` devuelve cookie de sesión | Swagger guarda la cookie al hacer login |
| Agente ADK (servicio) | Header `X-API-Key: spb_...` | Botón **Authorize** en Swagger, o el header en cada llamada |

Para crear una API key: entra con el admin, luego `POST /api/v1/admin/api-keys` con `{"name": "adk-clasificacion"}`. La clave en claro se devuelve **una sola vez**. También se puede generar en la pantalla `/admin`.

### Roles y permisos por proyecto

| Rol | Puede |
|---|---|
| Admin del sistema | Todo; crear usuarios y API keys |
| `product_owner` | Todo en el proyecto, incluida la aprobación HITL de cualquier nivel |
| `scrum_master` | Crear y editar HU, asignar, mover estados, pedir refinamiento; decidir HU de nivel 0 y 1 |
| `developer` | Mover HU entre `lista` → `en_progreso` → `en_revision` (y regresar); comentar en la revisión humana |
| `stakeholder` | Solo lectura; puede comentar en la revisión humana |
| Servicio ADK (API key) | Ingesta, crear HU, asignar, mover estados, refinamiento, lanzar revisiones. No aprueba ni decide HITL |

### Revisión humana (HITL) de cada HU

Es la función central. Cada HU pasa por una revisión multiagente y luego por una persona:

1. **Agentes** (`POST .../stories/{code}/review` o «Revisar con agentes» en `/p/<CLAVE>/revision`): el analista
   evalúa INVEST, ambigüedades y preguntas y propone una versión mejorada; en paralelo, arquitectura (impactos y cambios
   críticos: migración de datos, seguridad, API pública, costo…), QA (casos Dado/Cuando/Entonces y criterios no
   verificables) y estimación (tipo HU-IA/HU-Tradicional y factores; el sistema calcula los puntos Fibonacci).
2. **Política en código** (`app/services/hitl.py`, no la decide el LLM): reúne motivos y asigna el nivel.

| Nivel | Cuándo | Quién decide |
|---|---|---|
| 2 · Requiere al product owner | Cambio crítico, conflicto entre agentes, ambigüedad o pregunta bloqueante, sin criterios o mayoría no verificable, un agente pide intervención, confianza < 0,60, agente sin respuesta, HU ya aprobada que cambia | Solo `product_owner` (o admin) |
| 1 · Revisar | Confianza entre 0,60 y 0,85, algún criterio no verificable, mejoras de redacción, HU generada por IA, spike o estimación incompleta | `product_owner` o `scrum_master` |
| 0 · Sin observaciones | Nada de lo anterior | `product_owner` o `scrum_master` |

Nada se aprueba solo, en ningún nivel. Decisiones (`POST .../stories/{code}/hitl/decision`, o los botones de la bandeja):

- **Aprobar** (con o sin la propuesta del analista): aplica la propuesta si se pide, fija el tipo si estaba vacío y pasa
  la HU de borrador a *refinada*.
- **Pedir cambios** (comentario obligatorio): el agente refina la HU con ese comentario y la vuelve a revisar sola.
- **Falta información** (comentario obligatorio): la HU sigue esperando; las respuestas del equipo entran como notas
  humanas en la siguiente revisión.
- **Descartar** (motivo obligatorio).
- **Comentar**: cualquier miembro, incluidos developer y stakeholder.

Un servicio con API key puede lanzar revisiones pero **nunca decide** (403). Si la HU cambia después de revisada, la
revisión queda *desactualizada* y vuelve a la cola de los agentes. La decisión lleva `expected_version` para no decidir
sobre cambios ajenos (409). Todo queda en el historial de la HU (`hitl_reviewed`, `hitl_aprobar`, …).

### Estados de una HU

Flujo principal: `borrador` → `en_refinamiento` → `refinada` → `lista` → `en_progreso` → `en_revision` → `hecha`.

Salidas permitidas (la matriz completa está en `app/services/workflow.py`):

| Desde | Puede pasar a |
|---|---|
| `borrador` | `en_refinamiento`, `descartada` |
| `en_refinamiento` | `refinada`, `borrador`, `descartada` |
| `refinada` | `lista`, `en_refinamiento`, `descartada` |
| `lista` | `en_progreso`, `refinada`, `descartada` |
| `en_progreso` | `en_revision`, `lista` |
| `en_revision` | `hecha`, `en_progreso` |
| `descartada` | `borrador` |
| `hecha` | (final) |

Transición fuera de la matriz → `422`. Transición válida sin permiso para el rol → `403`.
Matriz completa en `app/services/workflow.py`.

---

## 3. Contrato para agentes ADK

Base URL: `http://localhost:8000`. Todas las llamadas llevan `X-API-Key` y `Content-Type: application/json`.

### 3.1 Crear proyecto, usuarios, miembros, épicas, HU y asignaciones

**Endpoint:** `POST /api/v1/ingest/projects`
**Respuesta:** `201 Created`

Es **idempotente**: si el `key` del proyecto ya existe, lo actualiza; si una HU trae un `external_id` que ya existe en el proyecto, la actualiza en vez de duplicarla. Por eso ADK puede reenviar el mismo backlog sin crear duplicados.

**Request:**

```json
{
  "key": "PAGO",
  "name": "Pagos en línea",
  "description": "Backlog generado desde reuniones con stakeholders",
  "users": [
    {"email": "ana.po@empresa.co", "name": "Ana Pérez", "password": "clave-ana-2026"},
    {"email": "luis.dev@empresa.co", "name": "Luis Gómez", "password": "clave-luis-2026"}
  ],
  "members": [
    {"email": "ana.po@empresa.co", "role": "product_owner"},
    {"email": "luis.dev@empresa.co", "role": "developer"}
  ],
  "epics": [
    {
      "title": "Checkout",
      "description": "Flujo de pago",
      "stories": [
        {
          "external_id": "reunion-12-09-hu-1",
          "title": "Pagar con tarjeta de crédito",
          "role": "cliente",
          "need": "pagar con tarjeta",
          "benefit": "completar la compra",
          "acceptance_criteria": "- [ ] Muestra error si la tarjeta es rechazada\n- [ ] Permite reintentar hasta 3 veces",
          "type": "HU_TRADICIONAL",
          "type_justification": "Integración con pasarela",
          "fp_components": [
            {"name": "Pago", "type": "ILF", "det": 12, "ret": 2},
            {"name": "Pagar con tarjeta", "type": "EI", "det": 8, "ftr": 2}
          ],
          "priority_rank": 1,
          "source_ref": "Reunión 12/09, min 4:10",
          "assignee_email": "luis.dev@empresa.co"
        }
      ]
    }
  ],
  "stories": [
    {
      "external_id": "reunion-12-09-hu-2",
      "title": "Clasificar fraude con IA",
      "type": "HU_IA",
      "source_ref": "Reunión 12/09, min 17:32"
    }
  ]
}
```

**Campos:**

| Campo | Obligatorio | Descripción |
|---|---|---|
| `key` | sí | 2–10 caracteres, mayúsculas y dígitos, empieza con letra. Ej. `PAGO` |
| `name` | sí | Nombre del proyecto |
| `users[]` | no | Usuarios a crear si no existen. `password` opcional (mínimo 8). Sin contraseña no pueden iniciar sesión |
| `members[]` | no | `email` debe existir en `users` o ya en el sistema. `role`: `product_owner`, `scrum_master`, `developer`, `stakeholder` |
| `epics[].title` | sí (si hay épicas) | Se crea si no existe; se identifica por título |
| `stories[].title` | sí | Mínimo 3 caracteres |
| `stories[].external_id` | recomendado | Id estable del agente. Clave de idempotencia |
| `stories[].type` | no | `HU_IA` o `HU_TRADICIONAL` |
| `stories[].assignee_email` | no | Correo de un miembro del proyecto. Si no es miembro, responde `422` |
| `stories[]` (raíz) | no | HU sin épica |

Campos opcionales de HU: `role`, `need`, `benefit`, `acceptance_criteria` (**markdown**, máx. 20 000 caracteres; ver [sección 3.5), `type_justification`, `fp_components` (lista de funciones IFPUG; el servidor calcula `function_points`, ver sección 3.2.1), `priority_rank` (entero), `priority_method`, `source_ref`.

**Response:**

```json
{
  "project": "PAGO",
  "project_created": false,
  "users_created": 2,
  "stories_created": 2,
  "stories_updated": 0
}
```

Respuestas de error: `401` (API key inválida), `422` (un miembro no existe o un campo no cumple validación).

#### Crear o actualizar HU sin reingestar todo

- `POST /api/v1/projects/{key}/stories` crea una HU. Usa el mismo cuerpo que una HU de `ingest` (con `epic` por título si se quiere).
- `POST /api/v1/projects/{key}/stories:bulk` body `{"stories": [ ... ]}` crea o actualiza en lote por `external_id`. Respuesta: `{"created": n, "updated": m}`.
- `PATCH /api/v1/projects/{key}/stories/{code}` actualiza campos. Puede enviar `expected_version` para evitar sobrescribir cambios ajenos (`409` si no coincide).

#### Asignar una HU

**Endpoint:** `POST /api/v1/projects/{key}/stories/{code}/assign`

```json
{"assignee_email": "luis.dev@empresa.co"}
```

- Para quitar la asignación: `{"assignee_email": ""}`.
- Un humano también puede usar `{"user_id": 3}`.
- El correo debe ser miembro del proyecto; si no, `422`.

Respuesta: la HU completa, con `assignee: {id, name, email}`.

### 3.2 Refinar una HU

El ciclo tiene cuatro pasos: solicitar, tomar, resolver y consultar.

#### Paso 1 — Solicitar refinamiento

**Endpoint:** `POST /api/v1/projects/{key}/stories/{code}/status`

```json
{"status": "en_refinamiento", "expected_version": 2}
```

`expected_version` es opcional. Al entrar a `en_refinamiento` se crea una solicitud `pending`. Si la HU se mueve a otro estado mientras tanto, la solicitud pasa a `cancelled`.

Si hay un webhook configurado (`ADK_WEBHOOK_URL`), el sistema hace `POST` a esa URL con `{id, project_key, status, story, claimed_by, created_at}`, donde `story` es el mismo `input` del paso 2.

#### Paso 2 — Consultar la cola

**Endpoint:** `GET /api/v1/refinements?status=pending&project_key=PAGO`

Parámetros: `status` (`pending`, `in_progress`, `completed`, `cancelled`; por defecto `pending`) y `project_key` opcional.

**Response:**

```json
[
  {
    "id": 3,
    "story_code": "PAGO-2",
    "project_key": "PAGO",
    "status": "pending",
    "claimed_by": null,
    "claimed_at": null,
    "completed_at": null,
    "created_at": "2026-10-10T04:33:40.929599+00:00",
    "input": {
      "code": "PAGO-2",
      "epic": "Checkout",
      "title": "Pagar con tarjeta de crédito",
      "role": "cliente",
      "need": "pagar con tarjeta",
      "benefit": "completar la compra",
      "acceptance_criteria": "- [ ] Muestra error si la tarjeta es rechazada",
      "type": "HU_TRADICIONAL",
      "type_justification": "Integración con pasarela",
      "version": 2
    },
    "result": null,
    "error": null
  }
]
```

El agente debe usar `input` como contexto para el LLM. Guarda `input.version`: ese valor va en el paso 4.

#### Paso 3 — Tomar la solicitud

**Endpoint:** `POST /api/v1/refinements/{id}/claim`

Sin cuerpo. Pasa la solicitud a `in_progress` y la asocia a la API key que la toma. Si ya fue tomada por otro agente, responde `409`. Esto evita que dos agentes procesen la misma HU.

#### Paso 4 — Devolver el resultado

**Endpoint:** `POST /api/v1/refinements/{id}/result`

**Request:**

```json
{
  "expected_version": 2,
  "title": "Pagar con tarjeta de crédito y débito",
  "role": "cliente",
  "need": "pagar mi compra con tarjeta de crédito o débito",
  "benefit": "completar la compra sin salir del sitio",
  "acceptance_criteria": "- [ ] Muestra error claro si la tarjeta es rechazada\n- [ ] Permite reintentar hasta 3 veces\n- [ ] Confirma el pago por correo",
  "notes": "Se separó el caso débito; se añadió el criterio de reintento."
}
```

| Campo | Obligatorio | Descripción |
|---|---|---|
| `expected_version` | sí | El `input.version` que se leyó en el paso 2 |
| `title`, `role`, `need`, `benefit`, `acceptance_criteria` | no | Solo se actualizan los campos enviados (`null` o ausente = sin cambio). `acceptance_criteria` es un string markdown; una lista responde `422` |
| `notes` | no | Se guarda en el historial de la HU |

Efecto: la HU pasa a `refinada`, su versión sube, la solicitud pasa a `completed` y queda registrado en el historial qué cambió. Un humano la revisa y la pasa a `lista`.

**Response:** la solicitud con `status: "completed"` y `result` con el cuerpo enviado.

**Errores:**

| Código | Cuándo |
|---|---|
| `409` | La solicitud no está `in_progress` (no se tomó con claim, o ya se resolvió) |
| `409` | `expected_version` no coincide: la HU cambió. Vuelve a leer la HU y reintenta |
| `409` | La HU ya no está en `en_refinamiento` |
| `401` | API key inválida |

### 3.2.1 Dimensionar (etapa 3): puntos de función + FinOps

Reemplaza la estimación por story points. Método completo, fuentes verificadas, fórmulas y supuestos:
[`docs/metodo-fpa-finops.md`](docs/metodo-fpa-finops.md).

**Puntos de función (IFPUG sin ajustar).** Cada HU guarda `function_points` y `fp_analysis` (funciones con su complejidad y puntos).

- **Con agentes:** `POST /api/v1/projects/{key}/stories/{code}/agents/size` (una HU) o `POST /api/v1/projects/{key}/agents/size-backlog` (todas las HU activas sin puntos, en orden de prioridad; `{"resize": true}` recalcula todas). Responden `202` con un `AgentRun`. Dos agentes ADK en secuencia: uno identifica conceptos (entidades y transacciones) y otro los clasifica como ILF/EIF/EI/EO/EQ con sus conteos; **el servidor calcula los puntos**. Una entidad que ya contó una HU anterior suma 0 (`reused`). No cambian el estado de la HU ni aprueban la etapa.
- **Corrección humana (HITL):** `PATCH /api/v1/projects/{key}/stories/{code}` con `fp_components`:
  ```json
  {"fp_components": [
    {"name": "Pago", "type": "ILF", "det": 12, "ret": 2},
    {"name": "Cliente", "type": "ILF", "det": 8, "ret": 1, "reused": true},
    {"name": "Registrar pago", "type": "EI", "det": 8, "ftr": 2},
    {"name": "Comprobante", "type": "EO", "det": 10, "ftr": 2}
  ]}
  ```
  `ILF`/`EIF` llevan `det` y `ret`; `EI`/`EO`/`EQ` llevan `det` y `ftr`. `422` si falta un conteo, un tipo no existe o un valor no es entero positivo. El cambio queda en el historial.

**Contexto y restricciones (tamaño no funcional).** Los puntos de función no miden restricciones (seguridad, privacidad,
rendimiento) y cada HU se mediría aislada. Por eso el proyecto tiene un **grafo de contexto**: funcionalidades y entidades
(existentes o planificadas) y restricciones con categoría, severidad y regla.

- **El agente propone, una persona aprueba:** `POST /api/v1/projects/{key}/agents/extract-context` lee los documentos
  (carpeta o bucket) y guarda un **borrador**. `GET /api/v1/projects/{key}/context` lo muestra; `PUT .../context/draft`
  lo corrige (o lo crea a mano); `POST .../context/draft/approve` o `.../reject` lo revisa. Solo una persona aprueba
  (una API key de servicio recibe `403`). El dimensionamiento usa solo la última versión aprobada.
- **Al dimensionar** con un grafo aprobado, un agente arquitecto corre primero e informa qué funcionalidades toca la HU,
  qué entidades ya existen (suman 0 FP), qué restricciones activa y cuáles **viola**. El servidor descarta ids que no
  existen y toma la severidad del grafo. Cada HU guarda `nfr_points` (baja 2, media 4, alta 7, crítica 10 por restricción)
  y `context_analysis`; FinOps convierte los puntos NFR en iteraciones y tokens extra.
- **Violaciones (HITL):** una HU que incumple una restricción **bloquea la aprobación de la etapa 3**. Se resuelve
  cambiando la HU y volviendo a dimensionarla, o con una excepción justificada:
  `POST /api/v1/projects/{key}/stories/{code}/violations/{constraint_id}/waive` con `{"justification": "..."}` (mín. 10
  caracteres, solo personas). Todo queda en el historial.

Formato de un nodo de restricción para `PUT .../context/draft`:

```json
{"id": "privacidad-datos", "kind": "constraint", "name": "Datos locales", "category": "privacidad",
 "severity": "critica", "rule": "Los datos de usuarios no salen de servidores propios",
 "keywords": ["perfil", "api externa"]}
```

**FinOps de generación autónoma** (sin LLM, calculado al vuelo con los puntos guardados):

- `GET /api/v1/projects/{key}/finops`: por HU en orden de generación, la huella de contexto del repositorio antes de generarla, iteraciones, tokens de entrada/caché/salida, costo USD, **impuesto de contexto** (lo que cuesta de más por el repositorio acumulado) y horas; más totales, escenarios bajo/esperado/alto y media PERT. Parámetros opcionales: `model` (`claude-opus-5-5` por defecto, `claude-sonnet-5-5`, `claude-haiku-5-5`), `scenario` y cualquier supuesto (`loc_per_fp`, `context_read_ratio`, `cache_hit_ratio`, precios...); todos aparecen en Swagger.
- `GET /api/v1/projects/{key}/finops/export?format=csv|json`.
- `GET /api/v1/finops/models`: tabla de precios con fecha (`as_of`) y fuente.

Los supuestos por defecto **no están calibrados**: úsalos como orden de magnitud y ajústalos con datos reales (sección 9 del método).

### 3.3 Otros endpoints útiles para ADK

| Método y ruta | Uso |
|---|---|
| `GET /api/v1/projects/{key}/stories?status=&type=&epic_id=&assignee_id=` | Listar HU con filtros |
| `GET /api/v1/projects/{key}/stories/{code}` | Detalle de una HU |
| `GET /api/v1/projects/{key}/stories/{code}/events` | Historial de cambios |
| `POST /api/v1/projects/{key}/stories/{code}/status` | Mover de estado (valida la matriz) |
| `GET /api/v1/projects/{key}/pipeline` | Estado de las etapas HITL |
| `GET /api/v1/projects/{key}/reports/summary` | Resumen por estado, tipo, épica y asignado |
| `GET /api/v1/projects/{key}/export?format=csv\|json` | Exportar backlog |
| `GET /api/v1/refinements/{id}` | Detalle de una solicitud de refinamiento |

La lista completa con todos los campos está en Swagger (`/docs`).

### 3.4 Ejemplo completo con curl

```bash
B=http://localhost:8000
KEY="spb_...."   # creada en /admin o con POST /api/v1/admin/api-keys
J='Content-Type: application/json'

# 1. Ingesta
curl -s -H "X-API-Key: $KEY" -H "$J" -d @ingesta.json $B/api/v1/ingest/projects

# 2. Solicitar refinamiento de PAGO-2 (versión actual 2)
curl -s -H "X-API-Key: $KEY" -H "$J" -d '{"status":"en_refinamiento"}' $B/api/v1/projects/PAGO/stories/PAGO-2/status

# 3. Ver la cola y obtener el id
curl -s -H "X-API-Key: $KEY" "$B/api/v1/refinements?status=pending&project_key=PAGO"

# 4. Tomar la solicitud
curl -s -X POST -H "X-API-Key: $KEY" $B/api/v1/refinements/3/claim

# 5. Devolver el resultado
curl -s -H "X-API-Key: $KEY" -H "$J" -d @resultado.json $B/api/v1/refinements/3/result
```

Respuestas que obtuvimos al probar este flujo:

- Paso 2: `{"status": "en_refinamiento", "version": 2}`.
- Paso 5 con versión correcta: `status: "completed"`. La HU queda en `refinada` con versión 3.
- Paso 5 repetido con la versión vieja: `409`.

---

### 3.5 Criterios de aceptación en markdown

`acceptance_criteria` es un **string en markdown** en todas las entradas y salidas (ingesta, HU, refinamiento, export CSV y la UI). Formato recomendado:

```markdown
- [ ] El cliente ve el motivo del rechazo en lenguaje claro
- [ ] Se permiten hasta 3 reintentos por hora

## Escenario de error
- **Dado** una tarjeta rechazada, **cuando** el cliente confirma, **entonces** ve el error
```

La UI lo muestra renderizado con markdown **sin HTML**: si el texto trae etiquetas (`<script>`), se muestran escapadas. Una lista JSON responde `422`.

---

## 4. Agentes IA embebidos (ADK + Gemini)

Los agentes viven dentro del backend, en `app/agents/`. Sirven para **generar HU desde documentos**, **refinar HU** y **dimensionarlas con puntos de función** (sección 3.2.1). No necesitan MCP ni un servicio aparte. Los documentos se leen de **una sola fuente**: una carpeta local (por defecto) **o** un bucket de Cloud Storage (sección 4.3), nunca de ambas.

```
app/agents/
  ports.py            contrato: DTOs (GeneratedStory, RefinementOutput) y protocolo AgentProvider
  registry.py         elige el proveedor según AGENT_PROVIDER: none | stub | adk
  runner.py           orquesta: crea agent_runs, persiste HU con app/services, aplica reglas HITL
  prompts.py          instrucciones del agente (formato de HU, criterios en markdown)
  document_tools.py   herramientas de solo lectura del agente de documentos (listar y leer la carpeta o el bucket)
  providers/
    stub.py           determinista, sin LLM (demo y pruebas)
    adk.py            Google ADK: LlmAgent + Runner + output_schema → Gemini; AgentTool para el agente de documentos
app/services/documents/   lectura de PDF, BPMN, MD y TXT desde carpetas locales (folder.py) o Cloud Storage (gcs.py)
app/routers/agents.py     endpoints /api/v1/.../agents/*
```

**Separación de responsabilidades:** el proveedor solo devuelve datos validados por Pydantic. **No escribe en la base** y no tiene herramientas de escritura. Quien crea o actualiza HU es `AgentRunner`, con los mismos servicios que usa la API (permisos, auditoría, versiones).

**Reglas que siempre se cumplen, sin importar el modelo:**

- La generación solo crea HU en `borrador`. Reprocesar el mismo documento actualiza (no duplica): cada HU tiene un `external_id` determinista.
- El refinamiento pasa por el mismo flujo que un ADK externo (`claim` → `submit_result`) y termina en `refinada`. El agente **nunca aprueba etapas HITL ni mueve HU a `lista`**.
- Cada ejecución queda en `agent_runs` (estado, error, tokens, costo). Un fallo no deja HU a medias: la generación es una sola transacción.
- Los cambios del agente quedan en el historial de la HU con actor `agent:<proveedor>`.
- Solo product owner, scrum master y administradores pueden lanzar agentes. Developers y stakeholders reciben `403`.

### 4.1 Endpoints

| Método y ruta | Qué hace | Respuesta |
|---|---|---|
| `GET /api/v1/agents/status` | Proveedor, modelo y si está configurado | `200` |
| `GET /api/v1/projects/{key}/agents/documents` | Archivos de `documents/<KEY>/` | `200` |
| `POST /api/v1/projects/{key}/agents/generate-stories` | Genera HU. Body: `{"filename": "proceso.bpmn"}` **o** `{"document_text": "...", "source_name": "reunion"}`; opcional `instructions` | `202` con el run |
| `POST /api/v1/projects/{key}/agents/generate-stories/upload` | Igual, pero subiendo el archivo (`multipart`) | `202` |
| `POST /api/v1/projects/{key}/agents/generate-stories/from-documents` | Genera HU desde **todos** los documentos del proyecto con el agente de documentos (sección 4.4). Body opcional `{"instructions": "..."}` | `202` |
| `POST /api/v1/projects/{key}/stories/{code}/agents/refine` | Refina la HU (la pasa a `en_refinamiento` si hace falta). Body opcional `{"instructions": "..."}` | `202` |
| `GET /api/v1/projects/{key}/agents/runs` | Últimas 100 ejecuciones del proyecto | `200` |
| `GET /api/v1/agents/runs/{id}` | Detalle de una ejecución | `200` |

Errores: `503` si el proveedor no está configurado o el bucket no responde (el resto de la API sigue funcionando); `413` si el documento supera `AGENT_MAX_DOC_CHARS`; `422` si el archivo no se puede leer (PDF escaneado, BPMN con entidades externas, formato no soportado, ruta inválida); `404` si el archivo no existe en la carpeta del proyecto.

Ejemplo, generar desde un BPMN de la carpeta:

```bash
curl -s -H "Content-Type: application/json" -b cookies.txt \
  -d '{"filename": "proceso-pago.bpmn"}' \
  http://localhost:8000/api/v1/projects/PAGO/agents/generate-stories
# → 202 con {"id": 7, "status": "queued", ...}

curl -s -b cookies.txt http://localhost:8000/api/v1/agents/runs/7
# → {"status": "succeeded", "result": {"created": ["PAGO-4", "PAGO-5"], "updated": [], ...}, "tokens_in": ..., "cost_usd": ...}
```

Ejemplo, refinar una HU con el agente:

```bash
curl -s -H "Content-Type: application/json" -b cookies.txt \
  -d '{"instructions": "Agrega casos de error de la pasarela"}' \
  http://localhost:8000/api/v1/projects/PAGO/stories/PAGO-2/agents/refine
```

### 4.2 Carpetas de documentos

Cada proyecto tiene una subcarpeta con su **clave en mayúsculas**, dentro de `documents/` (en Docker se monta en `/data/documents`, solo lectura):

```
documents/
  PAGO/
    proceso-pago.bpmn    diagrama BPMN (Camunda, Bizagi, draw.io, ...)
    reunion-12-09.pdf    PDF con texto seleccionable (no escaneado)
    requisitos.md
    reunion.txt
```

| Formato | Cómo se lee |
|---|---|
| `.bpmn` | Se convierte en un resumen: procesos, participantes (carriles), actividades con su tipo y documentación, decisiones y flujo. Se parsea con `defusedxml`, que rechaza DTD y entidades externas |
| `.pdf` | Texto por página con `pypdf`. Un PDF sin texto (escaneo) responde `422`: hace falta OCR |
| `.md`, `.txt` | UTF-8 tal cual |

Un proyecto solo ve su subcarpeta. Las rutas con `..`, nombres con carpetas y symlinks que salen de la carpeta se rechazan. Los documentos se envían a Google al generar HU: usa solo los que tu cuenta esté autorizada a procesar.

`GET /api/v1/projects/{key}/agents/documents` devuelve `source` (`folder` o `gcs`), `folder` (la ruta o `gs://...`) y `documents`.

### 4.3 Documentos en un bucket de Cloud Storage (opcional)

Pasos para ponerlo en marcha (bucket, permisos, credenciales, Docker o local): sección 1.4. Aquí, las reglas.

La fuente de documentos es **excluyente**: `AGENT_DOCS_SOURCE=folder` (por defecto) **o** `AGENT_DOCS_SOURCE=gcs`. Con `gcs` no se lee la carpeta local (aunque siga montada), y con `folder` no se consulta el bucket. La API, las pantallas, el agente de documentos y `scripts/try_agent.py` usan siempre la misma fuente; `GET /api/v1/agents/status` la informa en `docs_source`.

La configuración se valida al arrancar. La app **no arranca** si:

| Configuración | Mensaje |
|---|---|
| `AGENT_DOCS_SOURCE=gcs` sin `AGENT_DOCS_BUCKET` | `AGENT_DOCS_SOURCE=gcs requiere AGENT_DOCS_BUCKET` |
| `AGENT_DOCS_SOURCE=folder` con `AGENT_DOCS_BUCKET`, `AGENT_DOCS_PREFIX` o `GCP_PROJECT` | `AGENT_DOCS_SOURCE=folder no usa ...: vacíalas, o usa AGENT_DOCS_SOURCE=gcs` |
| Un valor distinto de `folder` o `gcs` | error de validación de `agent_docs_source` |

Para leer los documentos desde un bucket, con la **misma estructura** (una carpeta por clave de proyecto):

```
gs://<AGENT_DOCS_BUCKET>/<AGENT_DOCS_PREFIX>/PAGO/proceso-pago.bpmn
gs://<AGENT_DOCS_BUCKET>/<AGENT_DOCS_PREFIX>/PAGO/reunion.md
```

1. **Configura `.env`:**
   ```
   AGENT_DOCS_SOURCE=gcs
   AGENT_DOCS_BUCKET=mi-bucket
   AGENT_DOCS_PREFIX=spb          # opcional; vacío = raíz del bucket
   GCP_PROJECT=mi-proyecto        # opcional
   ```
2. **Credenciales** (Application Default Credentials, solo lectura: rol `roles/storage.objectViewer` sobre el bucket):
   - Local: `gcloud auth application-default login`. En Docker, descomenta en `docker-compose.yml` el volumen `~/.config/gcloud:/root/.config/gcloud:ro`.
   - Servidor: una cuenta de servicio con `GOOGLE_APPLICATION_CREDENTIALS=/ruta/al/json` (monta el archivo como volumen o secreto).
3. **Sube los documentos:** `gcloud storage cp documents/PAGO/* gs://mi-bucket/spb/PAGO/`.
4. **Recrea el contenedor** (`docker compose up -d app`) y revisa `GET /api/v1/projects/PAGO/agents/documents`.

Las mismas reglas que la carpeta: solo los archivos directos de `<KEY>/` (las subcarpetas se ignoran), solo nombres sin rutas, 20 MB por archivo, formatos PDF/DOCX/BPMN/MD/TXT. Un bucket inaccesible responde `503` en la API; las pantallas siguen funcionando y muestran el error. Para volver a la carpeta: `AGENT_DOCS_SOURCE=folder` **y** vacía `AGENT_DOCS_BUCKET`, `AGENT_DOCS_PREFIX` y `GCP_PROJECT`.

#### Bucket con su propia organización (`AGENT_DOCS_MAP`)

Si el bucket ya tiene sus documentos organizados de otra forma, no hace falta copiarlos ni moverlos: `AGENT_DOCS_MAP` asocia una clave de proyecto a un prefijo del bucket, que puede ser una carpeta o el comienzo del nombre de archivos sueltos.

```
AGENT_DOCS_MAP=SINCHI=SERVI _ SINCHI __;DOCS=documentos/
```

| Clave | Lee |
|---|---|
| `SINCHI` | los archivos de la raíz cuyo nombre empieza por `SERVI _ SINCHI __` (p. ej. las «Notas de Gemini» de Meet en `.docx`) |
| `DOCS` | los archivos directos de `documentos/` |
| cualquier otra | `<AGENT_DOCS_PREFIX>/<KEY>/`, como siempre |

Las reglas no cambian: un proyecto solo ve los objetos de su prefijo, sin subcarpetas, y no puede leer los de otro.

#### Gemini en Vertex AI con las credenciales de GCP (sin API key)

En lugar de `GOOGLE_API_KEY`, el proveedor ADK puede usar Vertex AI con Application Default Credentials:

```
AGENT_PROVIDER=adk
AGENT_MODEL=gemini-2.5-flash
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=mi-proyecto-gcp
GOOGLE_CLOUD_LOCATION=us-central1
```

La identidad necesita `roles/aiplatform.user` en el proyecto (y `roles/storage.objectViewer` en el bucket). Sin credenciales, `/p/<KEY>/agents` muestra el proveedor como «no configurado» con el motivo.

Con Docker, `docker-compose.gcp.yml` monta el archivo de `gcloud auth application-default login` en modo lectura y usa su propio nombre de proyecto, para no mezclar la base con otra instalación:

```powershell
gcloud auth application-default login
docker compose -p spb-gcp -f docker-compose.yml -f docker-compose.gcp.yml up --build -d
docker compose -p spb-gcp exec app python scripts/seed.py
docker compose -p spb-gcp exec app python scripts/probar_gcp.py    # bucket + Gemini, de punta a punta
```

`scripts/probar_gcp.py` crea los proyectos de `AGENT_DOCS_MAP` que falten, lista sus documentos en el bucket y genera HU con Gemini leyendo esos documentos (quedan en `borrador`). Para parar: `docker compose -p spb-gcp down`.

Subir un archivo (`/generate-stories/upload`) o pegar texto (`document_text`) no son fuentes de documentos: el contenido se procesa en memoria y no se guarda en la carpeta ni en el bucket.

### 4.4 Generar desde todos los documentos (agente de documentos como AgentTool)

`POST /api/v1/projects/{key}/agents/generate-stories/from-documents` (o el botón **Generar HU desde todos los documentos** en `/p/<KEY>/agents`) usa dos agentes ADK:

```
generador (LlmAgent, output_schema = HU)
  └─ AgentTool(agente_documentos)            ← el generador le pregunta qué hay y qué dice cada documento
        └─ listar_documentos, leer_documento ← solo lectura, ligadas a la carpeta o bucket de ESE proyecto
```

- El agente de documentos no puede salir de la carpeta del proyecto: sus herramientas solo conocen esa fuente.
- La lectura total por ejecución está limitada por `AGENT_MAX_DOC_CHARS` (tramos de 15 000 caracteres).
- Cada HU guarda su documento de origen (`source_ref` = `documento · sección`). La clave de idempotencia es documento + título: repetir la ejecución, o generar desde ese mismo archivo, actualiza en vez de duplicar.
- `agent_runs.result.documents_read` lista los documentos leídos; los tokens incluyen los del agente de documentos.
- Con `AGENT_PROVIDER=stub` lee todos los documentos en orden, sin LLM.
- Con la Gemini API (AI Studio), ADK entrega la salida estructurada del generador mediante la herramienta interna `set_model_response`, porque combina herramientas y `output_schema`.

### 4.5 Activar Gemini (paso a paso)

El código está listo; los pasos con tu cuenta los haces tú:

1. **Clave de API.** Crea una API key en Google AI Studio (o en la consola de Google Cloud de tu cuenta empresarial, con acceso a la Gemini API). Guárdala solo en `.env`: no la pegues en chats, tickets ni commits. Si alguna vez la compartiste, bórrala y crea otra.
2. **Configura `.env`** (copia de `.env.example`):
   ```
   AGENT_PROVIDER=adk
   GOOGLE_API_KEY=tu-clave
   AGENT_MODEL=gemini-3.1-pro-preview
   ```
3. **Recrea el contenedor** para que lea el `.env` (un contenedor ya corriendo no recibe cambios de `.env`):
   ```bash
   docker compose up -d app
   ```
4. **Confirma qué modelos tiene tu cuenta** y ajusta `AGENT_MODEL` al que aparezca:
   ```bash
   docker compose exec app python scripts/try_agent.py --list-models
   ```
5. **Prueba directa**, sin pasar por la API. No guarda nada y gasta cuota de Gemini:
   ```bash
   docker compose exec app python scripts/try_agent.py proceso-pago.bpmn --project PAGO --refine-sample
   ```
6. **Guarda las HU** en el proyecto, con el mismo flujo que la API:
   ```bash
   docker compose exec app python scripts/try_agent.py proceso-pago.bpmn --project PAGO --save
   ```
   Las HU quedan en `borrador`. Reprocesar el mismo documento actualiza las HU existentes y no duplica.

Para probar sin Gemini usa `AGENT_PROVIDER=stub`: mismo flujo, con un proveedor determinista que convierte actividades de BPMN y viñetas en HU. Sirve para revisar el flujo sin gastar cuota.

Verificado: con `gemini-3.1-pro-preview` el comando de la prueba directa generó HU de un BPMN real (con tokens contados) y refinó una HU de muestra. Los tests cubren además el adaptador ADK completo (LlmAgent, Runner, sesión, `output_schema`, conteo de tokens) con un modelo falso que implementa `BaseLlm`, sin red.

### 4.6 Script `try_agent.py`

| Uso | Qué hace |
|---|---|
| `--list-models` | Lista los modelos que tu clave puede usar |
| `proceso-pago.bpmn --project PAGO` | Busca el archivo en `documents/PAGO/` y muestra las HU propuestas |
| `documents/PAGO/proceso-pago.bpmn --project PAGO` | También acepta la ruta con la carpeta del proyecto; solo se usa el nombre y se lee de la fuente configurada (carpeta **o** bucket) |
| `... --refine-sample` | Además refina una HU de ejemplo. No la guarda |
| `... --save` | Guarda las HU en el proyecto (en `borrador`). Requiere que el proyecto exista |

Dentro del contenedor, la carpeta de documentos está en `/data/documents`. El script resuelve los nombres a partir de `AGENT_DOCS_DIR`, así que no necesitas escribir esa ruta.

### 4.7 Problemas frecuentes

| Mensaje | Causa y solución |
|---|---|
| `no configuration file provided: not found` | Ejecutaste `docker compose` fuera de la carpeta del proyecto. Entra a la carpeta donde está `docker-compose.yml` |
| `Falta GOOGLE_API_KEY` | La clave no está en `.env`, o no recreaste el contenedor (`docker compose up -d app`) |
| `Agentes desactivados: define AGENT_PROVIDER=...` | `AGENT_PROVIDER` está en `none`. Ponlo en `adk` o `stub` |
| `404 NOT_FOUND ... no longer available to new users` | El modelo de `AGENT_MODEL` ya no está disponible para tu cuenta. Usa `--list-models` y elige otro |
| `400 INVALID_ARGUMENT` | Petición que el modelo rechaza. Las restricciones de longitud ya no se envían a Gemini; si vuelve a aparecer, revisa el modelo configurado |
| `No existe el documento X en la carpeta del proyecto` | El archivo no está en `documents/<CLAVE>/`. Revisa el nombre y la clave del proyecto |
| `El proyecto X no existe` | Con `--save`, crea el proyecto antes (ingesta o UI) |
| `503` en la API o en la UI | El proveedor no está configurado. Revisa `AGENT_PROVIDER` y `GOOGLE_API_KEY` |
| `422` al generar desde un PDF | El PDF no tiene texto (es un escaneo). Pásalo por OCR |
| La app no arranca: `AGENT_DOCS_SOURCE=gcs requiere AGENT_DOCS_BUCKET` | Define `AGENT_DOCS_BUCKET` o vuelve a `folder` |
| La app no arranca: `AGENT_DOCS_SOURCE=folder no usa ...` | Quedaron variables del bucket con `folder`. Vacíalas o usa `gcs` |
| `No se pudo conectar con Cloud Storage` / `No se pudo listar gs://...` | Faltan credenciales (ADC) o la identidad no tiene `roles/storage.objectViewer` sobre el bucket |
| `No hay documentos en ...` | La carpeta o el prefijo `<KEY>/` del bucket está vacío. Revisa `AGENT_DOCS_PREFIX` y la clave del proyecto |

---

## 5. Configuración

Variables (`.env` o entorno del contenedor). Pasos completos por modo: sección 1. En Docker, `docker-compose.yml` fija
`DATABASE_URL` (`db:5432`) y `AGENT_DOCS_DIR` (`/data/documents`) para el contenedor y esos valores ganan sobre `.env`.

| Variable | Por defecto | Uso |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://spb:spb@localhost:5432/spb` | Conexión a Postgres. En Docker la app usa `db:5432` |
| `SESSION_SECRET` | `cambiar-en-produccion` | Firma de la cookie de sesión. Cámbiala |
| `ADK_WEBHOOK_URL` | vacío | Si se define, recibe un `POST` al crear cada solicitud de refinamiento |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | `admin@spb.local` / `cambiar-me-ya` | Solo para `scripts/seed.py` |
| `AGENT_PROVIDER` | `none` | `none` (desactivado), `stub` (sin LLM) o `adk` (Gemini) |
| `GOOGLE_API_KEY` | vacío | Clave de Gemini API. Solo en `.env`, nunca en el repo |
| `AGENT_MODEL` | `gemini-3.1-pro-preview` | Id del modelo Gemini (confirmar con `--list-models`) |
| `AGENT_EXECUTION` | `background` | `background` (segundo plano) o `inline` (espera el resultado) |
| `AGENT_AUTO_REFINE` | `false` | `true`: al pasar una HU a `en_refinamiento`, el agente interno la refina |
| `AGENT_MAX_DOC_CHARS` | `200000` | Tamaño máximo del documento en caracteres (`413` si se excede) |
| `AGENT_DOCS_SOURCE` | `folder` | Fuente única de documentos: `folder` (carpeta `AGENT_DOCS_DIR`) **o** `gcs` (bucket). Ver sección 4.3 |
| `AGENT_DOCS_DIR` | `documents` | Solo `folder`. Carpeta raíz de documentos por proyecto (en Docker: `/data/documents`) |
| `AGENT_DOCS_BUCKET` / `AGENT_DOCS_PREFIX` | vacío | Solo `gcs` (bucket obligatorio). `gs://BUCKET/PREFIX/<KEY>/`. Con `folder` deben quedar vacías |
| `GCP_PROJECT` | vacío | Solo `gcs`, opcional. Proyecto de GCP del cliente de Storage |
| `GOOGLE_APPLICATION_CREDENTIALS` | vacío | Solo `gcs` con cuenta de servicio: ruta al JSON **dentro del contenedor**. Sin Docker, expórtala en el shell (sección 1.5). Con `gcloud auth application-default login` no hace falta |
| `AGENT_PRICE_IN_PER_1M` / `AGENT_PRICE_OUT_PER_1M` | vacío | Precio por millón de tokens; si no se definen, `cost_usd` queda vacío |

---

## 6. Tests

```bash
.venv/bin/python -m pytest                       # SQLite temporal
TEST_DATABASE_URL=postgresql+psycopg://spb:spb@localhost:5433/spb_test .venv/bin/python -m pytest
```

Para un caso: `pytest tests/test_api.py::test_refinement_cycle`.
Con Postgres, crea antes la base `spb_test` (`docker compose exec db createdb -U spb spb_test`). Los tests borran sus datos después de cada caso; no uses la base de desarrollo.

Cubren: grafo de contexto (validación, ciclo borrador → aprobado, permisos), tamaño no funcional y violaciones (bloqueo de la etapa 3, excepciones, corrección de la HU), puntos de función (matrices IFPUG, reuso de entidades, corrección humana), modelo FinOps (cálculo a mano, impuesto de contexto, escenarios, ventana, precios), agentes FPA en secuencia con un modelo falso, fuente de documentos en bucket (cliente falso, sin red), agente de documentos como AgentTool (modelo falso), ingesta idempotente, ciclo de refinamiento con control de versión, permisos por rol, validación HITL, asignación por correo desde ADK, reportes y exportación, y las pantallas principales.

---

## 7. Estructura

```
app/
  main.py            app FastAPI, sesiones, errores (JSON en /api, HTML en la web)
  models.py          modelo de datos (SQLAlchemy 2)
  schemas.py         contratos de entrada y salida
  services/          reglas de negocio: workflow, stories, refinement, projects, access, audit,
                     fpa (puntos de función), finops + finops_pricing (costo de generación autónoma),
                     context_graph + context (grafo de contexto y su ciclo HITL), nfr (tamaño no funcional)
  services/documents/  lectura de PDF, BPMN, MD y TXT desde carpetas o un bucket, por proyecto
  routers/           API JSON (auth, admin, projects, stories, adk, agents, finops, context) y web (ui.py)
  agents/            agentes IA: puertos, runner, proveedores stub y adk (ver sección 4)
  templates/         Jinja2 (UI)
  static/            app.css (tema claro/oscuro) y app.js (tema, toasts, Kanban)
alembic/             migraciones
documents/           carpetas de documentos por proyecto (PAGO/ tiene ejemplos)
scripts/seed.py      datos de demo
scripts/try_agent.py prueba directa de Gemini sin la API
tests/               pytest (API y UI)
```

---

## 8. Limitaciones del MVP

- Sin transcripción de audio: las reuniones se entregan como texto (MD/TXT) o PDF con texto.
- Gemini: la integración está probada con un modelo falso; la llamada real requiere tu `GOOGLE_API_KEY`.
- PDF escaneados (sin texto) no se procesan: falta OCR.
- El agente interno corre dentro del mismo proceso (en `background`). Si la app se reinicia a mitad de una ejecución, esa ejecución queda en `running`; un worker separado es el siguiente paso si hace falta.
- La priorización es un campo enviado por ADK o manual; no hay cálculo automático.
- El dimensionamiento FinOps usa supuestos no calibrados (ver `docs/metodo-fpa-finops.md`): sus cifras son orden de magnitud. El agente FPA usa prompts al estilo FPA-EX, sin el ajuste fino del artículo.
- El tamaño no funcional (`nfr_snap_lite_v1`) es un puntaje por severidad inspirado en SNAP, no SNAP. El grafo de contexto solo conoce las restricciones que están en los documentos o que una persona agrega; una violación sutil puede pasar inadvertida al agente, por eso la etapa 3 la aprueba una persona.
- No hay exportación directa a Jira o Trello; el CSV/JSON sirve de puente.
- Una contraseña enviada en `users[]` se guarda sólo si ese usuario es nuevo; no se cambia en ingestas posteriores. Para cambiarla hace falta una pantalla o endpoint de administración que aún no existe.
- En desarrollo la app crea las tablas al arrancar (`create_all`). En despliegue usa `alembic upgrade head`.
- Como el proyecto no está en producción, el cambio de story points a puntos de función y la tabla del grafo de contexto (`context_graphs`, más `nfr_points` y `context_analysis` en las HU) se hicieron **en el esquema inicial**, sin migración nueva. Una base creada antes de ese cambio se recrea: `docker compose down -v`, `docker compose up --build -d` y `docker compose exec app python scripts/seed.py`.
- Los estilos son CSS propio (sin framework).
