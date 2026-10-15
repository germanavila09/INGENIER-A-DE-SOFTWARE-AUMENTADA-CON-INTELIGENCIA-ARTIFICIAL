# Método de estimación de historias (HU-IA y HU-Tradicional)

> **Origen.** El método se adoptó del proyecto [SmartBacklogIA](https://github.com/diegodgt02-ai/SmartBacklogIA)
> (`docs/metodo-estimacion-hu-ia.md` y `app/services/estimation.py`). Aquí se resume tal como quedó
> integrado en el orquestador sobre GCP. Las referencias son las que cita ese documento.

## 1. Por qué un método por factores

Una historia que construye o ajusta un componente de IA/ML (HU-IA) mezcla tres fuentes de esfuerzo que un
único número de story points esconde:

- **Datos:** conseguirlos, limpiarlos y etiquetarlos.
- **Incertidumbre del resultado:** el modelo puede no alcanzar la precisión esperada.
- **Trabajo tradicional alrededor:** API, interfaz, seguridad y auditoría.

Por eso el método separa factores, calcula los puntos con una fórmula fija y expresa la incertidumbre de dos
maneras: un **rango** y, cuando hace falta, un **spike** (investigación con tiempo acotado).

## 2. Quién puntúa y quién calcula

- **`estimation_agent`** (Gemini, en paralelo con arquitectura y QA) decide el tipo (`HU_IA` o
  `HU_TRADICIONAL`) con una justificación y puntúa cada factor con un entero de 1 a 5.
- **El código** (`src/hu_multiagent/services/estimation.py`) calcula los puntos, el rango y el spike.
  Los mismos factores siempre dan el mismo resultado, y el modelo no puede proponer un número fuera del método.
- **Una persona** puede ajustar el tipo y los factores desde el SPB (botón «Estimar» o tabla de Validación).
  Su estimación prevalece sobre la del agente y queda en la versión `_human_estimate` y en el evento
  `human_estimate` de la auditoría, junto con la del agente para comparar.

## 3. Factores y pesos

| Tipo | Método | Factores (peso) |
|---|---|---|
| HU-Tradicional | `fibonacci_tradicional_v1` | complejidad (0.40) · esfuerzo (0.40) · incertidumbre (0.20) |
| HU-IA | `ia_factores_v1` | datos (0.25) · modelo (0.25) · evaluación (0.15) · riesgo (0.15) · integración (0.20) |

Escalas (1 = mínimo, 5 = máximo):

- **complejidad:** reglas de negocio, pantallas o flujos, casos de error.
- **esfuerzo:** construcción e integración (APIs, datos, pruebas).
- **incertidumbre:** qué tan claro está el requisito.
- **datos:** disponibilidad y calidad para entrenar o evaluar.
- **modelo:** complejidad del modelo o del prompt.
- **evaluación:** necesidad de validar con métricas o un conjunto de referencia.
- **riesgo:** probabilidad de no alcanzar el resultado (precisión, alucinaciones, sesgo).
- **integración:** trabajo tradicional alrededor del modelo.

## 4. Cálculo

1. **Puntaje bruto** = suma ponderada de los factores (entre 1 y 5).
2. **Nivel** en la escala de Fibonacci (1, 2, 3, 5, 8, 13). La escala [1, 5] se lleva linealmente al
   índice [0, 5], redondeando .5 hacia arriba: bruto 1 → 1 punto, bruto 5 → 13 puntos.
3. **Ajuste por riesgo** (solo HU-IA): si `riesgo ≥ 4`, sube un nivel (máximo 13).
4. **Rango:** un nivel abajo y uno arriba del resultado (estimación de tres puntos).
5. **Spike** (solo HU-IA): si `datos ≥ 4` o `riesgo ≥ 4`, se requiere un spike de **2 puntos**, que se
   cuenta aparte de la historia y debe terminar en una decisión (seguir, cambiar de enfoque o descartar).

Una HU-Tradicional nunca lleva spike: un requisito ambiguo se refleja en una `incertidumbre` alta y se resuelve
refinando la historia.

## 5. Ejemplos (calculados con el código)

| Caso | Factores | Bruto | Resultado |
|---|---|---|---|
| A · HU-IA con datos por conseguir | datos 4, modelo 4, evaluación 3, riesgo 4, integración 2 | 3.45 | 5 pts → riesgo 4 sube a **8 pts**, rango 5–13, **spike de 2 pts** |
| B · HU-IA acotada | datos 2, modelo 2, evaluación 2, riesgo 2, integración 3 | 2.20 | **3 pts**, rango 2–5, sin spike |
| C · HU-Tradicional poco clara | complejidad 3, esfuerzo 3, incertidumbre 4 | 3.20 | **5 pts**, rango 3–8, sin spike |

## 6. Relación con el control humano (HITL)

La estimación es **informativa**: no baja la confianza agregada, no genera conflictos entre agentes y, si el
agente no devuelve salida, la historia queda «sin estimar» sin detener el flujo. Solo agrega motivos de
revisión de nivel 1:

- `SPIKE_REQUIRED`: HU-IA con datos o riesgo ≥ 4.
- `ESTIMATION_INCOMPLETE`: el agente devolvió factores fuera de rango o incompletos. No se corrigen en
  silencio; una persona los completa.

## 7. Dónde queda

- `generated/user_stories/<ID>/estimation.json`: salida del agente, estimación calculada y error, si lo hubo.
- `proposed.json` y `approved.json`: la estimación vigente (en la aprobada, la de la persona si la ajustó).
- `state/project_state.json` → `stories.<ID>.human_estimate`.
- Backlog del SPB y exportación CSV: columnas `puntos`, `rango_puntos`, `spike_puntos`, `metodo_estimacion`,
  `estimacion_por`.

## 8. Límites y calibración

- No existe un método estándar y validado de story points para ML. Este es una **propuesta justificada y
  calibrable**, no un valor empírico.
- El spike de 2 puntos es una convención, no una medida de duración.
- Para calibrar: comparar los puntos estimados con el esfuerzo real de varias iteraciones y ajustar pesos o
  umbrales. Cualquier cambio de pesos o escala exige un nuevo sufijo de versión del método (`_v2`), para que las
  estimaciones guardadas sigan siendo interpretables.

## 9. Referencias (citadas por SmartBacklogIA)

- Amershi, S. et al. (2019). *Software Engineering for Machine Learning: A Case Study*. ICSE-SEIP 2019. Sustenta
  los factores de datos e integración.
- Sculley, D. et al. (2015). *Hidden Technical Debt in Machine Learning Systems*. NIPS 2015. Sustenta el factor
  de integración: gran parte del costo está fuera del modelo.
- Cohn, M. (2005). *Agile Estimating and Planning*. Prentice Hall. Base de los story points y de la estimación
  relativa.
- Estimación de tres puntos (PERT, Malcolm et al., 1959). Sustenta el rango.
- Chapman, P. et al. (2000). *CRISP-DM 1.0*. Sustenta el factor de evaluación.
- Práctica de spikes (Beck, *Extreme Programming Explained*, 1999). Sustenta el spike cuando hay riesgo o datos
  incompletos.
