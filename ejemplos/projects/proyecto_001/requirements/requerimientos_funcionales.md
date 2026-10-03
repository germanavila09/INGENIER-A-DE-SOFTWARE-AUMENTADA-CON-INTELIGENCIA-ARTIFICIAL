# Requerimientos funcionales — PRJ001
Versión: 1.3
Autor: Equipo de análisis funcional (ficticio)
Fecha: 2026-09-15

- RF-01: El sistema debe mostrar indicadores de dengue por municipio.
- RF-02: El sistema debe permitir exportar los datos agregados.
- RF-03: El sistema debe cargar semanalmente los archivos de notificación de casos.

## Reglas de negocio
- RN-01: Los datos se agregan por semana epidemiológica.
- RN-02: Solo los usuarios autorizados pueden ver datos a nivel de caso individual (datos personales).
- RN-03: Las exportaciones solo contienen datos agregados, nunca datos personales.

## Requerimientos no funcionales
- RNF-01: El tablero debe responder en menos de 3 segundos.
- RNF-02: Restricción: los datos personales no pueden salir del proyecto de GCP de la entidad.
