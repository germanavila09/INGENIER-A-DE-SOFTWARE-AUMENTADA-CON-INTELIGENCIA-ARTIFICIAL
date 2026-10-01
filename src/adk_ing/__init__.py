"""Cliente del bucket gs://adk_ing (proyecto servi-modelos-ia-dev)."""

from .bucket import BucketReader, ObjectInfo
from .config import Settings, get_settings

__all__ = ["BucketReader", "ObjectInfo", "Settings", "get_settings"]
