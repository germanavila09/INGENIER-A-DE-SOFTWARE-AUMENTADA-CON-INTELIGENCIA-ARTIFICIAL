# Ruta de historias (orquestador HU + API + front) para Cloud Run.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY deploy/constraints.txt deploy/constraints.txt
COPY src ./src
RUN pip install -c deploy/constraints.txt .

COPY adk_web.py ./
COPY agents ./agents
COPY web ./web

# Sesiones ADK en memoria: el contenedor no tiene disco persistente. Los resultados van a GCS (HU_RESULTS_URI).
ENV ADK_SESSION_URI=memory:// \
    GOOGLE_GENAI_USE_VERTEXAI=TRUE \
    GOOGLE_CLOUD_LOCATION=us-central1 \
    PORT=8080

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8080
CMD ["sh", "-c", "exec python adk_web.py --host 0.0.0.0 --port ${PORT} --no-browser --skip-checks"]
