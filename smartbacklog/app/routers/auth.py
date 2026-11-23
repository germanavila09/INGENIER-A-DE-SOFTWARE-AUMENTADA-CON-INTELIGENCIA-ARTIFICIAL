from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.models import User
from app.schemas import user_out
from app.security import verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user and user.is_active and verify_password(password, user.password_hash):
        return user
    return None


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    request.session["user_id"] = user.id
    return user_out(user)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(require_user)):
    return user_out(user)
