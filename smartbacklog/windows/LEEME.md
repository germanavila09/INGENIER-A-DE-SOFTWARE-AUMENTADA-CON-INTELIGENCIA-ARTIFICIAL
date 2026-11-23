# SmartBacklogIA en tu computador (Windows)

Corre todo en local: Postgres en Docker, documentos en `smartbacklog\documents\<CLAVE>\` y Gemini con tu
propia API key. No necesita GCP, bucket ni permisos de IAM.

## Requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado.
- [Git](https://git-scm.com/download/win) para clonar el repositorio (o descarga el ZIP desde GitHub).
- Una API key de Gemini: https://aistudio.google.com/apikey (cada probador usa la suya).

## Primera vez

```powershell
git clone https://github.com/germanavila09/INGENIER-A-DE-SOFTWARE-AUMENTADA-CON-INTELIGENCIA-ARTIFICIAL.git
cd INGENIER-A-DE-SOFTWARE-AUMENTADA-CON-INTELIGENCIA-ARTIFICIAL\smartbacklog\windows
.\INICIAR.cmd
```

`INICIAR.cmd` abre Docker Desktop si hace falta, pide la API key (no se muestra y queda solo en
`smartbacklog\.env`, que git ignora), construye la app, crea los usuarios de prueba y abre http://localhost:8000.
La primera construcción tarda unos minutos; las siguientes, segundos.

## Uso diario

| Acción | Cómo |
|---|---|
| Iniciar | doble clic en `INICIAR.cmd` |
| Otro puerto (si el 8000 está ocupado) | `INICIAR.cmd -Puerto 8001` |
| Cambiar la API key | `CONFIGURAR_LOCAL.cmd -CambiarClave` |
| Apagar (conserva los datos) | doble clic en `DETENER.cmd` |
| Borrar todo y empezar de cero | `docker compose -p spb down -v` en la carpeta `smartbacklog` |

## Usuarios de prueba

| Usuario | Rol | Clave |
|---|---|---|
| `admin@spb.local` | administrador (actúa como product owner en todos los proyectos) | `cambiar-me-ya` |
| `po@spb.local` · `sm@spb.local` · `dev@spb.local` · `stake@spb.local` | product owner · scrum master · developer · stakeholder del proyecto demo SPB | `demo-spb-2026` |

Son claves de prueba publicadas: cámbialas antes de abrir la app a otras personas fuera de tu equipo.

## Demo con los documentos del curso

1. `INICIAR.cmd` copia `docs_ejemplo` (acta del sprint 3, glosario, política de calidad) a `documents\SPRINT\`.
2. Entra como admin, crea el proyecto con clave **SPRINT** y agrega a `po`, `sm`, `dev` y `stake` en **Miembros**.
3. **Agentes IA → Generar HU desde todos los documentos.**
4. **Revisión humana → Revisar con agentes.** Cada HU queda en nivel 2 (solo product owner), 1 o 0 (product
   owner o scrum master), con motivos, preguntas y la versión que propone el analista.
5. Decide: aprobar (con o sin la propuesta), pedir cambios (el agente refina y vuelve a revisar), falta
   información, descartar o comentar. Prueba con distintos usuarios para ver los permisos por nivel.
6. **Dashboard → aprobar la etapa 1** (solo se puede con todas las HU activas aprobadas), luego la 2.
7. **Agentes IA → Dimensionar backlog** (puntos de función) y **Estimación**: puntos de historia por factores
   junto a los puntos de función; fija la prioridad y aprueba las etapas 3, 4 y 5.

## Si algo falla

- `docker compose -p spb logs --tail 80 app` (en la carpeta `smartbacklog`).
- «AGENT_DOCS_SOURCE=folder no usa ...»: corre `CONFIGURAR_LOCAL.cmd`.
- Error 400/403 de Gemini: API key inválida o sin cuota (`CONFIGURAR_LOCAL.cmd -CambiarClave`).
- Con la API key gratuita, Google puede usar lo que envías para mejorar sus productos: usa documentos de prueba.
