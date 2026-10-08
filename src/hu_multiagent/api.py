"""API REST del Smart Product Backlog (SPB) sobre el motor de historias.

La consume el front ``web/spb/index.html`` y la monta ``adk_web.py`` bajo ``/api``.
Usa el mismo ``HuEngine`` (``get_engine()``) que el orquestador conversacional, así que
el chat de ADK y la interfaz ven y modifican el mismo estado.

Reglas que se mantienen aquí:
- Las decisiones humanas solo entran por esta API (o por el chat): ningún agente las toma.
- Un proyecto no admite dos ejecuciones a la vez (409 mientras haya una en curso).
- La carga de documentos es la única escritura sobre la entrada (bucket de proyectos).
"""

from __future__ import annotations

import asyncio
import csv
import io
import os
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable
from urllib.parse import unquote

import yaml
from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from adk_ing.documentos import EXTENSIONES

from .models.common import now_iso
from .models.state import WorkflowState as S
from .models.user_story import UserStory
from .tools.gcs_tools import loose_key, scan_project_files, split_root
from .tools.project_tools import AUDIO_FORMATS, classify
from .workflows.engine import ProjectNotFound, get_engine

router = APIRouter(prefix="/api", tags=["smart-product-backlog"])

AUDIO_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/m4a"}
PERMITIDAS = EXTENSIONES | set(AUDIO_MIME)
MAX_AUDIO_MB = 20          # límite de un audio enviado en línea a Gemini para transcribir
DECISIONES = ("APPROVED", "MODIFIED", "REJECTED", "NEEDS_MORE_INFORMATION")

ESTADO_UI = {  # estado del flujo → estado que muestra la interfaz
    S.WAITING_FOR_HUMAN: "decision", S.REJECTED: "descartada", S.ERROR: "error",
    S.COMPLETED: "aprobada", S.APPROVED: "aprobada",
}


# ============================================================== transcripción
PROMPT_TRANSCRIPCION = (
    "Transcribe literalmente este audio de una reunión de proyecto, en su idioma original. "
    "Separa por hablante cuando se distinga (Hablante 1, Hablante 2… o el nombre si lo dicen). "
    "No resumas, no corrijas cifras ni nombres y marca lo inaudible como [inaudible]."
)


async def transcribir_con_gemini(data: bytes, mime: str, nombre: str) -> str:
    from google import genai
    from google.genai import types

    modelo = get_engine().settings.model
    client = genai.Client()
    resp = await client.aio.models.generate_content(
        model=modelo if isinstance(modelo, str) else "gemini-2.5-flash",
        contents=[types.Part.from_bytes(data=data, mime_type=mime), PROMPT_TRANSCRIPCION],
    )
    return (resp.text or "").strip()


# Reemplazable en pruebas (no llama a Gemini).
TRANSCRIPTOR: Callable[[bytes, str, str], Awaitable[str]] = transcribir_con_gemini


# ===================================================================== trabajos
_jobs: dict[str, dict] = {}


def _en_curso(pid: str) -> bool:
    j = _jobs.get(pid)
    return bool(j and not j["task"].done())


def _publico(job: dict | None) -> dict:
    if not job:
        return {"estado": "inactivo"}
    return {k: v for k, v in job.items() if k != "task"}


def _iniciar(pid: str, tipo: str, fabrica: Callable[[], Awaitable[dict]], detalle: str = "") -> dict:
    if _en_curso(pid):
        raise HTTPException(409, f"Ya hay un trabajo en curso en {pid} ({_jobs[pid]['tipo']}). Espera a que termine.")
    info: dict[str, Any] = {"tipo": tipo, "detalle": detalle, "estado": "en_curso", "inicio": now_iso(),
                            "fin": "", "resultado": None, "error": ""}

    async def correr():
        try:
            r = await fabrica()
            info["resultado"] = r
            fallo = str(r.get("status", "")).upper() in ("ERROR",)
            info["estado"] = "error" if fallo else "terminado"
            if fallo:
                info["error"] = r.get("error") or r.get("mensaje") or "Error"
        except Exception as exc:  # noqa: BLE001 — se informa a la interfaz
            info["estado"], info["error"] = "error", f"{type(exc).__name__}: {exc}"[:500]
        finally:
            info["fin"] = now_iso()

    info["task"] = asyncio.get_running_loop().create_task(correr())
    _jobs[pid] = info
    return _publico(info)


