"""FinOps del backlog: tokens, costo, impuesto de contexto y tiempo de generación autónoma. Sin LLM.

El cálculo es puro (services/finops.py) y se hace al vuelo con los puntos de función guardados, así
que cambiar de modelo o de supuestos no requiere volver a ejecutar agentes.
"""

from __future__ import annotations

import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_principal
from app.models import Project
from app.services import finops, finops_pricing
from app.services.access import Principal, load_project, require_member
from app.services.errors import Unprocessable

router = APIRouter(prefix="/api/v1", tags=["finops"])

EXPORT_COLUMNS = [
    "order", "code", "title", "type", "status", "function_points", "nfr_points", "context_before_tokens", "context_read_tokens",
    "iterations", "input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "cost_usd",
    "context_tax_usd", "hours", "exceeds_window",
]


def project_finops(project: Project, params: finops.FinopsParams | None = None) -> dict:
    stories = [
        finops.StoryInput(
            code=s.code, title=s.title, type=s.type, status=s.status,
            function_points=s.function_points, priority_rank=s.priority_rank, id=s.id, nfr_points=s.nfr_points,
        )
        for s in project.stories
    ]
    try:
        result = finops.estimate(stories, params)
    except finops.FinopsError as exc:
        raise Unprocessable(str(exc)) from exc
    return {"project": project.key, **result}


def finops_params(params: Annotated[finops.FinopsParams, Query()]) -> finops.FinopsParams:
    """Supuestos del modelo como parámetros de consulta. Va en una dependencia porque FastAPI solo expande un
    modelo de consulta cuando es el único parámetro de consulta de la función (el export además tiene `format`)."""
    return params


@router.get("/finops/models")
def finops_models(principal: Principal = Depends(get_principal)):
    """Tabla de precios (USD por 1M tokens) con fecha y fuente."""
    return finops_pricing.catalog()


@router.get("/projects/{key}/finops")
def get_finops(
    key: str,
    params: finops.FinopsParams = Depends(finops_params),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """Por HU (en orden de generación) y total. Cualquier supuesto se puede sobrescribir como parámetro."""
    project = load_project(db, key)
    require_member(db, project, principal)
    return project_finops(project, params)


@router.get("/projects/{key}/finops/export")
def export_finops(
    key: str,
    params: finops.FinopsParams = Depends(finops_params),
    format: str = Query("csv", pattern="^(json|csv)$"),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    project = load_project(db, key)
    require_member(db, project, principal)
    result = project_finops(project, params)
    if format == "json":
        return result
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(result["stories"])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{key}-finops-{params.model}-{params.scenario}.csv"'},
    )
