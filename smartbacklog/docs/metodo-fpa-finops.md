# Método de dimensionamiento: puntos de función (FPA) + FinOps de generación autónoma

Etapa 3 del pipeline. Reemplaza la estimación por story points (`ia_factores_v1` / `fibonacci_tradicional_v1`).
Responde tres preguntas por HU y para todo el backlog:

1. **¿Qué tan grande es el software?** Tamaño funcional objetivo en puntos de función IFPUG sin ajustar (UFP).
2. **¿Qué restricciones lo condicionan?** Tamaño no funcional por las restricciones del proyecto (seguridad,
   privacidad, rendimiento...) que la HU activa, y las que **viola**, a partir de un grafo de contexto aprobado.
3. **¿Cuánto cuesta que un agente de IA lo construya solo?** Huella de contexto del repositorio, tokens, costo en
   USD, «impuesto de contexto» y horas de reloj de un ciclo de generación 100 % autónomo.

Implementación:

| Pieza | Archivo | Método |
|---|---|---|
| Cálculo de puntos de función (puro) | `app/services/fpa.py` | `fpa_ifpug_ufp_v1` |
| Agentes concepto → clasificación (ADK) | `app/agents/providers/adk.py::size_story`, `app/agents/prompts.py` | ConceptAct en dos pasos |
| Modelo FinOps (puro) | `app/services/finops.py` | `finops_context_tax_v1` |
| Tabla de precios | `app/services/finops_pricing.py` | versionada con fecha |
| Grafo de contexto (puro) | `app/services/context_graph.py` | validación y consultas |
| Ciclo de vida del grafo (HITL) | `app/services/context.py`, `app/routers/context.py` | borrador → aprobado |
| Tamaño no funcional (puro) | `app/services/nfr.py` | `nfr_snap_lite_v1` |
| Ejecución y trazabilidad | `app/agents/runner.py::execute_sizing` | `agent_runs` + historial |

---

## 1. Problema

Los story points miden esfuerzo humano relativo y no se pueden convertir en costo de cómputo. Cuando el código lo
genera un agente, el costo depende de otra cosa: cuántos tokens lee y escribe en cada vuelta de su ciclo. Y lo que
lee crece: cada HU construida agrega código al repositorio que el agente vuelve a leer en las HU siguientes.
Un número de puntos no ve ese efecto acumulado.

## 2. Fuentes y verificación

| Fuente | Qué aporta | Verificación |
|---|---|---|
| Zhao et al. *Emergence of A Novel Domain Expert: A Generative AI-based Framework for Software Function Point Analysis*. ASE 2024 (39th IEEE/ACM ASE), NIER track. | FPA-EX: LLM + *Densely Supervised Fine-Tuning* (DSFT) + *ConceptAct Prompting* (CAP) para extraer puntos de función de especificaciones. Dataset de 778 pares anotados; F1 0.491 mayor que GPT-3.5. | Confirmado en la página del congreso (conf.researchr.org, ASE 2024). **Es de 2024, no de 2025.** La afirmación «5× de productividad» **no aparece** en el resumen: no se usa. |
| He et al. *Token Economics for LLM Agents: A Dual-View Study from Computing and Economics*. arXiv 2605.09104 (2026). | Tokens como factor de producción, medio de intercambio y unidad de cuenta; costo frente a calidad y pasos de razonamiento. | Confirmado (arXiv). Es una **revisión (survey)**, no un marco formal validado: se usa como marco conceptual. |
| FinOps Foundation. *GenAI FinOps vs. Cloud FinOps: Similar Roots, Different Challenges* (2026). | El costo de GenAI es por consumo y no determinista; seguir la tasa de consumo de tokens; la retención de contexto es variable de costo. | Confirmado (finops.org). El título es «**Similar** Roots», no «Different Roots». |
| Revefi. *FinOps for AI: Managing LLM and AI Agent Costs* (2026). | Costo no determinista de agentes en ciclos ReAct; trazabilidad de extremo a extremo; herramientas, caché y razonamiento oculto alteran el precio plano. | Confirmado como **blog de un proveedor** (revefi.com). Contexto, no evidencia. |
| *How Do AI Agents Spend Your Money? Analyzing and Predicting Token Consumption in Agentic Coding Tasks* (Microsoft Research / Stanford Digital Economy Lab). | Consumo de tokens real de agentes de código. | Encontrado, **no leído en detalle**: fuente de calibración pendiente. No se citan cifras suyas hasta revisarlo. |
| IFPUG CPM / ISO/IEC 20926. | Tipos de función, matrices de complejidad y pesos. | Estándar. |
| IFPUG SNAP (*Software Non-functional Assessment Process*). | Medir los requisitos no funcionales aparte de los puntos de función. | Estándar. Aquí solo se toma la idea de medirlos aparte: el método `nfr_snap_lite_v1` **no** implementa las categorías ni las fórmulas de SNAP. |