# ==================================================================== utilidades
def _usuario(x_usuario: str | None) -> str:
    """Nombre de quien decide. El front lo envía con encodeURIComponent (los encabezados HTTP son ASCII)."""
    u = re.sub(r"[^\w .@\-]", "", unquote(x_usuario or "").strip())[:60]
    return u or "usuario"


def _resolver(project_id: str) -> tuple[Any, str, str]:
    eng = get_engine()
    try:
        pid, root = eng._resolve(project_id)
    except ProjectNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return eng, pid, root


def _ocupado(eng, pid: str) -> bool:
    """Trabajo de la interfaz en curso, o análisis lanzado desde el chat (el motor tiene el candado)."""
    if _en_curso(pid):
        return True
    etapa = (eng.progress.get(pid) or {}).get("etapa")
    return eng._lock.locked() and etapa not in (None, "terminado", "error")


def _sin_trabajo(pid: str) -> None:
    if _ocupado(get_engine(), pid):
        raise HTTPException(409, "Hay un análisis en curso en este proyecto; espera a que termine.")


def _nombre_seguro(nombre: str) -> str:
    base = PurePosixPath((nombre or "").replace("\\", "/")).name
    base = unicodedata.normalize("NFC", base)
    base = re.sub(r'[\x00-\x1f<>:"|?*]', "", base).strip().lstrip(".")
    stem, _, ext = base.rpartition(".") if "." in base else (base, "", "")
    stem = stem.strip()[:120] or "documento"
    return f"{stem}.{ext.lower()}" if ext else stem


def _destino(root: str, nombre: str) -> str:
    """Ruta en la entrada para que el archivo quede dentro del proyecto."""
    folder, key = split_root(root)
    if key:  # proyecto de archivos sueltos: el prefijo del nombre lo agrupa
        final = nombre if loose_key(nombre) == key else f"{key} __ {nombre}"
        return f"{folder}/{final}" if folder else final
    return f"{folder}/documentos/{nombre}"


def _libre(storage, rel: str) -> str:
    if not storage.exists(rel):
        return rel
    ext = PurePosixPath(rel).suffix
    stem = rel[: -len(ext)] if ext else rel
    for i in range(2, 100):
        cand = f"{stem} ({i}){ext}"
        if not storage.exists(cand):
            return cand
    raise HTTPException(409, f"Ya existen demasiadas copias de {rel}")


def _slug(nombre: str) -> str:
    plano = unicodedata.normalize("NFKD", nombre)
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return "_".join(t.upper() for t in re.findall(r"[A-Za-z0-9]+", plano))[:40].strip("_")


# ================================================================== proyectos
class NuevoProyecto(BaseModel):
    nombre: str = Field(min_length=3, max_length=120)
    descripcion: str = Field("", max_length=2000)


@router.get("/health")
def salud() -> dict:
    eng = get_engine()
    return {"ok": True, "entrada": eng.settings.input_uri, "resultados": eng.settings.results_uri,
            "modelo": eng.model if isinstance(eng.model, str) else type(eng.model).__name__,
            "agente_chat": "orquestador_hu", "max_refinamientos": eng.settings.max_reanalysis}


@router.get("/projects")
def listar_proyectos() -> dict:
    eng = get_engine()
    proyectos = []
    for p in eng.discover():
        if "project_id" not in p:
            continue
        p["trabajo"] = _publico(_jobs.get(p["project_id"]))["estado"]
        proyectos.append(p)
    return {"entrada": eng.settings.input_uri, "proyectos": proyectos}


@router.post("/projects", status_code=201)
def crear_proyecto(body: NuevoProyecto, x_usuario: str | None = Header(None)) -> dict:
    eng = get_engine()
    pid = _slug(body.nombre)
    if len(pid) < 2:
        raise HTTPException(400, "El nombre debe tener letras o números.")
    eng.discover()
    if eng._match(pid):
        raise HTTPException(409, f"Ya existe un proyecto con el identificador {pid}.")
    folder = f"projects/{pid.lower()}"
    if eng.input.exists(f"{folder}/project.yaml"):
        raise HTTPException(409, f"Ya existe {folder}/project.yaml")
    data = {"project_id": pid, "project_name": body.nombre.strip(), "status": "active",
            "owner": _usuario(x_usuario), "version": "1.0", "description": body.descripcion.strip(),
            "created_at": now_iso(), "created_with": "Smart Product Backlog"}
    try:
        uri = eng.input.write_bytes(f"{folder}/project.yaml",
                                    yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8"),
                                    "text/yaml; charset=utf-8")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"No se pudo crear el proyecto en {eng.settings.input_uri}: {exc}"[:400]) from exc
    eng.discover()
    return {"project_id": pid, "project_name": data["project_name"], "ubicacion": uri}


