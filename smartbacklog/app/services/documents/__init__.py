from app.config import settings
from app.services.documents.folder import FolderSource
from app.services.documents.gcs import GcsSource
from app.services.documents.loaders import SUPPORTED_EXTENSIONS, load_document_text
from app.services.errors import ServiceUnavailable

DocumentSource = FolderSource | GcsSource
SOURCE_LABELS = {"folder": "Carpeta local", "gcs": "Bucket de Cloud Storage"}


def get_document_source(project_key: str, client=None) -> DocumentSource:
    """La única fuente de documentos del proyecto, según AGENT_DOCS_SOURCE: folder (por defecto) O gcs.

    Nunca se combinan: con gcs no se lee la carpeta local, y con folder no se consulta el bucket.
    """
    kind = settings.agent_docs_source
    if kind == "folder":
        return FolderSource(settings.agent_docs_dir_path, project_key)
    if kind == "gcs":
        return GcsSource(
            settings.agent_docs_bucket or "", settings.agent_docs_prefix, project_key,
            client=client, gcp_project=settings.gcp_project,
            mapped_prefix=settings.docs_map.get(project_key.upper()),
        )
    raise ServiceUnavailable(f"AGENT_DOCS_SOURCE desconocido: {settings.agent_docs_source} (usa folder o gcs)")


__all__ = [
    "DocumentSource",
    "FolderSource",
    "GcsSource",
    "SOURCE_LABELS",
    "SUPPORTED_EXTENSIONS",
    "get_document_source",
    "load_document_text",
]