### Qué se toma de FPA-EX y qué no

- **Sí:** la separación *Concept → Act*. Un agente identifica conceptos (entidades y transacciones) con evidencia; otro
  los clasifica y cuenta. Es la parte de FPA-EX que se puede reproducir con prompts.
- **No:** el ajuste fino DSFT (requiere el dataset y entrenamiento). Por eso este método es **inspirado en FPA-EX**,
  no FPA-EX. La calidad esperada es menor que la del artículo hasta calibrar con datos propios (sección 9).

## 3. Puntos de función (`fpa_ifpug_ufp_v1`)

El agente (o una persona) entrega **componentes**; el código calcula complejidad y puntos. El LLM nunca hace aritmética.

| Tipo | Qué es | Conteos |
|---|---|---|
| ILF | Grupo de datos lógico mantenido por la aplicación | DET, RET |
| EIF | Grupo de datos que la aplicación lee y mantiene otro sistema (incluido un servicio externo de IA) | DET, RET |
| EI | Proceso que crea, modifica o elimina datos, o recibe datos de fuera | DET, FTR |
| EO | Salida con cálculo o datos derivados | DET, FTR |
| EQ | Consulta sin cálculo | DET, FTR |

**Complejidad** (fila = RET o FTR, columna = DET):

| | ILF/EIF: DET 1–19 | 20–50 | 51+ | | EI: DET 1–4 | 5–15 | 16+ | | EO/EQ: DET 1–5 | 6–19 | 20+ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RET 1 / FTR 0–1 (EI) / FTR 0–1 (EO/EQ) | Baja | Baja | Media | | Baja | Baja | Media | | Baja | Baja | Media |
| RET 2–5 / FTR 2 (EI) / FTR 2–3 (EO/EQ) | Baja | Media | Alta | | Baja | Media | Alta | | Baja | Media | Alta |
| RET 6+ / FTR 3+ (EI) / FTR 4+ (EO/EQ) | Media | Alta | Alta | | Media | Alta | Alta | | Media | Alta | Alta |

**Pesos** (baja / media / alta): ILF 7/10/15 · EIF 5/7/10 · EI 3/4/6 · EO 4/5/7 · EQ 3/4/6. `UFP = Σ pesos`.
No se aplica el factor de ajuste (VAF), que IFPUG dejó como opcional.

**Entidades compartidas entre HU.** Una ILF/EIF se cuenta una sola vez por aplicación. Las HU se dimensionan en orden
de generación y cada una recibe el **registro** de entidades ya contadas. Las que coinciden llegan con `reused=true`
y suman 0 (siguen siendo FTR de las transacciones). El servidor lo hace cumplir aunque el modelo lo olvide: compara
nombres sin mayúsculas, tildes ni espacios extra.

## 4. Agentes (ADK)

`SequentialAgent("fpa_pipeline")` con dos `LlmAgent`, ambos con `output_schema`:

