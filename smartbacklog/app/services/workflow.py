from dataclasses import dataclass

from app.services.errors import Forbidden, Unprocessable

STATUS_LABELS = {
    "borrador": "Borrador",
    "en_refinamiento": "En refinamiento",
    "refinada": "Refinada",
    "lista": "Lista",
    "en_progreso": "En progreso",
    "en_revision": "En revisión",
    "hecha": "Hecha",
    "descartada": "Descartada",
}

TRANSITIONS: dict[str, set[str]] = {
    "borrador": {"en_refinamiento", "descartada"},
    "en_refinamiento": {"refinada", "borrador", "descartada"},
    "refinada": {"lista", "en_refinamiento", "descartada"},
    "lista": {"en_progreso", "refinada", "descartada"},
    "en_progreso": {"en_revision", "lista"},
    "en_revision": {"hecha", "en_progreso"},
    "hecha": set(),
    "descartada": {"borrador"},
}

WRITE_ROLES = {"product_owner", "scrum_master"}
# Transiciones de ejecución que puede hacer un developer además de los roles de escritura.
DEVELOPER_TRANSITIONS = {
    ("lista", "en_progreso"),
    ("en_progreso", "en_revision"),
    ("en_progreso", "lista"),
    ("en_revision", "en_progreso"),
}


@dataclass
class Actor:
    kind: str  # "user" | "service"
    name: str
    user_id: int | None = None
    is_admin: bool = False
    project_role: str | None = None

    @property
    def is_service(self) -> bool:
        return self.kind == "service"


def check_transition(from_status: str, to_status: str, actor: Actor) -> None:
    if to_status not in TRANSITIONS.get(from_status, set()):
        raise Unprocessable(
            f"Transición no permitida: {from_status} → {to_status}"
        )
    if actor.is_service or actor.is_admin or actor.project_role in WRITE_ROLES:
        return
    if actor.project_role == "developer" and (from_status, to_status) in DEVELOPER_TRANSITIONS:
        return
    raise Forbidden(f"Tu rol no puede mover la HU de {from_status} a {to_status}")
