"""Persistencia del estado de proyectos e historias con validación de transiciones.

Backend: JSON en el destino de estado (carpeta local o gs://). Cada proyecto se
guarda en projects/<project_id>/state/project_state.json. La interfaz permite
cambiar a Firestore sin tocar los agentes (fase 2).
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
            self.store(state.project_id).write_json(STATE_FILE, state)

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