1. `fpa_concept_agent` (`FPA_CONCEPT_INSTRUCTION`): conceptos `entidad`/`transaccion` con evidencia. Guarda el
   resultado en el estado de la sesión (`output_key="fpa_concepts"`).
2. `fpa_act_agent` (`FPA_ACT_INSTRUCTION`): recibe `{fpa_concepts}` en su instrucción y devuelve funciones con
   tipo, DET, RET/FTR, `reused` y una justificación por función.

Las respuestas se validan con los DTO estrictos de `app/agents/ports.py` (`SizingOutput`); una respuesta inválida
(por ejemplo `det = 0`) falla la ejecución sin guardar nada. Los tokens de los dos agentes se suman en `agent_runs`.
El proveedor `stub` aplica una heurística determinista (una ILF por épica, una transacción por criterio) para
pruebas y demos sin Gemini.

## 4b. Grafo de contexto, restricciones y tamaño no funcional

Los puntos de función miden funcionalidad, no restricciones. Sin contexto, además, cada HU se mide aislada: el
agente no sabe qué existe ya ni qué restricciones del proyecto aplican. El grafo de contexto cubre ambas cosas.

### Grafo

| Nodo | Atributos | Para qué sirve |
|---|---|---|
| `feature` (funcionalidad) | `status`: existente o planificado | Qué toca la HU; lo existente es reuso |
| `entity` (entidad de datos) | `status`: existente o planificado | Las entidades existentes entran al registro de FPA: suman 0 |
| `constraint` (restricción) | `category` (seguridad, privacidad, rendimiento, disponibilidad, cumplimiento, integracion, costo, otro), `severity` (baja, media, alta, critica), `rule`, `keywords` | Qué debe cumplir la HU y qué incumple |

Aristas: `depends_on` (feature → feature), `uses` (feature → entity), `applies_to` (constraint → feature o entity).
`context_graph.validate` rechaza ids repetidos o inválidos, valores fuera de las listas, aristas a nodos que no
existen y aristas entre tipos incompatibles.

### Ciclo HITL del grafo

1. `POST /projects/{key}/agents/extract-context`: el agente arquitecto lee los documentos del proyecto (carpeta o
   bucket, con las mismas herramientas de solo lectura del agente de documentos) y propone el grafo. Se guarda como
   **borrador** (versión nueva); un borrador anterior sin revisar queda reemplazado.
2. Una persona lo revisa: `GET /projects/{key}/context`. Puede corregirlo (`PUT /projects/{key}/context/draft`) o
   crearlo a mano sin agente.
3. Una persona con rol de escritura lo **aprueba** (`.../context/draft/approve`) o lo rechaza. Una API key de
   servicio no puede aprobar. La aprobada anterior pasa a `superseded`. Ninguna versión se borra y cada acción queda
   en su `log`.

El dimensionamiento usa **solo la última versión aprobada**. Sin grafo aprobado, el dimensionamiento funciona igual
que antes, sin análisis de contexto.

### Agente de contexto en el dimensionamiento

Con un grafo aprobado, el `SequentialAgent` del dimensionamiento antepone un tercer agente:

```
context_enricher_agent  → informe: funcionalidades tocadas, existentes reusadas, restricciones activadas y violadas
fpa_concept_agent       → conceptos
fpa_act_agent           → funciones IFPUG (recibe el informe: lo existente es reused; las NFR no se cuentan como FP)
```

El servidor no confía en el informe tal cual: descarta ids que no estén en el grafo (`unknown_ids`), toma la
**severidad del grafo** (no del modelo) y siembra el registro de FPA con las entidades existentes del grafo.

### Tamaño no funcional (`nfr_snap_lite_v1`)

Por cada restricción activada (una violada también cuenta como activada), una sola vez por HU:

