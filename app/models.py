import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    story_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    epics: Mapped[list["Epic"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Epic.position"
    )
    stories: Mapped[list["UserStory"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    stages: Mapped[list["PipelineStage"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="PipelineStage.stage_number"
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32))

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class Epic(Base):
    __tablename__ = "epics"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="epics")
    stories: Mapped[list["UserStory"]] = relationship(back_populates="epic")


class UserStory(Base):
    __tablename__ = "user_stories"
    __table_args__ = (
        UniqueConstraint("project_id", "code"),
        UniqueConstraint("project_id", "external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    epic_id: Mapped[int | None] = mapped_column(ForeignKey("epics.id", ondelete="SET NULL"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    code: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(Text)
    need: Mapped[str | None] = mapped_column(Text)
    benefit: Mapped[str | None] = mapped_column(Text)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)  # markdown
    type: Mapped[str | None] = mapped_column(String(24))
    type_justification: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="borrador", index=True)
    # Tamaño funcional (puntos de función IFPUG sin ajustar) y su análisis; ver services/fpa.py.
    function_points: Mapped[float | None] = mapped_column(Float)
    fp_analysis: Mapped[dict | None] = mapped_column(JSON)
    # Tamaño no funcional y análisis contra el grafo de contexto (restricciones, violaciones); ver services/nfr.py.
    nfr_points: Mapped[float | None] = mapped_column(Float)
    context_analysis: Mapped[dict | None] = mapped_column(JSON)
    priority_rank: Mapped[int | None] = mapped_column(Integer)
    priority_method: Mapped[str | None] = mapped_column(String(64))
    source_ref: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    project: Mapped[Project] = relationship(back_populates="stories")
    epic: Mapped[Epic | None] = relationship(back_populates="stories")
    assignee: Mapped[User | None] = relationship()
    events: Mapped[list["StoryEvent"]] = relationship(
        back_populates="story", cascade="all, delete-orphan", order_by="StoryEvent.id"
    )
    refinements: Mapped[list["RefinementRequest"]] = relationship(
        back_populates="story", cascade="all, delete-orphan", order_by="RefinementRequest.id"
    )
    reviews: Mapped[list["StoryReview"]] = relationship(
        back_populates="story", cascade="all, delete-orphan", order_by="StoryReview.id"
    )


class StoryEvent(Base):
    __tablename__ = "story_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("user_stories.id", ondelete="CASCADE"))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_name: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(32))
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    diff: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    story: Mapped[UserStory] = relationship(back_populates="events")


class RefinementRequest(Base):
    __tablename__ = "refinement_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    story_id: Mapped[int] = mapped_column(ForeignKey("user_stories.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    claimed_by: Mapped[str | None] = mapped_column(String(160))
    claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    story: Mapped[UserStory] = relationship(back_populates="refinements")


class ContextGraph(Base):
    """Una versión del grafo de contexto de un proyecto. Las versiones no se borran (trazabilidad).

    status: draft (propuesta del agente o de una persona) → approved (la usa el dimensionamiento) →
    superseded (reemplazada por una aprobación posterior); o rejected.
    """

    __tablename__ = "context_graphs"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    nodes: Mapped[list] = mapped_column(JSON, default=list)
    edges: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(160))
    agent_run_id: Mapped[int | None] = mapped_column(Integer)
    log: Mapped[list] = mapped_column(JSON, default=list)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship()
    reviewed_by: Mapped[User | None] = relationship()


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"
    __table_args__ = (UniqueConstraint("project_id", "stage_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    stage_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    change_summary: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="stages")
    approved_by: Mapped[User | None] = relationship()


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("user_stories.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(24))  # generate_stories | generate_from_docs | refine_story | size_story | size_backlog | extract_context
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(120))
    input_ref: Mapped[str | None] = mapped_column(String(500))
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship()


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class StoryReview(Base):
    """Revisión multiagente de una versión de la HU (analista, arquitectura, QA, estimación) y su nivel HITL.

    El nivel lo calcula services/hitl.py con reglas en código: 0 = sin observaciones, 1 = revisar,
    2 = cambios críticos o información faltante (solo el product owner decide). Nada se aprueba solo:
    toda revisión espera una decisión humana (HitlDecision).
    """

    __tablename__ = "story_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("user_stories.id", ondelete="CASCADE"), index=True)
    story_version: Mapped[int] = mapped_column(Integer)
    # Versión resultante cuando una persona aprueba aplicando la propuesta (la revisión sigue vigente).
    applied_version: Mapped[int | None] = mapped_column(Integer)
    # Huella del contenido revisado (título, rol, necesidad, beneficio, criterios) y del aprobado. La revisión
    # sigue vigente mientras el contenido no cambie, aunque cambien estado, prioridad, asignación o tamaño.
    content_hash: Mapped[str | None] = mapped_column(String(64))
    applied_hash: Mapped[str | None] = mapped_column(String(64))
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|done|failed
    level: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    quality: Mapped[float | None] = mapped_column(Float)
    story_type: Mapped[str | None] = mapped_column(String(24))
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    proposal: Mapped[dict | None] = mapped_column(JSON)
    estimation: Mapped[dict | None] = mapped_column(JSON)
    agents: Mapped[dict | None] = mapped_column(JSON)  # salida de cada agente y errores
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    story: Mapped[UserStory] = relationship(back_populates="reviews")
    decisions: Mapped[list["HitlDecision"]] = relationship(
        back_populates="review", cascade="all, delete-orphan", order_by="HitlDecision.id"
    )


class HitlDecision(Base):
    """Decisión o comentario de una persona sobre una revisión. Solo personas: un servicio no decide."""

    __tablename__ = "hitl_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("story_reviews.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("user_stories.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(24))  # aprobar|pedir_cambios|descartar|falta_info|comentario
    comment: Mapped[str | None] = mapped_column(Text)
    applied_proposal: Mapped[bool] = mapped_column(Boolean, default=False)
    level: Mapped[int | None] = mapped_column(Integer)
    role: Mapped[str | None] = mapped_column(String(32))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    review: Mapped[StoryReview] = relationship(back_populates="decisions")
    decided_by: Mapped[User | None] = relationship()
