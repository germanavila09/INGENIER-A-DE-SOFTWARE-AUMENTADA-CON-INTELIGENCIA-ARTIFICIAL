"""Crea admin, usuarios demo, proyecto SPB con épicas e HU de ejemplo.

Uso (con la base ya migrada/creada):
    python scripts/seed.py
Variables: SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD (por defecto admin@spb.local / cambiar-me-ya),
SEED_DEMO (true/false) y SEED_DEMO_PASSWORD (clave de los usuarios demo; por defecto demo-spb-2026).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Project, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import projects as project_service  # noqa: E402
from app.services.stories import upsert_story  # noqa: E402
from app.services.workflow import Actor  # noqa: E402

DEMO_USERS = [
    ("po@spb.local", "Paula Product Owner", "product_owner"),
    ("sm@spb.local", "Samuel Scrum", "scrum_master"),
    ("dev@spb.local", "Diana Developer", "developer"),
    ("stake@spb.local", "Esteban Stakeholder", "stakeholder"),
]

DEMO_STORIES = [
    ("Gestión de backlog", {
        "title": "Subir transcripción de reunión",
        "role": "Analista de producto", "need": "subir la transcripción de la reunión con stakeholders",
        "benefit": "generar HU a partir de lo conversado",
        "acceptance_criteria": "- [ ] Acepta archivos TXT y DOCX\n- [ ] Muestra vista previa del texto",
        "type": "HU_TRADICIONAL", "type_justification": "Carga de archivos sin componentes de IA",
        "priority_rank": 1, "source_ref": "Reunión 2026-09-12, min 4:10",
        "fp_components": [
            {"name": "Transcripción", "type": "ILF", "det": 6, "ret": 1},
            {"name": "Subir transcripción", "type": "EI", "det": 5, "ftr": 1},
            {"name": "Vista previa del texto", "type": "EQ", "det": 3, "ftr": 1},
        ],
    }),
    ("Gestión de backlog", {
        "title": "Generar HU desde transcripción",
        "role": "Product owner", "need": "que un agente proponga HU a partir de la transcripción",
        "benefit": "reducir tiempo de redacción",
        "acceptance_criteria": "- [ ] Cada HU incluye fuente\n- [ ] Formato rol/necesidad/beneficio",
        "type": "HU_IA", "type_justification": "Requiere LLM para extracción de requisitos",
        "priority_rank": 2, "source_ref": "Reunión 2026-09-12, min 4:10",
        "fp_components": [
            {"name": "Historia de usuario", "type": "ILF", "det": 12, "ret": 1},
            {"name": "Servicio LLM", "type": "EIF", "det": 4, "ret": 1},
            {"name": "Generar HU desde transcripción", "type": "EI", "det": 6, "ftr": 3},
        ],
    }),
    ("Clasificación", {
        "title": "Clasificar HU como IA o tradicional",
        "role": "Product owner", "need": "saber qué HU requieren componentes de IA",
        "benefit": "estimar cada tipo con su método",
        "acceptance_criteria": "- [ ] Cada HU tiene etiqueta y justificación",
        "type": "HU_IA", "type_justification": "Clasificación asistida por LLM",
        "priority_rank": 3,
        # Historia de usuario y Servicio LLM ya los contó la HU anterior: suman 0 (reused).
        "fp_components": [
            {"name": "Historia de usuario", "type": "ILF", "det": 12, "ret": 1, "reused": True},
            {"name": "Servicio LLM", "type": "EIF", "det": 4, "ret": 1, "reused": True},
            {"name": "Clasificar HU", "type": "EI", "det": 4, "ftr": 2},
        ],
    }),
]


def main() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        admin_email = os.getenv("SEED_ADMIN_EMAIL", "admin@spb.local")
        admin_pass = os.getenv("SEED_ADMIN_PASSWORD", "cambiar-me-ya")
        if db.scalar(select(User).where(User.email == admin_email)) is None:
            db.add(User(email=admin_email, name="Administrador", is_admin=True, password_hash=hash_password(admin_pass)))
            print(f"Admin creado: {admin_email}")

        # En un servidor público: SEED_DEMO=false (sin usuarios ni proyecto demo) o SEED_DEMO_PASSWORD propia.
        if os.getenv("SEED_DEMO", "true").strip().lower() in ("0", "false", "no"):
            db.commit()
            print("Datos demo omitidos (SEED_DEMO=false)")
            return
        demo_pass = os.getenv("SEED_DEMO_PASSWORD", "demo-spb-2026")
        users = {}
        for email, name, _ in DEMO_USERS:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, name=name, password_hash=hash_password(demo_pass))
                db.add(user)
                db.flush()
            users[email] = user

        project = db.scalar(select(Project).where(Project.key == "SPB"))
        if project is None:
            project = Project(key="SPB", name="Smart Product Backlog", description="Proyecto demo del curso")
            db.add(project)
            db.flush()
            for email, _, role in DEMO_USERS:
                project_service.add_member(db, project, users[email], role)
            actor = Actor(kind="user", name="seed", is_admin=True, project_role="product_owner")
            for epic_title, data in DEMO_STORIES:
                upsert_story(db, project, {**data, "epic": epic_title}, actor)
            db.commit()
            print("Proyecto SPB creado con HU de ejemplo")
        else:
            print("Proyecto SPB ya existe; sin cambios")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