| Severidad | baja | media | alta | critica |
|---|---|---|---|---|
| Puntos NFR | 2 | 4 | 7 | 10 |

`nfr_points` es la suma. Es un supuesto inicial, calibrable como el resto (sección 9).

### Violaciones (HITL)

Una violación es una restricción que la HU, **tal como está escrita**, incumple (por ejemplo, enviar perfiles de
usuarios a una API externa cuando una restricción de privacidad lo prohíbe). Queda en `context_analysis.violations`
con la regla y el motivo, y en el historial (`constraint_violations`).

- **Bloquea la etapa 3:** no se aprueba mientras alguna HU activa tenga una violación abierta.
- Se cierra de dos formas: **cambiar la HU** y volver a dimensionarla (si ya no incumple, la violación desaparece),
  o registrar una **excepción** justificada (`POST /projects/{key}/stories/{code}/violations/{constraint_id}/waive`,
  mínimo 10 caracteres). Solo una persona registra excepciones; queda en el historial (`violation_waived`).
- Una excepción se conserva al volver a dimensionar la HU si la misma restricción sigue violada.

## 5. Modelo FinOps (`finops_context_tax_v1`)

Entrada: las HU activas (no descartadas) con sus UFP, en **orden de generación** = prioridad (vacías al final) y id.
Las HU sin puntos se listan pero no se costean.

Para la HU *k*, con el repositorio en `C_k` tokens:

| Magnitud | Fórmula |
|---|---|
| Código generado | `code_k = UFP_k · loc_per_fp · tokens_per_loc · (1 + test_ratio) + NFR_k · nfr_tokens_per_point` |
| Huella de contexto | `C_1 = base_context_tokens`; `C_{k+1} = C_k + code_k` |
| Iteraciones | `N_k = ⌈(base_iterations + iterations_per_fp · UFP_k + iterations_per_nfr_point · NFR_k) · m_escenario · (ia_iteration_factor si HU_IA)⌉` |
| Especificación | `spec_k = spec_base_tokens + spec_tokens_per_fp · UFP_k` |
| Contexto leído por iteración | `read_k = min(context_read_ratio · C_k, ventana − reserva − spec_k − tool_out)`; si se acota, `exceeds_window` |
| Prefijo estable | `P_k = read_k + spec_k` |
| Escritura de caché | `P_k` (1ª iteración) |
| Lectura de caché | `(N_k − 1) · cache_hit_ratio · P_k` |
| Entrada sin caché | `(N_k − 1) · (1 − cache_hit_ratio) · P_k + N_k · tool_output_tokens_per_iteration` |
| Salida | `code_k · (1 + reasoning_ratio) + N_k · output_overhead_per_iteration` (el razonamiento se cobra como salida) |
| Costo | `Σ tokens · precio / 10⁶` (tabla de precios; con umbral de contexto largo si el modelo lo tiene) |
| **Impuesto de contexto** | `costo_k − costo_k con C_k = base_context_tokens` |
| Tiempo | `N_k · (ttft_s + tool_seconds_per_iteration) + salida_k / output_tps` |

El impuesto de la primera HU es 0. Como `C_k` crece de forma lineal con las HU anteriores, el impuesto por HU crece
lineal y el **total crece de forma superlineal** (~cuadrática): duplicar el backlog más que duplica el impuesto.

**Escenarios:** bajo, esperado y alto multiplican las iteraciones por `scenario_low` (0.5), 1 y `scenario_high` (2).
Se reporta además la media PERT `(bajo + 4·esperado + alto) / 6` del costo y del tiempo.

### Supuestos por defecto

Son **hipótesis iniciales, no medidas**. Todos se pueden cambiar por petición y vuelven en `params` del resultado.

