# Backlog — PRJ001 (ejemplo ficticio)

## US-002 Exportar incidencia filtrada
Descripción: Como analista de datos quiero exportar en CSV los datos agregados filtrados del tablero para compartirlos con el comité de vigilancia.
Épica: EP-01 Tablero epidemiológico
Criterios de aceptación:
- El botón "Exportar CSV" descarga los datos con los filtros activos.
- El archivo contiene municipio, semana epidemiológica y número de casos.
- El archivo no contiene datos personales (RN-03).
Dependencias: US-001
Estado: draft

## US-003 Migrar casos históricos
Descripción: Como administrador de datos quiero migrar los casos históricos 2010-2023 de la base PostgreSQL legada al nuevo esquema en BigQuery para tener series históricas en el tablero.
Épica: EP-02 Datos históricos
Criterios de aceptación:
- Todos los casos 2010-2023 quedan disponibles en BigQuery.
- La base legada deja de usarse al terminar la migración.
Estado: draft

## US-004 Alertas por umbral epidémico
Descripción: Como epidemiólogo quiero recibir una alerta cuando un municipio supere el umbral epidémico para actuar a tiempo.
Épica: EP-03 Alertas
Criterios de aceptación:
- Se envía una alerta por correo cuando un municipio supera el umbral.
- La alerta indica municipio, semana y número de casos.
Dependencias: US-001, US-010
Puntos: 8
Estado: aprobada