# =================================================================== documentos
def _archivos(eng, root: str) -> list[dict]:
    files = scan_project_files(eng.input, root)
    nombres = {inner for inner, _ in files}
    out = []
    for inner, obj in files:
        ext = PurePosixPath(inner).suffix.lower()
        es_audio = ext.lstrip(".") in AUDIO_FORMATS
        stem = inner[: -len(ext)] if ext else inner
        out.append({
            "nombre": PurePosixPath(inner).name, "ruta": inner, "tamano": obj.size,
            "actualizado": obj.updated.isoformat() if obj.updated else "",
            "formato": ext.lstrip(".") or "?", "categoria": classify(inner).value,
            "soportado": ext in EXTENSIONES, "audio": es_audio,
            "transcrito": es_audio and f"{stem}.transcripcion.txt" in nombres,
            "es_transcripcion": inner.endswith(".transcripcion.txt"),
        })
    return sorted(out, key=lambda f: f["ruta"].lower())


@router.get("/projects/{project_id}/files")
def listar_archivos(project_id: str) -> dict:
    eng, pid, root = _resolver(project_id)
    return {"project_id": pid, "archivos": _archivos(eng, root), "permitidas": sorted(PERMITIDAS)}


@router.post("/projects/{project_id}/files")
async def subir_archivos(project_id: str, files: list[UploadFile] = File(...)) -> dict:
    eng, pid, root = _resolver(project_id)
    max_mb = float(os.getenv("DOCS_MAX_MB", "50"))
    resultados = []
    for up in files:
        nombre = _nombre_seguro(up.filename or "")
        ext = PurePosixPath(nombre).suffix.lower()
        item = {"nombre": up.filename, "estado": "error", "mensaje": ""}
        resultados.append(item)
        if ext not in PERMITIDAS:
            item["mensaje"] = f"Formato {ext or 'sin extensión'} no soportado. Usa: {', '.join(sorted(PERMITIDAS))}"
            continue
        data = await up.read()
        if not data:
            item["mensaje"] = "El archivo está vacío."
            continue
        if len(data) > max_mb * 1024 * 1024:
            item["mensaje"] = f"Supera {max_mb:.0f} MB."
            continue
        try:
            rel = _libre(eng.input, _destino(root, nombre))
            item["ruta"] = eng.input.write_bytes(rel, data, up.content_type or AUDIO_MIME.get(ext))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — permisos del bucket, sobre todo
            item["mensaje"] = (f"No se pudo guardar en {eng.settings.input_uri}: {exc}"[:300]
                               + " (la cuenta necesita permiso de escritura, p. ej. roles/storage.objectCreator)")
            continue
        item["estado"], item["mensaje"] = "subido", "Guardado."
        if ext in AUDIO_MIME:
            if len(data) > MAX_AUDIO_MB * 1024 * 1024:
                item["estado"] = "subido_sin_transcripcion"
                item["mensaje"] = (f"El audio supera {MAX_AUDIO_MB} MB y no se transcribió. "
                                   "Divídelo o sube su transcripción en .txt.")
                continue
            try:
                texto = await TRANSCRIPTOR(data, AUDIO_MIME[ext], nombre)
                if not texto:
                    raise ValueError("la transcripción salió vacía")
                trans = rel[: -len(ext)] + ".transcripcion.txt"
                encabezado = (f"# Transcripción de {nombre}\n"
                              "Generada automáticamente con Gemini: verifica nombres, cifras y decisiones.\n\n")
                eng.input.write_bytes(trans, (encabezado + texto + "\n").encode("utf-8"), "text/plain; charset=utf-8")
                item["estado"], item["transcripcion"] = "transcrito", eng.input.uri_for(trans)
                item["mensaje"] = "Guardado y transcrito: la transcripción entra al análisis."
            except Exception as exc:  # noqa: BLE001
                item["estado"] = "subido_sin_transcripcion"
                item["mensaje"] = f"Guardado, pero no se pudo transcribir: {exc}"[:300]
    return {"project_id": pid, "resultados": resultados, "archivos": _archivos(eng, root)}


# ===================================================================== análisis
class Analisis(BaseModel):
    regenerar: bool = False
    forzar: bool = False
    historias: list[str] | None = None