| Parámetro | Defecto | Parámetro | Defecto |
|---|---|---|---|
| `loc_per_fp` | 50 | `base_iterations` | 6 |
| `tokens_per_loc` | 10 | `iterations_per_fp` | 0.4 |
| `test_ratio` | 0.5 | `ia_iteration_factor` | 1.5 |
| `base_context_tokens` | 20 000 | `reasoning_ratio` | 1.0 |
| `spec_base_tokens` | 1 000 | `output_overhead_per_iteration` | 500 |
| `spec_tokens_per_fp` | 150 | `cache_hit_ratio` | 0.8 |
| `context_read_ratio` | 0.3 | `output_tps` | 60 |
| `tool_output_tokens_per_iteration` | 2 000 | `ttft_s` | 2 |
| `context_headroom_tokens` | 64 000 | `tool_seconds_per_iteration` | 20 |
| `iterations_per_nfr_point` | 0.3 | `nfr_tokens_per_point` | 750 |

### Precios

`app/services/finops_pricing.py`, USD por millón de tokens, al **2026-10-06** (precios de primera parte de Anthropic):

| Modelo | Entrada | Salida | Lectura de caché | Escritura de caché |
|---|---|---|---|---|
| Claude Opus 5.5 (`claude-opus-5-5`, defecto) | 4.00 | 20.00 | 0.20 | 5.00* |
| Claude Sonnet 5.5 (`claude-sonnet-5-5`) | 2.00 | 10.00 | 0.20 | 2.50* |
| Claude Haiku 5.5 (`claude-haiku-5-5`), hasta 100K de entrada | 0.10 | 0.50 | 0.01* | 0.125* |
| Claude Haiku 5.5, más de 100K de entrada | 0.50 | 2.50 | 0.05* | 0.625* |

\* Derivado con los multiplicadores estándar de prompt caching (escritura 1.25×, lectura 0.1× de la entrada).
Se marca en `prices.derived`. El brief original citaba Claude 3.5 Sonnet / Opus: esa generación está retirada.
Verificar precios antes de presupuestar; cualquiera se sobrescribe con `price_input`, `price_output`,
`price_cache_read` y `price_cache_write`.

## 6. Ejemplo trabajado

Una HU de 20 FP, primera del backlog, con los supuestos por defecto y Claude Opus 5.5
(es el caso de `tests/test_finops.py::test_first_story_matches_hand_calculation`):

- spec = 1 000 + 150·20 = 4 000; contexto leído = 0.3 · 20 000 = 6 000; prefijo = 10 000.
- iteraciones = ⌈6 + 0.4·20⌉ = 14.
- caché: escritura 10 000; lectura 13 · 0.8 · 10 000 = 104 000; sin caché 13 · 0.2 · 10 000 + 14 · 2 000 = 54 000.
- código = 20 · 50 · 10 · 1.5 = 15 000 tokens; salida = 15 000 · 2 + 14 · 500 = 37 000.
- costo = (54 000·4 + 104 000·0.2 + 10 000·5 + 37 000·20) / 10⁶ = **USD 1.0268**; impuesto 0 (primera HU).
- tiempo = 14 · 22 s + 37 000 / 60 s ≈ **15.4 min**.

La HU siguiente parte de un repositorio de 35 000 tokens (20 000 + 15 000) y paga impuesto de contexto.

## 7. HITL

- Los agentes **proponen**: guardan componentes y puntos, con historial `agent:<proveedor>` y la justificación
  (`sizing_rationale`). No cambian el estado de la HU ni aprueban etapas.
- Un humano **corrige** con `PATCH /api/v1/projects/{key}/stories/{code}` y `fp_components`; el servidor recalcula y
  deja el diff en el historial.
- Un humano con rol de escritura **aprueba** la etapa 3 («Estimación») después de la 2. Una API
  key de servicio no puede aprobar. La aprobación se **bloquea** mientras haya restricciones violadas abiertas
  (sección 4b); no se bloquea por HU sin dimensionar.
- El grafo de contexto lo aprueba una persona antes de que el dimensionamiento lo use (sección 4b).

## 8. Aplicación en la app

