"""Manifest, normalización de documentos y contexto consolidado del proyecto.

Todo aquí es determinista: no se usa LLM para clasificar ni para extraer, y el
ProjectContext solo contiene afirmaciones FACT con su fuente. Los vacíos se
convierten en open_questions.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import PurePosixPath

import yaml

from adk_ing.documentos import FormatoNoSoportado, extraer

from ..models.common import Question, Statement, StatementType, now_iso
from ..models.project import (
    DocumentCategory as C,
    DocumentRef,
    NormalizedDocument,
    ProjectContext,
    ProjectInfo,
    ProjectManifest,
)
from ..models.user_story import UserStory
from ..services.storage_service import InputStorage
from .gcs_tools import read_project_file, root_folder_uri, root_label, root_path, scan_project_files, split_root

# ---------------------------------------------------------------- clasificar
FOLDER_CATEGORY = {
    "requirements": C.REQUIREMENTS, "requerimientos": C.REQUIREMENTS, "requisitos": C.REQUIREMENTS,
    "user_stories": C.USER_STORIES, "historias": C.USER_STORIES, "historias_usuario": C.USER_STORIES, "stories": C.USER_STORIES,
    "epics": C.EPICS, "epicas": C.EPICS,
    "acceptance": C.ACCEPTANCE_CRITERIA, "criterios": C.ACCEPTANCE_CRITERIA,
    "functional": C.FUNCTIONAL, "funcional": C.FUNCTIONAL,
    "architecture": C.ARCHITECTURE, "arquitectura": C.ARCHITECTURE,
    "technical": C.TECHNICAL, "tecnico": C.TECHNICAL, "tecnica": C.TECHNICAL,
    "backlog": C.BACKLOG,
    "decisions": C.DECISIONS, "decisiones": C.DECISIONS, "actas": C.DECISIONS, "adr": C.DECISIONS,
    "dependencies": C.DEPENDENCIES, "dependencias": C.DEPENDENCIES,
    "tests": C.TESTS, "pruebas": C.TESTS,
    "docs": C.OTHER,
}
KEYWORD_CATEGORY = [
    (("historia", "user_stor", "hu_", "us-"), C.USER_STORIES),
    (("backlog",), C.BACKLOG),
    (("epica", "épica", "epic"), C.EPICS),
    (("arquitect", "architect"), C.ARCHITECTURE),
    (("acta", "decision", "decisión", "adr", "notas", "minuta", "reunion", "reunión", "sesion", "sesión",
      "meeting", "comite", "comité", "transcrip"), C.DECISIONS),
    (("propuesta", "proposal", "alcance", "cronograma"), C.FUNCTIONAL),
    (("requisit", "requer", "requirement", "srs"), C.REQUIREMENTS),
    (("tecnic", "técnic", "technical"), C.TECHNICAL),
    (("criterio", "acceptance"), C.ACCEPTANCE_CRITERIA),
    (("dependenc",), C.DEPENDENCIES),
    (("test", "prueba"), C.TESTS),
]
STORY_CATEGORIES = {C.USER_STORIES, C.BACKLOG}
AUDIO_FORMATS = {"mp3", "wav", "m4a"}  # se leen a través de su transcripción (.transcripcion.txt)


def classify(inner_path: str) -> C:
    p = PurePosixPath(inner_path)
    name = p.name.lower()
    if name in ("project.yaml", "project.yml"):
        return C.PROJECT_FILE
    if name.startswith("readme"):
        return C.README
    for part in p.parts[:-1]:
        cat = FOLDER_CATEGORY.get(part.lower())
        if cat:
            return cat
    for keys, cat in KEYWORD_CATEGORY:
        if any(k in name for k in keys):
            return cat
    return C.OTHER


# ------------------------------------------------------------------ manifest
def parse_project_info(root: str, project_yaml: bytes | None) -> ProjectInfo:
    label = root_label(root)
    if project_yaml:
        data = yaml.safe_load(project_yaml.decode("utf-8")) or {}
        if isinstance(data, dict) and data.get("project_id"):
            return ProjectInfo(
                project_id=str(data["project_id"]),
                project_name=str(data.get("project_name", label)),
                status=str(data.get("status", "")),
                owner=str(data.get("owner", "")),
                version=str(data.get("version", "")),
                description=str(data.get("description", "")),
                from_project_yaml=True,
            )
    # Sin project.yaml: el id sale del nombre de la carpeta o del prefijo de los archivos
    # sueltos (y queda una open_question).
    pid = re.sub(r"[^A-Za-z0-9_\-]", "_", label).upper()[:64].strip("_") or "PROYECTO"
    nombre = label.replace("_", " ") if split_root(root)[1] else label
    return ProjectInfo(project_id=pid, project_name=nombre, from_project_yaml=False)


def build_manifest(storage: InputStorage, root: str, previous: ProjectManifest | None = None) -> ProjectManifest:
    files = scan_project_files(storage, root)
    yaml_bytes = None
    for inner, _ in files:
        if inner.lower() in ("project.yaml", "project.yml"):
            yaml_bytes = read_project_file(storage, root, inner)
            break
    info = parse_project_info(root, yaml_bytes)

    docs = []
    for inner, obj in files:
        docs.append(
            DocumentRef(
                path=inner,
                uri=storage.uri_for(root_path(root, inner)),
                category=classify(inner),
                format=PurePosixPath(inner).suffix.lower().lstrip(".") or "sin_extension",
                size=obj.size,
                signature=obj.firma,
                updated=obj.updated.isoformat() if obj.updated else None,
            )
        )

    prev = {d.path: d.signature for d in (previous.documents if previous else [])}
    now = {d.path: d.signature for d in docs}
    new = [p for p in now if p not in prev]
    modified = [p for p in now if p in prev and prev[p] != now[p]]
    deleted = [p for p in prev if p not in now]
    version = (previous.version + 1) if previous and (new or modified or deleted) else (previous.version if previous else 1)

    return ProjectManifest(
        project_id=info.project_id,
        project_name=info.project_name,
        bucket=storage.bucket or storage.uri,
        root_path=root_folder_uri(storage, root),
        documents=docs,
        user_stories=[d.path for d in docs if d.category in STORY_CATEGORIES],
        architecture_documents=[d.path for d in docs if d.category == C.ARCHITECTURE],
        technical_documents=[d.path for d in docs if d.category == C.TECHNICAL],
        new_documents=new if previous else [],
        modified_documents=modified,
        deleted_documents=deleted,
        last_scan=now_iso(),
        version=version,
        info=info,
    )


# ------------------------------------------------------------- normalización
ID_RE = re.compile(r"\b(?:US|HU|EP|EPIC|RF|RNF|RN|REQ|ADR|CA|BR|RSK)-\d{1,4}\b", re.IGNORECASE)
DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b|"
    r"\b\d{1,2} de (?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre) de \d{4}\b",
    re.IGNORECASE,
)
VERSION_RE = re.compile(r"(?:versi[oó]n|version)\s*[:=]?\s*v?(\d+(?:\.\d+)+)", re.IGNORECASE)
AUTHOR_RE = re.compile(r"^\s*(?:autor(?:es|a)?|author[s]?|elaborado por)\s*[:\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _defined_ids(text: str, structured) -> list[str]:
    ids = set()
    for line in text.splitlines():
        s = line.strip().lstrip("#*-| ").strip()
        m = ID_RE.match(s)
        if m:
            ids.add(m.group(0).upper())
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k.lower() in ("id", "story_id", "codigo", "código") and isinstance(v, str) and ID_RE.fullmatch(v.strip()):
                    ids.add(v.strip().upper())
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(structured)
    return sorted(ids)


def _office_authors(fmt: str, data: bytes) -> list[str]:
    try:
        if fmt == "docx":
            from docx import Document

            a = Document(io.BytesIO(data)).core_properties.author
            return [a] if a else []
        if fmt == "pdf":
            from pypdf import PdfReader

            meta = PdfReader(io.BytesIO(data)).metadata
            return [meta.author] if meta and meta.author else []
    except Exception:
        pass
    return []


def normalize_document(storage: InputStorage, root: str, ref: DocumentRef, max_chars: int = 250000) -> NormalizedDocument:
    data = read_project_file(storage, root, ref.path)
    warnings: list[str] = []
    structured = None
    text = ""
    sections = 0
    if ref.format in ("json",):
        try:
            structured = json.loads(data.decode("utf-8-sig"))
            text = json.dumps(structured, ensure_ascii=False, indent=1)
        except ValueError as exc:
            warnings.append(f"JSON inválido: {exc}")
    elif ref.format in ("yaml", "yml"):
        try:
            structured = yaml.safe_load(data.decode("utf-8-sig"))
            text = yaml.safe_dump(structured, allow_unicode=True, sort_keys=False)
        except yaml.YAMLError as exc:
            warnings.append(f"YAML inválido: {exc}")
    if not text:
        try:
            doc = extraer(ref.path, data)
            text = "\n\n".join(f"[{s.etiqueta}]\n{s.texto}" if len(doc.secciones) > 1 else s.texto for s in doc.secciones)
            sections = len(doc.secciones)
            if doc.aviso:
                warnings.append(doc.aviso)
        except FormatoNoSoportado as exc:
            warnings.append(str(exc))
        except Exception as exc:
            warnings.append(f"No se pudo leer: {type(exc).__name__}: {exc}"[:300])

    if len(text) > max_chars:
        warnings.append(f"Texto truncado a {max_chars} caracteres.")
        text = text[:max_chars]

    defined = _defined_ids(text, structured)
    mentioned = sorted({m.upper() for m in ID_RE.findall(text)})
    title = ""
    for line in text.splitlines():
        if line.strip():
            title = line.strip().lstrip("#").strip()[:120]
            break
    vm = VERSION_RE.search(text)
    authors = [a.strip() for a in AUTHOR_RE.findall(text)] + _office_authors(ref.format, data)

    return NormalizedDocument(
        path=ref.path,
        uri=ref.uri,
        category=ref.category,
        format=ref.format,
        title=title or PurePosixPath(ref.path).name,
        text=text,
        sections=sections or (1 if text else 0),
        identifiers=defined,
        references=[i for i in mentioned if i not in defined],
        dates=sorted(set(DATE_RE.findall(text)))[:20],
        version=vm.group(1) if vm else "",
        authors=sorted(set(a for a in authors if a)),
        structured=structured if isinstance(structured, (dict, list)) else None,
        warnings=warnings,
        signature=ref.signature,
    )


# ------------------------------------------------------------------ contexto
RULE_RE = re.compile(r"^\s*[-*]?\s*((?:RN|BR)-\d+[^\n]*|regla de negocio[^\n]*)", re.IGNORECASE | re.MULTILINE)
CONSTRAINT_RE = re.compile(r"^\s*[-*]?\s*((?:RNF)-\d+[^\n]*|restricci[oó]n[^\n]*|constraint[^\n]*)", re.IGNORECASE | re.MULTILINE)
DECISION_RE = re.compile(r"^\s*[-*]?\s*((?:decisi[oó]n|decision|se decide|se aprueba)[^\n]*)", re.IGNORECASE | re.MULTILINE)


def _excerpt(text: str, n: int = 1500) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " […]"


def build_context(
    manifest: ProjectManifest, docs: list[NormalizedDocument], stories: list[UserStory]
) -> ProjectContext:
    def facts(cats: set[C], n: int = 1500) -> list[Statement]:
        return [Statement(statement=_excerpt(d.text, n), type=StatementType.FACT, source=d.uri) for d in docs if d.category in cats and d.text]

    def lines(regex: re.Pattern, cats: set[C] | None = None) -> list[Statement]:
        out, seen = [], set()
        for d in docs:
            if cats and d.category not in cats:
                continue
            for m in regex.findall(d.text):
                s = m.strip()
                if s.lower() not in seen:
                    seen.add(s.lower())
                    out.append(Statement(statement=s, type=StatementType.FACT, source=d.uri))
        return out

    info = manifest.info
    business = []
    if info.description:
        business.append(Statement(statement=info.description, type=StatementType.FACT, source=manifest.root_path + "project.yaml"))
    business += facts({C.README, C.REQUIREMENTS, C.FUNCTIONAL})

    rules = lines(RULE_RE)
    for s in stories:
        for r in s.business_rules:
            rules.append(Statement(statement=f"{s.story_id}: {r}", type=StatementType.FACT, source=s.source))

    deps = [
        Statement(statement=f"{s.story_id} depende de {d}", type=StatementType.FACT, source=s.source)
        for s in stories for d in s.depends_on
    ]
    epics = []
    for e in sorted({s.epic for s in stories if s.epic}):
        src = next(s.source for s in stories if s.epic == e)
        epics.append(Statement(statement=e, type=StatementType.FACT, source=src))
    epics += facts({C.EPICS}, 800)

    # ----------------------------------------------- vacíos → open_questions
    questions: list[Question] = []
    if not info.from_project_yaml:
        questions.append(Question(
            question=f"No existe project.yaml: ¿cuál es el project_id y nombre oficial? Se usó '{info.project_id}' a partir de la carpeta.",
            blocking=False, reason="Identificación del proyecto", sources=[manifest.root_path]))
    if not any(d.category == C.ARCHITECTURE for d in docs):
        questions.append(Question(
            question="No hay documentación de arquitectura; el impacto técnico se evaluará con información limitada.",
            blocking=False, reason="Contexto técnico incompleto", sources=[manifest.root_path]))
    known_ids = {s.story_id.upper() for s in stories} | {i for d in docs for i in d.identifiers}
    for s in stories:
        if not s.acceptance_criteria:
            questions.append(Question(question=f"{s.story_id} no tiene criterios de aceptación.", blocking=True,
                                      reason="Historia no verificable", sources=[s.source]))
        for d in s.depends_on:
            if d.upper() not in known_ids:
                questions.append(Question(question=f"{s.story_id} depende de {d}, que no existe en el proyecto.",
                                          blocking=False, reason="Dependencia no confirmada", sources=[s.source]))
    dep_ids = {d.upper() for s in stories for d in s.depends_on}
    orphan_refs = sorted({r for d in docs for r in d.references
                          if r.startswith(("US-", "HU-")) and r not in known_ids and r not in dep_ids})
    for r in orphan_refs:
        srcs = [d.uri for d in docs if r in d.references]
        questions.append(Question(question=f"Se menciona {r} pero no se encontró su definición.", blocking=False,
                                  reason="Referencia huérfana", sources=srcs))

    risks = [
        Statement(statement=f"{d.path}: {w}", type=StatementType.FACT, source=d.uri)
        for d in docs for w in d.warnings
    ]

    return ProjectContext(
        project_id=manifest.project_id,
        project_name=manifest.project_name,
        business_context=business,
        technical_context=facts({C.TECHNICAL}),
        architecture=facts({C.ARCHITECTURE}, 2500),
        epics=epics,
        user_stories=[s.story_id for s in stories],
        dependencies=deps,
        business_rules=rules,
        technical_constraints=lines(CONSTRAINT_RE),
        known_decisions=lines(DECISION_RE, {C.DECISIONS}) or facts({C.DECISIONS}, 800),
        open_questions=questions,
        risks=risks,
        source_documents=[d.uri for d in docs],
        manifest_version=manifest.version,
    )


PROMPT_PRIORITY = [C.README, C.REQUIREMENTS, C.FUNCTIONAL, C.ARCHITECTURE, C.DECISIONS, C.TECHNICAL,
                   C.EPICS, C.ACCEPTANCE_CRITERIA, C.DEPENDENCIES, C.TESTS, C.OTHER]


def context_for_prompt(
    context: ProjectContext, docs: list[NormalizedDocument], stories: list[UserStory],
    current_story_id: str, max_chars: int = 30000,
) -> str:
    """Texto del contexto que reciben los agentes LLM (solo de ESTE proyecto)."""
    parts = [f"PROYECTO: {context.project_id} — {context.project_name}"]
    if context.open_questions:
        parts.append("PREGUNTAS ABIERTAS YA DETECTADAS:\n" + "\n".join(f"- {q.question}" for q in context.open_questions))
    otras = [s for s in stories if s.story_id != current_story_id]
    if otras:
        parts.append("OTRAS HISTORIAS DEL PROYECTO (para detectar duplicados y dependencias):\n" + "\n".join(
            f"- {s.story_id} | {s.title} | {s.narrative()[:200]} | depende de: {', '.join(s.depends_on) or '—'}" for s in otras))
    texto = "\n\n".join(parts)
    restante = max_chars - len(texto)
    for cat in PROMPT_PRIORITY:
        for d in docs:
            if d.category != cat or not d.text or restante <= 200:
                continue
            bloque = f"\n\n### DOCUMENTO [{d.category.value}] {d.path}\nFUENTE: {d.uri}\n{d.text}"
            if len(bloque) > restante:
                bloque = bloque[:restante] + " […truncado]"
            texto += bloque
            restante -= len(bloque)
    return texto