@router.post("/projects/{project_id}/analyze", status_code=202)
async def analizar(project_id: str, body: Analisis | None = None, x_usuario: str | None = Header(None)) -> dict:
    eng, pid, _ = _resolver(project_id)
    body = body or Analisis()
    usuario = _usuario(x_usuario)
    trabajo = _iniciar(pid, "analisis", lambda: eng.analyze_project(
        pid, story_ids=body.historias or None, force=body.forzar, trigger=f"interfaz SPB ({usuario})",
        regenerate=body.regenerar))
    return {"project_id": pid, "trabajo": trabajo}


@router.get("/projects/{project_id}/status")
def estado(project_id: str) -> dict:
    eng, pid, _ = _resolver(project_id)
    trabajo = _publico(_jobs.get(pid))
    if trabajo["estado"] != "en_curso" and _ocupado(eng, pid):
        trabajo = {"estado": "en_curso", "tipo": "analisis", "detalle": "iniciado desde el asistente"}
    return {"project_id": pid, "trabajo": trabajo, "progreso": eng.progress.get(pid),
            "proyecto": eng.project_status(pid)}


# ====================================================================== backlog
def _estado_ui(rec, en_curso: bool) -> str:
    if rec.state in ESTADO_UI:
        return ESTADO_UI[rec.state]
    if rec.state == S.READY_FOR_IMPLEMENTATION:
        if rec.approved_by_human:
            return "aprobada"
        return "revisar" if rec.review_pending else "lista"
    return "analizando" if en_curso else "pendiente"


def _respuestas(agg: dict) -> list[dict]:
    return [{"agente": r.get("agent"), "estado": r.get("status"), "confianza": r.get("confidence"),
             "resumen": r.get("summary", "")} for r in agg.get("responses", [])]


