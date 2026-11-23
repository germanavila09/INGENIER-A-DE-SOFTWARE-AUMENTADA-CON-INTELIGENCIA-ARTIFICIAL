# SmartBacklogIA: cambios implementados y guía de pruebas

Para el equipo que va a probar la versión de `smartbacklog/`. Instalación y usuarios: [windows/LEEME.md](../windows/LEEME.md).

## Enlaces

| Qué | Dónde |
|---|---|
| Método de estimación (documento) | [docs/metodo_estimacion.md](../../docs/metodo_estimacion.md) |
| Método de estimación (página interactiva con calculadora) | [docs/estimacion_hu.html](../../docs/estimacion_hu.html): descárgala y ábrela en el navegador |
| Política HITL en código | [app/services/hitl.py](../app/services/hitl.py) |
| Cálculo de la estimación | [app/services/estimation.py](../app/services/estimation.py) |
| Puntos de función y FinOps | [metodo-fpa-finops.md](metodo-fpa-finops.md) |
| Cambios en GitHub | PR [#1](https://github.com/germanavila09/INGENIER-A-DE-SOFTWARE-AUMENTADA-CON-INTELIGENCIA-ARTIFICIAL/pull/1) (código) y [#3](https://github.com/germanavila09/INGENIER-A-DE-SOFTWARE-AUMENTADA-CON-INTELIGENCIA-ARTIFICIAL/pull/3) (README) |

## Qué cambió

Base: [SmartBacklogIA](https://github.com/diegodgt02-ai/SmartBacklogIA) (Diego Carabali y equipo). Sobre ella:

1. **Revisión humana (HITL) como función central.** Cada HU pasa por cuatro agentes: el analista (INVEST,
   ambigüedades, preguntas, versión mejorada) y luego, en paralelo, arquitectura (impactos y cambios críticos),
   QA (casos Dado/Cuando/Entonces, criterios no verificables) y estimación (tipo y factores).
2. **Nivel de riesgo calculado en código, no por el modelo.**

   | Nivel | Cuándo | Quién decide |
   |---|---|---|
   | 2 · Requiere al product owner | cambio crítico (migración, seguridad, API pública, costo…), conflicto entre agentes, pregunta bloqueante, sin criterios o mayoría no verificable, confianza < 0,60, un agente sin respuesta, HU ya aprobada que cambia | solo product owner (o admin) |
   | 1 · Revisar | confianza 0,60–0,85, algún criterio no verificable, mejoras de redacción, HU generada por IA, spike, estimación incompleta | product owner o scrum master |
   | 0 · Sin observaciones | nada de lo anterior | product owner o scrum master |

3. **Decisiones:** aprobar (con o sin la propuesta del analista), pedir cambios (el agente refina con tu comentario
   y vuelve a revisar sola), falta información, descartar y comentar. Comentar lo puede hacer cualquier miembro,
   incluidos developer y stakeholder. Nada se aprueba solo, y una API key de servicio nunca decide.
4. **Bandeja «Revisión humana»** con contador en el menú, ordenada por riesgo, y panel de revisión en el detalle
   de cada HU (lo que dijo cada agente e historial de decisiones).
5. **Etapa 1 bloqueada** hasta que todas las HU activas estén aprobadas en la revisión humana.
6. **Vista «Estimación»** (etapas 3 y 4): puntos de historia por factores (`fibonacci_tradicional_v1`,
   `ia_factores_v1`) junto a puntos de función IFPUG y tamaño no funcional, explicación del método y prioridad
   editable en bloque.
7. **La revisión sigue vigente** mientras no cambie el contenido de la HU (título, rol, necesidad, beneficio,
   criterios). Cambiar estado, prioridad, asignación o tamaño no la reabre; editar el contenido sí.
8. **Modo local sin GCP:** Postgres en Docker, documentos en `documents/<CLAVE>/` y Gemini con API key.
   Scripts de Windows en `windows/`.

## Qué probar

Usa el proyecto demo **SPRINT** (documentos de `docs_ejemplo`) y los usuarios de prueba. Marca cada punto:

- [ ] **Generación:** Agentes IA → «Generar HU desde todos los documentos» crea HU en borrador con su fuente.
- [ ] **Revisión:** Revisión humana → «Revisar con agentes». Cada HU queda con nivel, motivos, preguntas y propuesta.
- [ ] **Permisos por nivel:** con `sm@spb.local`, una HU de nivel 2 no muestra «Aprobar» y la API responde 403;
      una de nivel 0 o 1 sí se puede aprobar.
- [ ] **Solo comentar:** con `dev@spb.local` o `stake@spb.local` solo aparece «Comentar».
- [ ] **Aprobar con la propuesta:** la HU cambia a la versión del analista y pasa a «Refinada».
- [ ] **Pedir cambios:** sin comentario da error; con comentario, el agente refina y la HU vuelve a la bandeja con una revisión nueva.
- [ ] **Falta información:** la HU queda esperando; un comentario de otro miembro y «Revisar de nuevo» deberían
      bajar el nivel si respondía la pregunta bloqueante.
- [ ] **Descartar:** exige motivo.
- [ ] **Etapa 1:** el dashboard no la deja aprobar mientras quede alguna HU sin aprobar; luego sí.
- [ ] **Editar una HU aprobada** (título o criterios): su revisión queda en «Cambió desde la revisión» y vuelve a la cola de agentes.
- [ ] **Priorizar:** en Estimación, cambiar prioridades no reabre la revisión.
- [ ] **Estimación:** los puntos coinciden con la calculadora (`docs/estimacion_hu.html`) para los mismos factores.
- [ ] **Etapas 3 a 5:** «Dimensionar backlog» llena los puntos de función; aprobar etapas 3, 4 y 5; exportar en Reportes.

## Ten en cuenta

- **Cada probador usa su propia API key** de Gemini y su propia base de datos: las pruebas de uno no se ven en la
  de otro. Para probar juntos, una persona levanta la app y crea usuarios para las demás («Usuarios y API keys»
  y «Miembros»).
- **API key gratuita:** Google puede usar lo que se envía para mejorar sus productos. Usa los documentos de
  ejemplo, no documentos reales de clientes.
- **Límites de uso:** cada HU usa cuatro agentes; con la clave gratuita puedes llegar al límite por minuto. Si una
  HU queda en «La revisión falló», espera un minuto y pulsa «Revisar» (bandeja) o «Revisar de nuevo» (detalle).
- **Los resultados de los agentes varían** entre ejecuciones: el nivel o la estimación de una misma HU pueden
  cambiar. La política y los cálculos son deterministas; lo que varía es lo que responde el modelo.
- **Documentos:** van en `smartbacklog/documents/<CLAVE>/`, con la clave del proyecto en mayúsculas. Formatos:
  PDF con texto, DOCX, BPMN, MD y TXT.
- **Contraseñas de prueba** (`cambiar-me-ya`, `demo-spb-2026`): son públicas. Cámbialas si abres tu instancia a otros.
- **La versión en Cloud Run** es la integración anterior y no tiene estos cambios.
- **Si algo falla**, abre un issue en GitHub con: pasos, usuario y rol, código de la HU, captura y la salida de
  `docker compose -p spb logs --tail 80 app` (sin la API key).
