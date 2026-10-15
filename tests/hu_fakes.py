"""Compatibilidad: el modelo simulado vive en ``hu_multiagent.simulado`` (también sirve al modo demo)."""

from hu_multiagent.simulado import (  # noqa: F401
    FALLAS,
    LLAMADAS,
    ModeloSimulado,
    analyst,
    architecture,
    estimation,
    generator,
    qa,
)
