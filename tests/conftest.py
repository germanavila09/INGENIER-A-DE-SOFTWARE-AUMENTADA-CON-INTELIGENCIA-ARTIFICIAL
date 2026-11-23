import os
import tempfile
from pathlib import Path

# Debe ir antes de importar la app: la configuración se lee al importar.
_db_file = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{_db_file}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ApiKey, User  # noqa: E402
from app.security import generate_api_key, hash_api_key, hash_password  # noqa: E402

PASSWORD = "clave-segura-123"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    # drop_all evita esquemas viejos en bases de prueba reutilizadas (create_all no altera tablas existentes).
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def make_user(db):
    def _make(email: str, name: str | None = None, admin: bool = False, password: str | None = PASSWORD):
        user = User(
            email=email,
            name=name or email.split("@")[0],
            is_admin=admin,
            password_hash=hash_password(password) if password else None,
        )
        db.add(user)
        db.commit()
        return user

    return _make


@pytest.fixture
def service_key(db):
    raw = generate_api_key()
    db.add(ApiKey(name="adk-test", key_hash=hash_api_key(raw)))
    db.commit()
    return raw


def login(client: TestClient, email: str, password: str = PASSWORD) -> None:
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text


INGEST_PAYLOAD = {
    "key": "DEMO",
    "name": "Demo",
    "users": [
        {"email": "po@demo.co", "name": "Paula PO", "password": PASSWORD},
        {"email": "dev@demo.co", "name": "Diego Dev", "password": PASSWORD},
        {"email": "stake@demo.co", "name": "Sara Stake", "password": PASSWORD},
    ],
    "members": [
        {"email": "po@demo.co", "role": "product_owner"},
        {"email": "dev@demo.co", "role": "developer"},
        {"email": "stake@demo.co", "role": "stakeholder"},
    ],
    "epics": [
        {
            "title": "Autenticación",
            "stories": [
                {
                    "external_id": "hu-1",
                    "title": "Iniciar sesión",
                    "role": "usuario",
                    "need": "entrar con correo",
                    "benefit": "acceder a mis proyectos",
                    "acceptance_criteria": "- [ ] Muestra error con credenciales inválidas",
                    "type": "HU_TRADICIONAL",
                    "source_ref": "Reunión 1, min 3",
                }
            ],
        }
    ],
    "stories": [{"external_id": "hu-2", "title": "Clasificar HU por IA", "type": "HU_IA"}],
}


def hitl_approve_all(client: TestClient, key: str = "DEMO") -> None:
    """Revisa con el agente stub las HU sin revisión y las aprueba con la sesión actual (debe ser product owner).

    La etapa 1 exige que cada HU activa tenga su revisión HITL aprobada (ver services/hitl.py).
    """
    from app.config import settings

    saved = settings.agent_provider, settings.agent_execution
    settings.agent_provider, settings.agent_execution = "stub", "inline"
    try:
        client.post(f"/api/v1/projects/{key}/hitl/review-pending")
        for item in client.get(f"/api/v1/projects/{key}/hitl").json()["decidir"]:
            res = client.post(f"/api/v1/projects/{key}/stories/{item['code']}/hitl/decision", json={"decision": "aprobar"})
            assert res.status_code == 200, res.text
    finally:
        settings.agent_provider, settings.agent_execution = saved
