from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ApiKey, User, utcnow
from app.security import hash_api_key
from app.services.access import Principal


# Registra el esquema en OpenAPI: en /docs aparece el botón "Authorize" para la API key de servicio.
api_key_scheme = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="API key de servicio (agentes ADK), creada en /admin"
)


def get_principal(
    request: Request,
    db: Session = Depends(get_db),
    x_api_key: str | None = Security(api_key_scheme),
) -> Principal:
    if x_api_key:
        key = db.scalar(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(x_api_key), ApiKey.is_active.is_(True))
        )
        if key is None:
            raise HTTPException(status_code=401, detail="API key inválida o revocada")
        key.last_used_at = utcnow()
        db.commit()
        return Principal(service_name=key.name)

    user_id = request.session.get("user_id")
    if user_id:
        user = db.get(User, user_id)
        if user and user.is_active:
            return Principal(user=user)
    raise HTTPException(status_code=401, detail="No autenticado")


def require_user(principal: Principal = Depends(get_principal)) -> User:
    if principal.is_service:
        raise HTTPException(status_code=403, detail="Esta operación requiere un usuario humano")
    return principal.user


def require_service(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_service:
        raise HTTPException(status_code=403, detail="Esta operación requiere una API key de servicio")
    return principal


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Requiere rol de administrador")
    return user
