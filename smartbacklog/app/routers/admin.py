from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.models import ApiKey, User
from app.schemas import ApiKeyIn, UserIn, user_out
from app.security import generate_api_key, hash_api_key, hash_password
from app.services.errors import Conflict, NotFound

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.email))]


@router.post("/users", status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise Conflict("Ya existe un usuario con ese email")
    user = User(
        email=body.email.lower(),
        name=body.name,
        is_admin=body.is_admin,
        password_hash=hash_password(body.password) if body.password else None,
    )
    db.add(user)
    db.commit()
    return user_out(user)


@router.get("/api-keys")
def list_keys(db: Session = Depends(get_db)):
    return [
        {"id": k.id, "name": k.name, "is_active": k.is_active, "last_used_at": k.last_used_at}
        for k in db.scalars(select(ApiKey).order_by(ApiKey.id))
    ]


@router.post("/api-keys", status_code=201)
def create_key(body: ApiKeyIn, db: Session = Depends(get_db)):
    if db.scalar(select(ApiKey).where(ApiKey.name == body.name)):
        raise Conflict("Ya existe una API key con ese nombre")
    raw = generate_api_key()
    db.add(ApiKey(name=body.name, key_hash=hash_api_key(raw)))
    db.commit()
    # La clave en claro sólo se devuelve esta vez.
    return {"name": body.name, "api_key": raw}


@router.post("/api-keys/{key_id}/revoke")
def revoke_key(key_id: int, db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    if key is None:
        raise NotFound("API key no existe")
    key.is_active = False
    db.commit()
    return {"ok": True}
