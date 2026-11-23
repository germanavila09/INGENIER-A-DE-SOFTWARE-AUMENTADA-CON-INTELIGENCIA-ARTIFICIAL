@echo off
rem Levanta SmartBacklogIA en modo local (Docker + Postgres + documents\<CLAVE>\ + Gemini con API key).
rem Opciones: INICIAR.cmd -Puerto 8001   |   INICIAR.cmd -Gcp  (bucket y Vertex AI con gcloud)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar.ps1" %*
pause