| Dónde | Qué hace |
|---|---|
| `PATCH /api/v1/projects/{key}/stories/{code}` | `fp_components` → `function_points` y `fp_analysis`. 422 si un componente es inválido. |
| `POST /api/v1/projects/{key}/stories/{code}/agents/size` | Dimensiona una HU (run `size_story`). |
| `POST /api/v1/projects/{key}/agents/size-backlog` | Dimensiona en orden las HU sin puntos; `{"resize": true}` recalcula todas (run `size_backlog`). Una sola transacción. |
| `GET /api/v1/projects/{key}/finops` | Modelo FinOps por HU y total. Parámetros: `model`, `scenario` y cualquier supuesto. |
| `GET /api/v1/projects/{key}/finops/export?format=csv\|json` | Exportación. |
| `GET /api/v1/finops/models` | Tabla de precios con fecha y fuente. |
| Ingesta (`/ingest/projects`, `stories`, `stories:bulk`) | Aceptan `fp_components`. |
| `POST /api/v1/projects/{key}/agents/extract-context` | El agente propone el grafo de contexto (run `extract_context`); queda como borrador. |
| `GET /api/v1/projects/{key}/context` · `GET .../context/versions/{n}` | Grafo aprobado, borrador e historial. |
| `PUT /api/v1/projects/{key}/context/draft` | Edición humana del borrador (o creación a mano). |
| `POST /api/v1/projects/{key}/context/draft/approve` · `.../reject` | Revisión humana (una API key de servicio recibe 403). |
| `POST /api/v1/projects/{key}/stories/{code}/violations/{constraint_id}/waive` | Excepción justificada a una violación. |
| Pantallas | Ficha de HU: tabla de funciones, contexto, restricciones, violaciones con «Registrar excepción» y «Medir con agente». Reportes: grafo de contexto (extraer, aprobar, rechazar), violaciones abiertas, tabla FinOps con NFR y «Dimensionar HU pendientes». Backlog y tablero muestran FP. |

## 9. Calibración

1. **Tamaño:** comparar los FP del agente con un conteo de un analista certificado en una muestra de HU; ajustar
   prompts (o, más adelante, entrenar) si el error es sistemático por tipo de función.
2. **Consumo:** ejecutar el agente de código real sobre HU dimensionadas y registrar tokens de entrada, caché y
   salida, iteraciones y tiempo. Ajustar `loc_per_fp`, `tokens_per_loc`, `iterations_per_fp`, `context_read_ratio`,
   `cache_hit_ratio` y `reasoning_ratio` para minimizar el error. Contrastar con el estudio de Microsoft / Stanford.
3. Cambiar los valores por defecto o las fórmulas **solo** creando una versión nueva del método
   (`finops_context_tax_v2`, `fpa_ifpug_ufp_v2`) y actualizando este documento.

## 10. Limitaciones

- Los supuestos de la sección 5 no están calibrados: las cifras de costo son orden de magnitud, no presupuesto.
- El modelo de ciclo es simple: no acumula la conversación dentro de la HU (solo el prefijo estable se relee) ni
  modela compactación, recuperación selectiva, reintentos por fallos ni ejecución en paralelo.
- Los puntos de función miden funcionalidad. Los requisitos no funcionales se miden aparte con
  `nfr_snap_lite_v1`, que es una simplificación (un puntaje por severidad), no SNAP. El trabajo de experimentación
  de una HU-IA lo aproxima `ia_iteration_factor`.
- El grafo de contexto solo es tan bueno como los documentos y la revisión humana: una restricción que nadie
  documentó no se detecta. El agente puede no ver una violación sutil; por eso la etapa 3 la revisa una persona.
- El conteo depende del texto de la HU: una HU pobre produce un conteo pobre. Por eso la revisión humana (sección 7).
- Los precios cambian: verificar `prices.as_of` antes de usar las cifras.
