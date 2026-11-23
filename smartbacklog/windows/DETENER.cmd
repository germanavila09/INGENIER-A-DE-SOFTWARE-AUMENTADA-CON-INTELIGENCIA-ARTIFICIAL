@echo off
rem Apaga SmartBacklogIA. Conserva la base de datos (proyectos, HU, revisiones y aprobaciones).
cd /d "%~dp0.."
docker compose -p spb down 2>nul
docker compose -p spb-gcp down 2>nul
pause
