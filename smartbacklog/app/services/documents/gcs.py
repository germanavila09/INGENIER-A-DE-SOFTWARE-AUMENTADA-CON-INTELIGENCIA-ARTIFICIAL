"""Documentos en un bucket de Cloud Storage: gs://<bucket>/<prefijo>/<KEY>/<archivo>.

Misma interfaz que FolderSource (list, resolve, read_bytes, location) y las mismas reglas:
un proyecto solo ve los archivos directos de su carpeta, sin subcarpetas ni rutas.

Con AGENT_DOCS_MAP, una clave puede apuntar a un prefijo cualquiera del bucket: una carpeta
(«documentos/») o el comienzo del nombre de archivos sueltos («SERVI _ SINCHI __»). Sirve para
buckets que ya tienen su propia organización, sin copiar ni mover nada.

Solo lectura. La autenticación usa Application Default Credentials:
    gcloud auth application-default login
o una cuenta de servicio con GOOGLE_APPLICATION_CREDENTIALS. La identidad necesita
al menos roles/storage.objectViewer sobre el bucket.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from app.services.documents.folder import MAX_FILE_BYTES, DocumentInfo
from app.services.documents.loaders import SUPPORTED_EXTENSIONS
from app.services.errors import NotFound, ServiceUnavailable, Unprocessable


def normalize_prefix(prefix: str | None) -> str:
    prefix = (prefix or "").strip().strip("/")
    return f"{prefix}/" if prefix else ""


def _client(project: str | None):
    # Se importa aquí: sin el paquete o sin credenciales el backend arranca igual.
    try:
        from google.cloud import storage
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ServiceUnavailable("google-cloud-storage no está instalado (revisa requirements.txt)") from exc
    try:
        return storage.Client(project=project)
    except Exception as exc:  # noqa: BLE001 - p. ej. DefaultCredentialsError
        raise ServiceUnavailable(f"No se pudo conectar con Cloud Storage: {exc}") from exc


class GcsSource:
    kind = "gcs"

    def __init__(self, bucket: str, prefix: str | None, project_key: str, client=None, gcp_project: str | None = None,
                 mapped_prefix: str | None = None):
        if not bucket:
            raise ServiceUnavailable("Falta AGENT_DOCS_BUCKET para AGENT_DOCS_SOURCE=gcs")
        # La clave ya viene validada; esto es defensa extra para que no salga de su carpeta.
        if not project_key or "/" in project_key or project_key in (".", ".."):
            raise Unprocessable("Clave de proyecto inválida para el bucket de documentos")
        self.bucket_name = bucket
        if mapped_prefix:
            if ".." in mapped_prefix or mapped_prefix.startswith("/"):
                raise Unprocessable("Prefijo de AGENT_DOCS_MAP inválido")
            # Carpeta («documentos/») o comienzo del nombre de archivos sueltos («SERVI _ SINCHI __»).
            self.project_prefix = mapped_prefix
        else:
            self.project_prefix = f"{normalize_prefix(prefix)}{project_key}/"
        # Los nombres que ve el proyecto son relativos a la carpeta que contiene el prefijo.
        self._base = self.project_prefix[: self.project_prefix.rfind("/") + 1]
        self._client = client or _client(gcp_project)
        self._bucket = self._client.bucket(bucket)

    @property
    def location(self) -> str:
        return f"gs://{self.bucket_name}/{self.project_prefix}"

    def list(self) -> list[DocumentInfo]:
        try:
            # delimiter="/" deja fuera las subcarpetas: igual que la carpeta local.
            blobs = list(self._client.list_blobs(self.bucket_name, prefix=self.project_prefix, delimiter="/"))
        except Exception as exc:  # noqa: BLE001
            raise ServiceUnavailable(f"No se pudo listar {self.location}: {exc}") from exc
        items = []
        for blob in sorted(blobs, key=lambda b: b.name):
            name = blob.name[len(self._base):]
            if not name or "/" in name or PurePosixPath(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            items.append(DocumentInfo(name=name, size=blob.size or 0))
        return items

    def resolve(self, filename: str):
        if not filename or PurePosixPath(filename).name != filename or "\\" in filename or "\x00" in filename \
                or filename in (".", ".."):
            raise Unprocessable("Nombre de archivo inválido (solo el nombre, sin rutas)")
        if PurePosixPath(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise Unprocessable(f"Formato no soportado: {PurePosixPath(filename).suffix}")
        object_name = self._base + filename
        if not object_name.startswith(self.project_prefix):
            raise NotFound(f"No existe el documento {filename} en {self.location}")
        try:
            blob = self._bucket.get_blob(object_name)
        except Exception as exc:  # noqa: BLE001
            raise ServiceUnavailable(f"No se pudo consultar {self.location}{filename}: {exc}") from exc
        if blob is None:
            raise NotFound(f"No existe el documento {filename} en {self.location}")
        if (blob.size or 0) > MAX_FILE_BYTES:
            raise Unprocessable("El archivo supera 20 MB")
        return blob

    def read_bytes(self, filename: str) -> bytes:
        blob = self.resolve(filename)
        try:
            return blob.download_as_bytes()
        except Exception as exc:  # noqa: BLE001
            raise ServiceUnavailable(f"No se pudo leer {self.location}{filename}: {exc}") from exc
