from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import models  # noqa: F401  (registra modelos en Base.metadata)
from app.config import settings
from app.db import Base, engine
from app.routers import adk, admin, agents, auth, context, finops, hitl, projects, stories, ui
from app.routers.ui import LoginRequired, render
from app.services.errors import AppError

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # MVP: crea tablas si no existen. Para cambios de esquema usar Alembic.
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Smart Product Backlog API",
    version="0.1.0",
    lifespan=lifespan,
    description=(
        "API del MVP. Humanos: POST /api/v1/auth/login (cookie de sesión). "
        "Agentes ADK: botón Authorize con la API key (header X-API-Key). "
        "Flujo ADK: ingest/projects → stories:bulk → refinements (list, claim, result)."
    ),
)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax", max_age=60 * 60 * 12,
                   https_only=settings.session_https_only)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(projects.router)
app.include_router(stories.router)
app.include_router(adk.router)
app.include_router(agents.router)
app.include_router(hitl.router)
app.include_router(finops.router)
app.include_router(context.router)
app.include_router(ui.router)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)
    response = render(request, "error.html", None, message=exc.message, code=exc.status_code)
    response.status_code = exc.status_code
    return response


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "No encontrado"}, status_code=404)
    response = render(request, "error.html", None, message="La página no existe", code=404)
    response.status_code = 404
    return response


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