def construir_backlog(eng, pid: str) -> dict:
    state = eng.state.load(pid)
    store = eng.results_store(pid)
    en_curso = _en_curso(pid)
    gen = (store.read_json("generated/story_generation.json") or {}).get("output") or {}
    reporte = store.read_json("generated/reports/backlog_report.json") or {}
    orden = {sid: i for i, sid in enumerate(reporte.get("recommended_order", []))}

    historias = []
    for sid, rec in state.stories.items():
        base = f"generated/user_stories/{sid}"
        original = store.read_json(f"{base}/original.json")
        if not original:
            continue
        story = UserStory.model_validate(original)
        proposed = store.read_json(f"{base}/proposed.json") or {}
        hitl = store.read_json(f"{base}/hitl.json") or {}
        analysis = (store.read_json(f"{base}/analysis.json") or {}).get("output") or {}
        arch = (store.read_json(f"{base}/architecture_review.json") or {}).get("output") or {}
        tests = (store.read_json(f"{base}/tests.json") or {}).get("output") or {}
        agg = store.read_json(f"{base}/aggregated_review.json") or {}
        mods = rec.human_modifications or {}

        ia = [i for i in arch.get("impacts", []) if i.get("area") == "ai_ml"]
        decision = None
        if rec.state == S.WAITING_FOR_HUMAN and rec.pending_decision_id in state.decisions:
            r = state.decisions[rec.pending_decision_id].request
            decision = {"decision_id": r.decision_id, "motivo": r.reason, "recomendacion": r.recommendation,
                        "riesgo": r.risk, "confianza": r.confidence, "preguntas": r.blocking_questions,
                        "alternativas": r.alternatives, "agente": r.agent}
        riesgos = [{"agente": resp.get("agent"), "descripcion": x.get("description"), "severidad": x.get("severity")}
                   for resp in agg.get("responses", []) for x in resp.get("risks", [])]
        prioridad = mods.get("priority") or story.priority
        historias.append({
            "id": sid, "titulo": mods.get("title") or story.title or rec.title, "epica": story.epic or "Sin épica",
            "origen": story.origin, "fuente": story.source,
            "tipo": "HU-IA" if ia else "HU-Tradicional",
            "tipo_motivo": "; ".join(i.get("description", "") for i in ia),
            "prioridad": prioridad, "prioridad_motivo": "" if mods.get("priority") else story.priority_reason,
            "prioridad_origen": "humano" if mods.get("priority") else ("ia" if story.origin == "generated" else
                                                                      ("documento" if story.priority else "")),
            "estado": rec.state.value, "estado_ui": _estado_ui(rec, en_curso),
            "orden": orden.get(sid), "depende_de": story.depends_on,
            "original": {"narrativa": story.narrative(), "criterios": story.acceptance_criteria,
                         "reglas": story.business_rules},
            "propuesta": proposed.get("improved_story"), "cambios_sugeridos": proposed.get("suggested_changes", []),
            "evidencia": [e.model_dump(mode="json") for e in story.evidence],
            "confianza_generacion": story.generation_confidence,
            "calidad": rec.quality_score, "confianza": rec.confidence, "invest": proposed.get("invest_score"),
            "hitl": {"nivel": rec.hitl_level, "accion": hitl.get("action"),
                     "motivos": [{"codigo": m.get("code"), "descripcion": m.get("description"),
                                  "severidad": m.get("severity"), "critico": m.get("critical", False)}
                                 for m in hitl.get("reasons", [])]},
            "decision": decision,
            "preguntas": [{"pregunta": q.get("question"), "bloqueante": q.get("blocking", False)}
                          for q in analysis.get("missing_information", [])],
            "pruebas": [{"id": t.get("test_id"), "titulo": t.get("title"), "tipo": t.get("type"),
                         "dado": t.get("given"), "cuando": t.get("when"), "entonces": t.get("then")}
                        for t in tests.get("test_cases", [])],
            "no_verificables": [x.get("statement") for x in tests.get("untestable_criteria", [])],
            "riesgos": riesgos, "cambios_criticos": arch.get("critical_changes", []),
            "conflictos": [c.get("description") for c in agg.get("conflicts", [])],
            "agentes": _respuestas(agg),
            "aprobada_por_humano": rec.approved_by_human, "refinamientos": rec.reanalysis_count,
            "comentario_humano": rec.human_feedback, "error": rec.last_error,
        })
    historias.sort(key=lambda h: (h["orden"] if h["orden"] is not None else 10_000, h["id"]))

    epicas: dict[str, dict] = {}
    for h in historias:
        e = epicas.setdefault(h["epica"], {"nombre": h["epica"], "historias": [], "aprobadas": 0, "decision": 0,
                                           "descartadas": 0, "aprobables": 0})
        e["historias"].append(h["id"])
        e["aprobadas"] += h["estado_ui"] == "aprobada"
        e["decision"] += h["estado_ui"] == "decision"
        e["descartadas"] += h["estado_ui"] == "descartada"
        e["aprobables"] += h["estado_ui"] in ("lista", "revisar")
    for e in epicas.values():
        vigentes = len(e["historias"]) - e["descartadas"]
        e["total"] = len(e["historias"])
        e["avance"] = round(100 * e["aprobadas"] / vigentes) if vigentes else 0

    vivas = [h for h in historias if h["estado_ui"] != "descartada"]
    cuenta = lambda f: sum(1 for h in vivas if f(h))  # noqa: E731
    stats = {
        "total": len(vivas), "hu_ia": cuenta(lambda h: h["tipo"] == "HU-IA"),
        "generadas": cuenta(lambda h: h["origen"] == "generated"),
        "aprobadas": cuenta(lambda h: h["estado_ui"] == "aprobada"),
        "decision": cuenta(lambda h: h["estado_ui"] == "decision"),
        "revisar": cuenta(lambda h: h["estado_ui"] == "revisar"),
        "listas": cuenta(lambda h: h["estado_ui"] == "lista"),
        "descartadas": len(historias) - len(vivas),
        "prioridad": {p: cuenta(lambda h, p=p: h["prioridad"] == p) for p in ("Alta", "Media", "Baja")},
        "calidad_promedio": round(sum(h["calidad"] for h in vivas if h["calidad"] is not None)
                                  / max(1, cuenta(lambda h: h["calidad"] is not None)), 1),
    }
    return {
        "project_id": pid, "project_name": state.project_name, "estado": state.state.value,
        "resumen": gen.get("project_summary", ""),
        "decisiones_documentadas": [d.get("statement") for d in gen.get("decisions_found", [])],
        "preguntas_abiertas": [{"pregunta": q.get("question"), "bloqueante": q.get("blocking", False)}
                               for q in gen.get("open_questions", [])],
        "historias": historias, "epicas": list(epicas.values()), "stats": stats,
        "trabajo": _publico(_jobs.get(pid)), "max_refinamientos": eng.settings.max_reanalysis,
        "ciclos_dependencia": reporte.get("dependency_cycles", []),
    }


@router.get("/projects/{project_id}/backlog")
def backlog(project_id: str) -> dict:
    eng, pid, _ = _resolver(project_id)
    return construir_backlog(eng, pid)


