# Uso diario: levanta SmartBacklogIA en modo LOCAL (Postgres en Docker + documents\<CLAVE>\ + Gemini con API key).
#   .\iniciar.ps1                 local (por defecto)
#   .\iniciar.ps1 -Puerto 8001    otro puerto si el 8000 esta ocupado
#   .\iniciar.ps1 -Gcp            modo anterior: bucket + Vertex AI con tus credenciales de gcloud (usa .env.gcp)
param([switch]$Gcp, [int]$Puerto = 8000)
$ErrorActionPreference = 'Continue'
$aqui = $PSScriptRoot
$repo = Split-Path $aqui -Parent   # carpeta smartbacklog
# Nombre del proyecto de Docker: 'spb', o 'spb-gcp' si ya existe esa base (instalaciones anteriores).
function Salir($m) { Write-Host $m -ForegroundColor Yellow; exit 1 }

# 1. Docker Desktop: si no esta corriendo, lo abre y espera.
docker version --format '{{.Server.Version}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host 'Abriendo Docker Desktop...'
  # $env:ProgramW6432 apunta a "C:\Program Files" aunque PowerShell sea de 32 bits.
  $dd = @("$env:ProgramW6432", "$env:ProgramFiles", 'C:\Program Files') | Where-Object { $_ } |
        ForEach-Object { Join-Path $_ 'Docker\Docker\Docker Desktop.exe' } | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($dd) { Start-Process $dd } else { Salir 'Abre Docker Desktop manualmente y vuelve a ejecutar.' }
  for ($i = 0; $i -lt 60; $i++) { Start-Sleep 5; docker version --format '{{.Server.Version}}' 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { break } }
  if ($LASTEXITCODE -ne 0) { Salir 'Docker no arranco en 5 minutos.' }
}

$proj = 'spb'
docker volume inspect spb-gcp_pgdata 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $proj = 'spb-gcp' }

# 2. Configuracion.
Set-Location $repo
# Documentos de ejemplo del curso para el proyecto demo SPRINT (si el repo los trae y aun no estan).
$ejemplos = Join-Path (Split-Path $repo -Parent) 'docs_ejemplo'
$sprint = Join-Path $repo 'documents\SPRINT'
if ((Test-Path $ejemplos) -and -not (Test-Path $sprint)) {
  New-Item -ItemType Directory -Force $sprint | Out-Null
  foreach ($f in 'acta_reunion_sprint3.docx', 'glosario.md', 'politica_calidad_software.pdf') {
    $p = Join-Path $ejemplos $f; if (Test-Path $p) { Copy-Item $p $sprint }
  }
  Write-Host 'Documentos de ejemplo copiados a documents\SPRINT (crea el proyecto con clave SPRINT para usarlos).'
}
$envTxt = if (Test-Path '.env') { Get-Content '.env' -Raw } else { '' }
if ($Gcp) {
  if (-not (Test-Path '.env.gcp')) { Salir 'No existe .env.gcp (la copia del modo GCP).' }
  if ($envTxt -notmatch '(?m)^AGENT_DOCS_SOURCE=gcs') { Copy-Item '.env' '.env.local' -Force; Copy-Item '.env.gcp' '.env' -Force
    Write-Host 'Modo GCP: .env <- .env.gcp (el local quedo en .env.local)' }
  $adc = Join-Path $env:APPDATA 'gcloud\application_default_credentials.json'
  if (-not (Test-Path $adc)) { Salir 'Faltan credenciales. Ejecuta: gcloud auth application-default login' }
  $files = @('-f','docker-compose.yml','-f','docker-compose.gcp.yml')
} else {
  if ($envTxt -match '(?m)^AGENT_DOCS_SOURCE=gcs' -and (Test-Path '.env.local')) { Copy-Item '.env.local' '.env' -Force; $envTxt = Get-Content '.env' -Raw }
  if ($envTxt -notmatch '(?m)^GOOGLE_API_KEY=\S' -or $envTxt -notmatch '(?m)^AGENT_DOCS_SOURCE=folder') {
    & (Join-Path $aqui 'configurar_local.ps1')
    if (-not $?) { Salir 'No se pudo configurar el modo local.' }
  }
  $files = @('-f','docker-compose.yml')
}

# 3. Levantar (reconstruye solo si cambio el codigo; las migraciones se aplican al arrancar).
$env:SPB_PORT = "$Puerto"
$compose = @('compose','-p',$proj) + $files
$up = $compose + @('up','-d','--build','--remove-orphans')
& docker @up
if ($LASTEXITCODE -ne 0) { Salir "docker compose fallo (si el puerto esta ocupado: .\iniciar.ps1 -Puerto 8001)." }

# 4. Esperar a que responda.
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
  try { if ((Invoke-WebRequest "http://localhost:$Puerto/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200) { $ok = $true; break } } catch { }
  Start-Sleep 3
}
if (-not $ok) { $logs = $compose + @('logs','--tail','60','app'); & docker @logs; Salir 'La app no respondio.' }

# 5. Usuarios base (no cambia nada si ya existen).
$seed = $compose + @('exec','-T','app','python','scripts/seed.py')
& docker @seed | Out-Host

Start-Process "http://localhost:$Puerto/login"
Write-Host "Listo: http://localhost:$Puerto  ->  Revision humana en el menu de cada proyecto. Usuarios: ver windows\LEEME.md" -ForegroundColor Green
