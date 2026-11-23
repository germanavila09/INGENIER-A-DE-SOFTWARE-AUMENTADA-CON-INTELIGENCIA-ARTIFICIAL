@echo off
rem Pide la API key de Gemini y deja .env en modo local. -CambiarClave para reemplazarla.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configurar_local.ps1" %*
pause
