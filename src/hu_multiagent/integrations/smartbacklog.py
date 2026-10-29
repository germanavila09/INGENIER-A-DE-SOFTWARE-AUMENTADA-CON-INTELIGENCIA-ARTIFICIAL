"""Puente hacia SmartBacklogIA: publica las HU revisadas aquí como su backlog de partida.

Reparto de responsabilidades entre los dos sistemas (ver docs/integracion_smartbacklog.md):

- Aquí: documentos del bucket → generación y análisis multiagente → revisión humana por historia
  (niveles HITL 0/1/2) → tipo, estimación por factores y orden recomendado.
- SmartBacklogIA: sistema de registro con roles y las 5 etapas HITL por proyecto
  (generación, clasificación, estimación, priorización y backlog).

La publicación usa la API de servicio de SmartBacklogIA (``POST /api/v1/ingest/projects`` con
``X-API-Key``), que es idempotente por ``external_id``. Reglas que se mantienen:

- Por defecto solo se publican historias **aprobadas por una persona** aquí.
- Una historia ya publicada no se vuelve a enviar salvo con ``actualizar=True``; y aun así se
  respeta lo que se haya hecho allá: si en SmartBacklogIA ya salió de «borrador» o alguien la editó
  después de la última publicación, se omite (las ediciones humanas allá no se pisan).
- Las etapas de SmartBacklogIA solo las aprueba una persona en su interfaz: este puente las lee,
  nunca las aprueba (su API lo rechaza para servicios).

Uso por consola:
    python -m hu_multiagent.integrations.smartbacklog estado
    python -m hu_multiagent.integrations.smartbacklog publicar SERVI_SINCHI --clave SINCHI --simular
    python -m hu_multiagent.integrations.smartbacklog publicar SERVI_SINCHI --clave SINCHI
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..models.common import now_iso

ORIGEN = "hu_multiagent"
REGISTRO = "integrations/smartbacklog.json"   # en el destino de resultados del proyecto
CLAVE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")  # la que exige SmartBacklogIA
TIPOS = {"HU-IA": "HU_IA", "HU-Tradicional": "HU_TRADICIONAL"}
ESTADOS_PUBLICABLES_TODAS = ("aprobada", "lista", "revisar")
NARRATIVA_RE = re.compile(r"^\s*como\s+(?P<rol>.+?)\s*,?\s+quiero\s+(?P<necesidad>.+?)"
                          r"(?:\s*,?\s+para\s+(?P<beneficio>.+?))?\s*\.?\s*$", re.IGNORECASE | re.DOTALL)


class SmartBacklogError(Exception):
    """SmartBacklogIA no está configurado, no responde o rechazó la petición."""

    def __init__(self, mensaje: str, status: int | None = None) -> None:
        super().__init__(mensaje)
        self.status = status


# ================================================================ configuración
def _mapa(texto: str) -> dict[str, str]:
    out = {}
    for par in filter(None, (p.strip() for p in texto.split(";"))):
        if "=" not in par:
            raise ValueError(f"SBIA_CLAVES: «{par}» debe tener la forma PROYECTO=CLAVE")
        pid, clave = (x.strip() for x in par.split("=", 1))
        clave = clave.upper()
        if not CLAVE_RE.match(clave):
            raise ValueError(f"SBIA_CLAVES: «{clave}» no es una clave válida (letra inicial y 2-10 letras o dígitos)")
        out[pid.upper()] = clave
    return out


@dataclass(frozen=True)
class SmartBacklogConfig:
    url: str = "http://localhost:8000"
    api_key: str = field(default="", repr=False)
    claves: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 30.0

    @classmethod
    def from_env(cls) -> "SmartBacklogConfig":
        return cls(url=(os.getenv("SBIA_URL") or "http://localhost:8000").rstrip("/"),
                   api_key=os.getenv("SBIA_API_KEY", "").strip(),
                   claves=_mapa(os.getenv("SBIA_CLAVES", "")),
                   timeout_s=float(os.getenv("SBIA_TIMEOUT_S", "30")))

    @property
    def configurado(self) -> bool:
        return bool(self.api_key)

    def clave_para(self, project_id: str, explicita: str | None = None) -> str:
        """Clave del proyecto en SmartBacklogIA: la indicada, la de SBIA_CLAVES o una derivada del id."""
        if explicita:
            clave = explicita.strip().upper()
        elif project_id.upper() in self.claves:
            return self.claves[project_id.upper()]
        else:
            base = unicodedata.normalize("NFKD", project_id).encode("ascii", "ignore").decode().upper()
            partes = [p for p in re.split(r"[^A-Z0-9]+", base) if p]
            # SERVI_SINCHI → SINCHI (la última parte suele ser la significativa); si no cabe, se recorta.
            clave = (partes[-1] if partes and len(partes[-1]) >= 2 else "".join(partes))[:10]
            if clave and not clave[0].isalpha():
                clave = ("P" + clave)[:10]
        if not CLAVE_RE.match(clave or ""):
            raise SmartBacklogError(f"«{clave}» no sirve como clave de SmartBacklogIA: usa una letra inicial "
                                    "y de 2 a 10 letras o dígitos (o define SBIA_CLAVES).")
        return clave

    def ui_url(self, clave: str) -> str:
        return f"{self.url}/p/{clave}"


# ======================================================================= cliente
class SmartBacklogClient:
    """Cliente mínimo de la API de servicio de SmartBacklogIA."""

    def __init__(self, config: SmartBacklogConfig, transport: httpx.BaseTransport | None = None) -> None:
        if not config.configurado:
            raise SmartBacklogError("Falta SBIA_API_KEY: créala en SmartBacklogIA → Administración → API keys.")
        self.config = config
        self._http = httpx.Client(base_url=config.url, timeout=config.timeout_s, transport=transport,
                                  headers={"X-API-Key": config.api_key, "Accept": "application/json"})

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SmartBacklogClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _pedir(self, metodo: str, ruta: str, **kw) -> Any:
        try:
            r = self._http.request(metodo, ruta, **kw)
        except httpx.HTTPError as exc:
            raise SmartBacklogError(f"SmartBacklogIA no responde en {self.config.url}: {exc}") from exc
        if r.status_code == 401:
            raise SmartBacklogError("SmartBacklogIA rechazó la API key (inválida o revocada).", 401)
        if r.status_code >= 400:
            try:
                detalle = r.json().get("detail")
            except ValueError:
                detalle = r.text[:300]
            raise SmartBacklogError(f"SmartBacklogIA respondió {r.status_code}: {detalle}", r.status_code)
        return r.json() if r.content else None

    def salud(self) -> dict:
        return self._pedir("GET", "/health")

    def proveedor(self) -> dict:
        return self._pedir("GET", "/api/v1/agents/status")

    def proyecto(self, clave: str) -> dict | None:
        try:
            return self._pedir("GET", f"/api/v1/projects/{clave}")
        except SmartBacklogError as exc:
            if exc.status == 404:
                return None
            raise

    def historias(self, clave: str) -> list[dict]:
        data = self._pedir("GET", f"/api/v1/projects/{clave}/stories")
        return data["items"] if isinstance(data, dict) and "items" in data else (data or [])

    def pipeline(self, clave: str) -> list[dict]:
        return self._pedir("GET", f"/api/v1/projects/{clave}/pipeline")

    def ingestar(self, payload: dict) -> dict:
        return self._pedir("POST", "/api/v1/ingest/projects", json=payload)


# ======================================================================== mapeo
def external_id(project_id: str, story_id: str) -> str:
    return f"{ORIGEN}:{project_id}:{story_id}"[:120]


def partir_narrativa(narrativa: str) -> tuple[str | None, str | None, str | None]:
    """«Como <rol> quiero <necesidad> para <beneficio>» → (rol, necesidad, beneficio)."""
    m = NARRATIVA_RE.match(narrativa or "")
    if not m:
        return None, (narrativa or "").strip() or None, None
    limpia = lambda s: (s or "").strip().rstrip(".").strip() or None  # noqa: E731
    return limpia(m["rol"]), limpia(m["necesidad"]), limpia(m["beneficio"])


def _criterios_md(criterios: list[str]) -> str | None:
    lineas = [f"- [ ] {c.strip()}" for c in criterios if c and c.strip()]
    return "\n".join(lineas) or None


def _fuentes(h: dict) -> list[str]:
    """Documentos de origen por nombre: los citados como evidencia (generadas) o el documento de la HU."""
    rutas = [e.get("source") for e in h.get("evidencia") or [] if e.get("source")]
    if not rutas and h.get("origen") != "generated" and h.get("fuente"):
        rutas = [h["fuente"]]
    nombres = []
    for r in rutas:
        n = r.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if n and n not in nombres:
            nombres.append(n)
    return nombres[:3]


def _referencia(project_id: str, h: dict) -> str:
    """Trazabilidad legible en SmartBacklogIA: origen, estimación por factores y revisión de aquí."""
    partes = [f"{ORIGEN} {project_id}/{h['id']}"]
    fuentes = _fuentes(h)
    if fuentes:
        partes.append(f"fuente: {', '.join(fuentes)}")
    est = h.get("estimacion") or {}
    if est.get("puntos"):
        rango = est.get("rango") or {}
        txt = f"estimación {est['puntos']} pts"
        if rango:
            txt += f" ({rango['min']}–{rango['max']})"
        txt += f", {est.get('metodo', '')}, por {'persona' if est.get('origen') == 'humano' else 'agente'}"
        if (est.get("spike") or {}).get("required"):
            txt += f", spike {est['spike']['points']} pts"
        partes.append(txt)
    if h.get("prioridad"):
        partes.append(f"prioridad {h['prioridad']}")
    if h.get("calidad") is not None:
        partes.append(f"calidad {h['calidad']}")
    partes.append(f"HITL nivel {h['hitl']['nivel']}")
    partes.append("aprobada por persona" if h.get("aprobada_por_humano") else "sin aprobación humana")
    return " · ".join(partes)


def historia_a_sbia(project_id: str, h: dict) -> dict:
    """Historia del backlog de aquí (``api.construir_backlog``) → ``StoryIn`` de SmartBacklogIA."""
    prop = h.get("propuesta") or {}
    rol, necesidad, beneficio = partir_narrativa(prop.get("narrative") or h["original"]["narrativa"])
    criterios = prop.get("acceptance_criteria") or h["original"]["criterios"]
    titulo = (prop.get("title") or h["titulo"] or h["id"]).strip()
    if len(titulo) < 3:
        titulo = f"{h['id']} {titulo}".strip()
    data = {
        "external_id": external_id(project_id, h["id"]),
        "epic": None if h.get("epica") in (None, "", "Sin épica") else h["epica"][:255],
        "title": titulo[:255],
        "role": rol, "need": necesidad, "benefit": beneficio,
        "acceptance_criteria": _criterios_md(criterios),
        "type": TIPOS.get(h.get("tipo"), "HU_TRADICIONAL"),
        "type_justification": (h.get("tipo_motivo") or None) and h["tipo_motivo"][:2000],
        "source_ref": _referencia(project_id, h),
    }
    if h.get("orden") is not None:
        data["priority_rank"] = h["orden"] + 1
        data["priority_method"] = f"{ORIGEN}:orden_recomendado"
    return data


# ============================================================ plan y publicación
def _publicable(h: dict, incluir_sin_aprobar: bool) -> tuple[bool, str]:
    ui = h["estado_ui"]
    if ui == "aprobada" and h.get("aprobada_por_humano"):
        return True, ""
    if incluir_sin_aprobar and ui in ESTADOS_PUBLICABLES_TODAS:
        return True, ""
    motivos = {"decision": "espera una decisión humana", "descartada": "descartada", "error": "terminó con error",
               "analizando": "en análisis", "pendiente": "sin analizar"}
    return False, motivos.get(ui, "falta la aprobación de una persona")


def planear(project_id: str, backlog: dict, remotas: list[dict], registro: dict | None = None,
            actualizar: bool = False, incluir_sin_aprobar: bool = False) -> list[dict]:
    """Qué pasaría con cada historia: ``crear``, ``actualizar`` u ``omitir`` (con el motivo)."""
    por_ext = {r.get("external_id"): r for r in remotas if r.get("external_id")}
    publicadas = (registro or {}).get("historias", {})
    plan = []
    for h in backlog["historias"]:
        ext = external_id(project_id, h["id"])
        remota = por_ext.get(ext)
        item = {"id": h["id"], "titulo": h["titulo"], "estado_ui": h["estado_ui"], "external_id": ext,
                "codigo": remota.get("code") if remota else None,
                "estado_remoto": remota.get("status") if remota else None}
        ok, motivo = _publicable(h, incluir_sin_aprobar)
        if not ok:
            item.update(accion="omitir", motivo=motivo)
        elif remota is None:
            item.update(accion="crear", motivo="")
        elif not actualizar:
            item.update(accion="omitir", motivo=f"ya publicada como {remota.get('code')}")
        elif remota.get("status") != "borrador":
            item.update(accion="omitir", motivo=f"en SmartBacklogIA ya está en «{remota.get('status')}»: edítala allá")
        elif (publicadas.get(h["id"]) or {}).get("version_remota") not in (None, remota.get("version")):
            item.update(accion="omitir", motivo="alguien la editó en SmartBacklogIA después de publicarla")
        else:
            item.update(accion="actualizar", motivo="")
        plan.append(item)
    return plan


def resumen_plan(plan: list[dict]) -> dict:
    cuenta = {"crear": 0, "actualizar": 0, "omitir": 0}
    for p in plan:
        cuenta[p["accion"]] += 1
    return cuenta


def payload_ingest(clave: str, nombre: str, descripcion: str, project_id: str, backlog: dict, plan: list[dict]) -> dict:
    enviar = {p["id"] for p in plan if p["accion"] in ("crear", "actualizar")}
    historias = [historia_a_sbia(project_id, h) for h in backlog["historias"] if h["id"] in enviar]
    return {"key": clave, "name": nombre[:200] if len(nombre) >= 2 else clave, "description": descripcion or None,
            "stories": historias}


def publicar(eng, project_id: str, cliente: SmartBacklogClient, clave: str | None = None, actualizar: bool = False,
             incluir_sin_aprobar: bool = False, simular: bool = False, usuario: str = "usuario") -> dict:
    """Publica en SmartBacklogIA las historias del proyecto que correspondan y deja registro y auditoría."""
    from ..api import construir_backlog   # import tardío: api importa el motor
    from ..services.audit_service import AuditEntry

    pid, _ = eng._resolve(project_id)
    clave = cliente.config.clave_para(pid, clave)
    backlog = construir_backlog(eng, pid)
    store = eng.results_store(pid)
    registro = store.read_json(REGISTRO) or {}
    if registro.get("clave") and registro["clave"] != clave:
        raise SmartBacklogError(f"{pid} ya se publicó en SmartBacklogIA como {registro['clave']}; "
                                f"usa esa clave o borra {REGISTRO} para cambiarla.", 409)
    existe = cliente.proyecto(clave)
    remotas = cliente.historias(clave) if existe else []
    plan = planear(pid, backlog, remotas, registro, actualizar=actualizar, incluir_sin_aprobar=incluir_sin_aprobar)
    out = {"project_id": pid, "clave": clave, "url": cliente.config.ui_url(clave), "proyecto_nuevo": existe is None,
           "plan": plan, "resumen": resumen_plan(plan), "simulado": simular, "resultado": None}
    a_enviar = out["resumen"]["crear"] + out["resumen"]["actualizar"]
    if simular or not a_enviar:
        return out

    nombre = (existe or {}).get("name") or backlog.get("project_name") or pid
    descripcion = (existe or {}).get("description") or f"Publicado desde {ORIGEN} ({pid}). {backlog.get('resumen', '')}".strip()
    out["resultado"] = cliente.ingestar(payload_ingest(clave, nombre, descripcion[:4000], pid, backlog, plan))

    # Registro local: código y versión remota de cada historia, para detectar ediciones allá.
    por_ext = {r.get("external_id"): r for r in cliente.historias(clave)}
    historias = dict(registro.get("historias", {}))
    for p in plan:
        if p["accion"] in ("crear", "actualizar") and (r := por_ext.get(p["external_id"])):
            historias[p["id"]] = {"codigo": r.get("code"), "version_remota": r.get("version"), "publicada": now_iso()}
            p["codigo"], p["estado_remoto"] = r.get("code"), r.get("status")
    store.write_json(REGISTRO, {"clave": clave, "url": cliente.config.url, "ultima": now_iso(), "por": usuario,
                                "historias": historias})
    eng.audit.record(AuditEntry(event="smartbacklog_publish", project_id=pid, agent=ORIGEN, human_approval=usuario,
                                decision=f"{a_enviar} historia(s) → {clave}",
                                details={"clave": clave, "url": cliente.config.url, "resumen": out["resumen"],
                                         "resultado": out["resultado"],
                                         "historias": [p["id"] for p in plan if p["accion"] != "omitir"]}))
    return out


def estado_remoto(eng, project_id: str, cliente: SmartBacklogClient, clave: str | None = None) -> dict:
    """Lo que hay del proyecto en SmartBacklogIA: etapas HITL, historias publicadas y plan de publicación."""
    from ..api import construir_backlog

    pid, _ = eng._resolve(project_id)
    registro = eng.results_store(pid).read_json(REGISTRO) or {}
    clave = cliente.config.clave_para(pid, clave or registro.get("clave"))
    existe = cliente.proyecto(clave)
    remotas = cliente.historias(clave) if existe else []
    backlog = construir_backlog(eng, pid)
    plan = planear(pid, backlog, remotas, registro)
    propias = [r for r in remotas if str(r.get("external_id") or "").startswith(f"{ORIGEN}:{pid}:")]
    return {"project_id": pid, "clave": clave, "url": cliente.config.ui_url(clave), "existe": existe is not None,
            "nombre": (existe or {}).get("name"),
            "etapas": cliente.pipeline(clave) if existe else [],
            "historias": [{k: r.get(k) for k in ("code", "external_id", "title", "status", "type", "function_points",
                                                  "priority_rank", "version")} for r in propias],
            "total_remotas": len(remotas), "registro": registro, "plan": plan, "resumen": resumen_plan(plan)}


# ======================================================================== consola
def _imprimir_plan(out: dict) -> None:
    print(f"\n{out['project_id']} → SmartBacklogIA {out['clave']}  ({out['url']})"
          + ("  [proyecto nuevo]" if out.get("proyecto_nuevo") else ""))
    for p in out["plan"]:
        extra = f"  ({p['motivo']})" if p.get("motivo") else (f"  → {p['codigo']}" if p.get("codigo") else "")
        print(f"  {p['accion']:<10} {p['id']:<12} {p['titulo'][:60]}{extra}")
    r = out["resumen"]
    print(f"\ncrear {r['crear']} · actualizar {r['actualizar']} · omitir {r['omitir']}")


def main(argv: list[str] | None = None) -> int:
    from ..workflows.engine import get_engine

    ap = argparse.ArgumentParser(prog="python -m hu_multiagent.integrations.smartbacklog",
                                 description="Publica las HU revisadas en SmartBacklogIA (SBIA_URL, SBIA_API_KEY).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("estado", help="comprueba la conexión y la API key")
    pub = sub.add_parser("publicar", help="publica las HU aprobadas de un proyecto")
    pub.add_argument("proyecto")
    pub.add_argument("--clave", help="clave del proyecto en SmartBacklogIA (p. ej. SINCHI)")
    pub.add_argument("--actualizar", action="store_true", help="reenvía las ya publicadas que sigan en borrador")
    pub.add_argument("--incluir-sin-aprobar", action="store_true",
                     help="incluye las listas o por revisar (no las que esperan decisión)")
    pub.add_argument("--simular", action="store_true", help="solo muestra el plan, no envía nada")
    pub.add_argument("--usuario", default=os.getenv("USERNAME") or os.getenv("USER") or "consola")
    rem = sub.add_parser("remoto", help="etapas e historias del proyecto en SmartBacklogIA")
    rem.add_argument("proyecto")
    rem.add_argument("--clave")
    args = ap.parse_args(argv)

    try:
        cfg = SmartBacklogConfig.from_env()
        with SmartBacklogClient(cfg) as cli:
            if args.cmd == "estado":
                print("SmartBacklogIA:", cfg.url, cli.salud())
                print("agente:", {k: cli.proveedor().get(k) for k in ("provider", "model", "backend", "configured")})
                return 0
            eng = get_engine()
            if args.cmd == "remoto":
                print(json.dumps(estado_remoto(eng, args.proyecto, cli, args.clave), ensure_ascii=False, indent=2))
                return 0
            out = publicar(eng, args.proyecto, cli, args.clave, actualizar=args.actualizar,
                           incluir_sin_aprobar=args.incluir_sin_aprobar, simular=args.simular, usuario=args.usuario)
            _imprimir_plan(out)
            if out["resultado"]:
                print("SmartBacklogIA:", out["resultado"])
            elif args.simular:
                print("(simulación: no se envió nada)")
            return 0
    except (SmartBacklogError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
