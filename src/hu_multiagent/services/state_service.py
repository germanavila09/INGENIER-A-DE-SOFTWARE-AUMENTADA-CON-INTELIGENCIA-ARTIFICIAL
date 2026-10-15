"""Persistencia del estado de proyectos e historias con validación de transiciones.

Backend: JSON en el destino de estado (carpeta local o gs://). Cada proyecto se
guarda en projects/<project_id>/state/project_state.json. La interfaz permite
cambiar a Firestore sin tocar los agentes (fase 2).

Concurrencia: dentro de un proceso, el motor serializa las escrituras de cada proyecto
con un candado. Entre procesos (dos servidores sobre el mismo bucket), cada guardado
compara la versión leída con la guardada y rechaza pisar una más nueva
(ConcurrentModification). Es una verificación optimista, no una transacción: la ventana
entre leer y escribir sigue existiendo; cerrarla del todo requiere precondiciones de
generación de GCS o Firestore.
"""

from __future__ import annotations

import threading

from ..models.common import validate_project_id
from ..models.hitl import DecisionRecord
from ..models.state import (
    PROJECT_TRANSITIONS,
    STORY_TRANSITIONS,
    ProjectState,
    StoryRecord,
    Transition,
    WorkflowState,
    check_transition,
)
from .storage_service import ProjectStore, make_backend

STATE_FILE = "state/project_state.json"


class ConcurrentModification(Exception):
    """Otro proceso guardó el estado del proyecto después de que este lo leyera."""


class StateService:
    def __init__(self, state_uri: str, backend=None) -> None:
        self.uri = state_uri
        self._backend = backend or make_backend(state_uri)
        self._lock = threading.RLock()

    def store(self, project_id: str) -> ProjectStore:
        return ProjectStore(self._backend, project_id)

    # ---------------------------------------------------------------- cargar
    def load(self, project_id: str) -> ProjectState:
        validate_project_id(project_id)
        data = self.store(project_id).read_json(STATE_FILE)
        return ProjectState.model_validate(data) if data else ProjectState(project_id=project_id)

    def exists(self, project_id: str) -> bool:
        return self.store(project_id).exists(STATE_FILE)

    def save(self, state: ProjectState) -> None:
        with self._lock:
            store = self.store(state.project_id)
            guardado = store.read_json(STATE_FILE)
            actual = int((guardado or {}).get("version", 0) or 0)
            if guardado is not None and actual != state.version:
                raise ConcurrentModification(
                    f"El estado de {state.project_id} cambió en otro proceso (versión {actual}, se esperaba "
                    f"{state.version}). Vuelve a cargarlo y repite la operación.")
            state.version = actual + 1
            try:
                store.write_json(STATE_FILE, state)
            except Exception:
                state.version = actual
                raise

    # ----------------------------------------------------------- transiciones
    def transition_project(
        self, state: ProjectState, target: WorkflowState, actor: str, reason: str = "", run_id: str = ""
    ) -> ProjectState:
        check_transition(PROJECT_TRANSITIONS, state.state, target, f"proyecto {state.project_id}")
        if state.state != target:
            state.history.append(Transition(from_state=state.state, to_state=target, actor=actor, reason=reason, run_id=run_id))
            state.state = target
        self.save(state)
        return state

    def transition_story(
        self,
        state: ProjectState,
        story_id: str,
        target: WorkflowState,
        actor: str,
        reason: str = "",
        run_id: str = "",
    ) -> StoryRecord:
        rec = state.stories[story_id]
        check_transition(STORY_TRANSITIONS, rec.state, target, f"historia {story_id}")
        if rec.state != target:
            rec.history.append(Transition(from_state=rec.state, to_state=target, actor=actor, reason=reason, run_id=run_id))
            rec.state = target
        if run_id:
            rec.last_run_id = run_id
        self.save(state)
        return rec

    # ---------------------------------------------------------------- HITL
    def add_decision(self, state: ProjectState, record: DecisionRecord) -> None:
        state.decisions[record.request.decision_id] = record
        self.save(state)

    def find_decision(self, decision_id: str, project_ids: list[str]) -> tuple[ProjectState, DecisionRecord] | None:
        for pid in project_ids:
            st = self.load(pid)
            if decision_id in st.decisions:
                return st, st.decisions[decision_id]
        return None
