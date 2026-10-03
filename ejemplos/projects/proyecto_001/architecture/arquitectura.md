# Arquitectura — PRJ001 (ejemplo ficticio)
Versión: 1.1

## Componentes
- Ingesta: Cloud Functions que cargan cada semana los archivos de notificación en BigQuery (dataset `vigilancia`).
- Almacén analítico: BigQuery, tabla `vigilancia.casos_dengue` particionada por semana epidemiológica.
- Tablero: aplicación web en Cloud Run (FastAPI + React) que consulta BigQuery.
- Autenticación: Identity-Aware Proxy (IAP) con cuentas institucionales.
- Observabilidad: Cloud Logging y Cloud Monitoring.
- Sistema legado: base PostgreSQL local con casos 2010-2023, en solo lectura.

## Integraciones
- No hay API pública; el tablero es de uso interno.
