"""Modelo simulado para probar el sistema multiagente sin llamar a Gemini.

Devuelve salidas JSON válidas según el agente (detectado por su system prompt) y la
historia en curso, reproduciendo los escenarios del proyecto de ejemplo PRJ001.
"""

from __future__ import annotations

import json
import re
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

LLAMADAS: list[dict] = []    # registro: agente, proyecto, historia, instrucción
FALLAS: dict = {}            # (agente, story_id) -> lista de excepciones a lanzar en orden


def _q(question, blocking=True):
    return {"question": question, "blocking": blocking, "reason": "No está en los documentos", "sources": []}


def analyst(pid, sid, feedback: bool) -> dict:
    base = {
        "story_id": sid, "title": f"Historia {sid}", "epic": "", "role": "usuario", "need": "algo", "benefit": "valor",
        "acceptance_criteria": ["criterio"], "business_rules": [], "dependencies": [], "constraints": [],
        "definition_of_done": ["Pruebas en verde"], "technical_requirements": [], "data_requirements": [],
        "security_requirements": [], "integration_requirements": [], "edge_cases": [],
        "invest_score": {"independent": 4, "negotiable": 4, "valuable": 5, "estimable": 4, "small": 4, "testable": 4},
        "ambiguities": [], "missing_information": [], "risks": [], "suggested_changes": [],
        "improved_story": {"title": f"Historia {sid}", "narrative": "Como usuario quiero algo para obtener valor",
                           "acceptance_criteria": ["criterio verificable"]},
        "requires_hitl": False, "hitl_reason": "", "confidence": 0.92, "summary": f"Análisis de {sid}.",
    }
    if pid == "PRJ001" and sid == "US-001" and not feedback:
        base.update({
            "confidence": 0.72, "requires_hitl": True,
            "hitl_reason": "La fórmula de incidencia y la fuente poblacional no están definidas.",
            "invest_score": {"independent": 4, "negotiable": 4, "valuable": 5, "estimable": 2, "small": 4, "testable": 2},
            "ambiguities": [{"statement": "'Incidencia' no tiene fórmula definida", "type": "UNKNOWN", "source": ""}],
            "missing_information": [
                _q("¿Cuál es la fórmula exacta de incidencia?"),
                _q("¿Cuál es la fuente poblacional?"),
                _q("¿Qué definición de caso debe utilizarse?"),
                _q("¿Cuál es el nivel mínimo de agregación?", blocking=False),
                _q("¿Se requiere información histórica?", blocking=False),
            ],
            "suggested_changes": [{"field": "acceptance_criteria", "before": "visualizar incidencia",
                                   "after": "Se muestra la tasa de incidencia por 100.000 habitantes según la fórmula aprobada",
                                   "reason": "Hacer el criterio verificable"}],
        })
    elif pid == "PRJ001" and sid == "US-001" and feedback:
        base.update({"confidence": 0.9, "suggested_changes": [
            {"field": "title", "before": "Visualizar incidencia", "after": "Visualizar tasa de incidencia de dengue",
             "reason": "Mayor precisión"}]})
    elif pid == "PRJ001" and sid == "US-004":
        base.update({"confidence": 0.86, "suggested_changes": [
            {"field": "acceptance_criteria", "before": "supera el umbral", "after": "supera el umbral del canal endémico",
             "reason": "Definir el umbral"}]})
    return base


def architecture(pid, sid) -> dict:
    out = {"story_id": sid, "summary": "Sin impacto relevante.", "impacts": [], "critical_changes": [],
           "recommendations": [], "risks": [], "status": "APPROVED", "requires_hitl": False, "hitl_reason": "",
           "confidence": 0.9}
    if sid == "HU-IA-001":
        out["impacts"] = [{"area": "ai_ml", "description": "Clasificación de coberturas con un modelo entrenado",
                           "type": "INFERENCE", "source": "notas SINCHI"}]
    if pid == "PRJ001" and sid == "US-003":
        out.update({
            "summary": "Requiere migrar datos y retirar la base legada, en conflicto con ADR-001.",
            "impacts": [{"area": "database", "description": "Migración PostgreSQL → BigQuery", "type": "FACT",
                         "source": "ejemplos/projects/proyecto_001/architecture/arquitectura.md"}],
            "critical_changes": ["DATA_MIGRATION", "COMPONENT_REMOVAL"],
            "recommendations": [{"statement": "Mantener la base legada en solo lectura hasta 2027 (ADR-001) y migrar por lotes con validación.",
                                 "type": "RECOMMENDATION", "source": ""}],
            "status": "NEEDS_CHANGES", "requires_hitl": True, "hitl_reason": "Migración y retiro de componente.",
            "confidence": 0.88,
        })
    return out