# ============================================================= decisiones humanas
class Decision(BaseModel):
    decision: str
    comentario: str = Field("", max_length=4000)
    modificaciones: dict = Field(default_factory=dict)


@router.post("/projects/{project_id}/stories/{story_id}/decision")
async def decidir(project_id: str, story_id: str, body: Decision, x_usuario: str | None = Header(None)):
    eng, pid, _ = _resolver(project_id)
    _sin_trabajo(pid)
    usuario = _usuario(x_usuario)
    d = body.decision.strip().upper()
    if d not in DECISIONES:
        raise HTTPException(400, f"Decisión inválida. Usa: {', '.join(DECISIONES)}")
    sid = story_id.strip().upper()
    rec = eng.state.load(pid).stories.get(sid)
    if not rec:
        raise HTTPException(404, f"{sid} no existe en {pid}.")
    if d == "MODIFIED":  # el reanálisis tarda: se valida aquí y corre en segundo plano
        if not body.comentario.strip() and not body.modificaciones:
            raise HTTPException(400, "Indica qué refinar.")
        if rec.reanalysis_count >= eng.settings.max_reanalysis:
            raise HTTPException(400, f"{sid} ya tuvo {eng.settings.max_reanalysis} refinamientos: apruébala o descártala.")
        if rec.state not in (S.READY_FOR_IMPLEMENTATION, S.WAITING_FOR_HUMAN):
            raise HTTPException(400, f"{sid} está en {rec.state.value}; ahora no se puede refinar.")
        trabajo = _iniciar(pid, "refinamiento", lambda: eng.human_action(
            pid, sid, d, body.comentario.strip(), body.modificaciones, usuario), detalle=sid)
        return JSONResponse({"status": "en_curso", "story_id": sid, "trabajo": trabajo}, status_code=202)
    r = await eng.human_action(pid, sid, d, body.comentario.strip(), body.modificaciones, usuario)
    if r.get("status") == "error":
        raise HTTPException(400, r.get("mensaje", "No se pudo aplicar la decisión."))
    return r


class Epica(BaseModel):
    epica: str = Field(min_length=1, max_length=200)


@router.post("/projects/{project_id}/epics/approve")
async def aprobar_epica(project_id: str, body: Epica, x_usuario: str | None = Header(None)) -> dict:
    eng, pid, _ = _resolver(project_id)
    _sin_trabajo(pid)
    return await eng.approve_epic(pid, body.epica, _usuario(x_usuario))


@router.get("/projects/{project_id}/stories/{story_id}/explain")
def explicar(project_id: str, story_id: str) -> dict:
    eng, pid, _ = _resolver(project_id)
    r = eng.explain_story(pid, story_id)
    if r.get("status") == "error":
        raise HTTPException(404, r["mensaje"])
    return r


# ==================================================================== exportar
COLUMNAS = ["id", "titulo", "epica", "tipo", "origen", "prioridad", "estado", "nivel_hitl", "calidad", "confianza",
            "narrativa", "criterios", "depende_de", "aprobada_por_humano"]


@router.get("/projects/{project_id}/export")
def exportar(project_id: str, formato: str = Query("json", pattern="^(json|csv)$")) -> Response:
    eng, pid, _ = _resolver(project_id)
    data = construir_backlog(eng, pid)
    vigentes = [h for h in data["historias"] if h["estado_ui"] != "descartada"]
    nombre = f"backlog_{pid}_{now_iso()[:10]}"
    if formato == "json":
        import json

        cuerpo = json.dumps({k: data[k] for k in ("project_id", "project_name", "resumen", "stats", "epicas")}
                            | {"historias": vigentes, "exportado": now_iso()}, ensure_ascii=False, indent=2)
        return Response(cuerpo, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{nombre}.json"'})
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")  # ; abre bien en Excel con configuración regional en español
    w.writerow(COLUMNAS)
    for h in vigentes:
        prop = h.get("propuesta") or {}
        w.writerow([h["id"], prop.get("title") or h["titulo"], h["epica"], h["tipo"],
                    "IA" if h["origen"] == "generated" else "documento", h["prioridad"], h["estado_ui"],
                    h["hitl"]["nivel"], h["calidad"] if h["calidad"] is not None else "",
                    h["confianza"] if h["confianza"] is not None else "",
                    prop.get("narrative") or h["original"]["narrativa"],
                    " | ".join(prop.get("acceptance_criteria") or h["original"]["criterios"]),
                    ", ".join(h["depende_de"]), "sí" if h["aprobada_por_humano"] else "no"])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}.csv"'})
