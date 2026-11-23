from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://spb:spb@localhost:5432/spb"
    session_secret: str = "cambiar-en-produccion"
    session_https_only: bool = False  # true en Cloud Run: la cookie de sesión solo viaja por HTTPS
    adk_webhook_url: str | None = None

    # Agentes (ver app/agents). provider: none | stub | adk
    agent_provider: str = "none"
    agent_model: str = "gemini-3.1-pro-preview"
    agent_execution: str = "background"  # background | inline
    agent_auto_refine: bool = False
    # HITL: umbrales de confianza de la revisión multiagente y revisión automática.
    hitl_confidence_auto: float = 0.85
    hitl_confidence_review: float = 0.60
    hitl_review_after_refine: bool = True  # tras «pedir cambios», el agente refina y la HU se revisa de nuevo
    hitl_review_generated: bool = False  # revisar automáticamente las HU que genera el agente (más llamadas al LLM)
    agent_max_doc_chars: int = 200_000
    # Fuente de documentos, excluyente: folder (agent_docs_dir/<KEY>/) O gcs (gs://bucket/prefix/<KEY>/).
    agent_docs_source: Literal["folder", "gcs"] = "folder"
    agent_docs_dir: str = "documents"  # solo folder
    agent_docs_bucket: str | None = None  # solo gcs
    agent_docs_prefix: str = ""  # solo gcs
    gcp_project: str | None = None  # solo gcs
    # Proyecto SPB → prefijo del bucket, para buckets que ya tienen su propia organización.
    # "SINCHI=SERVI _ SINCHI __;DOCS=documentos/": la clave SINCHI lee los objetos cuyo nombre empieza
    # por "SERVI _ SINCHI __" y DOCS los de la carpeta documentos/. Las claves sin entrada usan
    # AGENT_DOCS_PREFIX/<KEY>/ como siempre. Solo gcs.
    agent_docs_map: str = ""
    google_api_key: str | None = None
    # Gemini en Vertex AI con Application Default Credentials (en lugar de GOOGLE_API_KEY).
    # Mismas variables que lee google-genai: GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION.
    google_genai_use_vertexai: bool = False
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    agent_price_in_per_1m: float | None = None
    agent_price_out_per_1m: float | None = None


    @field_validator("agent_docs_source", mode="before")
    @classmethod
    def _normalize_source(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    # Una variable vacía en .env (p. ej. AGENT_PRICE_IN_PER_1M=) significa «sin definir», no "".
    @field_validator(
        "agent_docs_bucket", "gcp_project", "agent_price_in_per_1m", "agent_price_out_per_1m", "google_cloud_project",
        mode="before"
    )
    @classmethod
    def _blank_is_none(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def _one_document_source(self):
        """Carpeta O bucket: una configuración que mezcla ambas no arranca."""
        if self.agent_docs_source == "gcs" and not self.agent_docs_bucket:
            raise ValueError("AGENT_DOCS_SOURCE=gcs requiere AGENT_DOCS_BUCKET")
        if self.agent_docs_source == "folder":
            mixed = [name for name, value in (
                ("AGENT_DOCS_BUCKET", self.agent_docs_bucket),
                ("AGENT_DOCS_PREFIX", self.agent_docs_prefix.strip()),
                ("GCP_PROJECT", self.gcp_project),
                ("AGENT_DOCS_MAP", self.agent_docs_map.strip()),
            ) if value]
            if mixed:
                raise ValueError(
                    f"AGENT_DOCS_SOURCE=folder no usa {', '.join(mixed)}: vacíalas, o usa AGENT_DOCS_SOURCE=gcs"
                )
        self.docs_map  # valida el formato de AGENT_DOCS_MAP al arrancar
        return self

    @property
    def docs_map(self) -> dict[str, str]:
        """AGENT_DOCS_MAP como {CLAVE: prefijo}. Formato: CLAVE=prefijo separados por ';'."""
        out: dict[str, str] = {}
        for item in self.agent_docs_map.split(";"):
            if not item.strip():
                continue
            key, sep, prefix = item.partition("=")
            key = key.strip().upper()
            if not sep or not key or not prefix.strip() or ".." in prefix:
                raise ValueError(f"AGENT_DOCS_MAP inválido en {item.strip()!r}: usa CLAVE=prefijo;OTRA=carpeta/")
            out[key] = prefix.strip().lstrip("/")
        return out

    @property
    def agent_docs_dir_path(self) -> Path:
        return Path(self.agent_docs_dir).expanduser().resolve()


settings = Settings()