def qa(pid, sid, feedback: bool) -> dict:
    out = {"story_id": sid, "summary": "Criterios verificables.", "status": "APPROVED", "confidence": 0.93,
           "untestable_criteria": [], "coverage_gaps": [],
           "test_cases": [{"test_id": f"TC-{sid}-01", "title": "Camino feliz", "type": "POSITIVE", "criterion": "criterio",
                           "given": "datos válidos", "when": "el usuario ejecuta la acción", "then": "obtiene el resultado"}]}
    if pid == "PRJ001" and sid == "US-001" and not feedback:
        out.update({"status": "NEEDS_CHANGES", "confidence": 0.75, "summary": "Un criterio no es verificable.",
                    "untestable_criteria": [{"statement": "visualizar incidencia: falta la fórmula", "type": "UNKNOWN", "source": ""}]})
    return out


def generator(pid, previas: bool) -> dict:
    ev = lambda t: {"statement": t, "type": "FACT", "source": "notas SINCHI"}  # noqa: E731
    stories = [
        {"story_id": "HU-IA-001", "title": "Publicar indicadores de coberturas", "epic": "Indicadores",
         "role": "analista ambiental", "need": "publicar mensualmente indicadores de coberturas de la tierra",
         "benefit": "monitorear cambios en la Amazonía", "acceptance_criteria": ["Se publica un indicador por mes"],
         "business_rules": [], "depends_on": [], "evidence": [ev("los indicadores se publican mensualmente")],
         "open_questions": [], "confidence": 0.8, "priority": "Alta",
         "priority_reason": "Los documentos piden publicar cada mes."},
        {"story_id": "HU-IA-002", "title": "Elegir plataforma de procesamiento", "epic": "Infraestructura",
         "role": "líder técnico", "need": "decidir entre Google Earth Engine e infraestructura propia",
         "benefit": "definir la arquitectura del piloto", "acceptance_criteria": ["Queda registrada la decisión"],
         "business_rules": [], "depends_on": [], "evidence": [ev("Definir si la implementación será en Google Earth Engine")],
         "open_questions": [_q("¿Quién toma la decisión de plataforma?", blocking=False)], "confidence": 0.7,
         "priority": "media", "priority_reason": "Sin señales de urgencia."},
    ]
    if previas:
        stories.append({**stories[0], "story_id": "HU-IA-003", "title": "Alertas de deforestación",
                        "need": "recibir alertas de pérdida de bosque", "evidence": [ev("alertas de pérdida")]})
    return {"project_summary": f"Resumen de {pid}", "epics": ["Indicadores", "Infraestructura"], "stories": stories,
            "decisions_found": [ev("el piloto se hará sobre un municipio de la Amazonía")],
            "open_questions": [_q("¿Umbrales de probabilidad por clase?")], "confidence": 0.78}


class ModeloSimulado(BaseLlm):
    model: str = "simulado"

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        si = llm_request.config.system_instruction
        instr = si if isinstance(si, str) else " ".join(p.text or "" for p in (si.parts if si else []))
        sid = (re.search(r'"story_id":\s*"([^"]+)"', instr) or [None, ""])[1]
        pid = (re.search(r'"project_id":\s*"([^"]+)"', instr) or [None, ""])[1]
        feedback = "Corrección humana" in instr
        if "USER_STORY_GENERATOR_AGENT" in instr:
            pid = (re.search(r"PROYECTO: (\S+)", instr) or [None, ""])[1]
            agente, data, sid = "story_generator_agent", generator(pid, "Historias generadas previamente" in instr), ""
        elif "USER_STORY_ANALYST_AGENT" in instr:
            agente, data = "story_analyst_agent", analyst(pid, sid, feedback)
        elif "ARCHITECTURE_AGENT" in instr:
            agente, data = "architecture_agent", architecture(pid, sid)
        elif "QA_TEST_AGENT" in instr:
            agente, data = "qa_agent", qa(pid, sid, feedback)
        else:
            raise AssertionError("agente desconocido")
        LLAMADAS.append({"agent": agente, "project_id": pid, "story_id": sid, "instruction": instr})
        pendientes = FALLAS.get((agente, sid))
        if pendientes:
            raise pendientes.pop(0)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(data, ensure_ascii=False))]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=len(instr) // 4, candidates_token_count=200, total_token_count=len(instr) // 4 + 200),
        )
