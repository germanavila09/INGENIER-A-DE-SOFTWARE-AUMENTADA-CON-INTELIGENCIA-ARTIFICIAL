# Deja SmartBacklogIA en modo LOCAL: Postgres en Docker, documentos en documents\<CLAVE>\ y Gemini con API key.
# No usa GCP (ni bucket, ni Vertex AI, ni credenciales de gcloud).
#
#   .\configurar_local.ps1                    pide la API key (sin mostrarla) y ajusta .env
#   .\configurar_local.ps1 -TraerDocumentos   ademas copia del bucket los documentos de cada proyecto (una vez)
#   .\configurar_local.ps1 -CambiarClave      reemplaza la API key guardada
#
# La primera vez guarda el .env anterior como .env.gcp (para volver al modo bucket + Vertex si hace falta).
param([switch]$TraerDocumentos, [switch]$CambiarClave)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent   # carpeta smartbacklog
$envFile = Join-Path $repo '.env'
$gcpFile = Join-Path $repo '.env.gcp'
$utf8 = New-Object System.Text.UTF8Encoding $false   # sin BOM

function Leer-Env([string]$path) {
  $h = @{}
  if (Test-Path $path) {
    foreach ($l in [IO.File]::ReadAllLines($path, $utf8)) {
      if ($l -match '^\s*([A-Za-z0-9_]+)\s*=(.*)$') { $h[$Matches[1]] = $Matches[2].Trim() }
    }
  }
  return $h
}

if (-not (Test-Path $repo)) { throw "No encuentro $repo" }
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $repo '.env.example') $envFile }
if (-not (Test-Path $gcpFile)) { Copy-Item $envFile $gcpFile; Write-Host "Copia del .env anterior: $gcpFile" }
$actual = Leer-Env $envFile

# 1. API key de Gemini (https://aistudio.google.com/apikey). Se pide oculta y nunca se imprime.
$clave = $actual['GOOGLE_API_KEY']
if ($CambiarClave -or -not $clave) {
  Write-Host 'Pega tu API key de Gemini (https://aistudio.google.com/apikey). No se mostrara en pantalla.'
  $seg = Read-Host 'API key' -AsSecureString
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seg)
  try { $clave = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim() }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
  if (-not $clave) { throw 'No se ingreso ninguna clave.' }
  if (-not $clave.StartsWith('AIza')) { Write-Host 'Aviso: las claves de AI Studio suelen empezar por "AIza". Revisa que sea la correcta.' -ForegroundColor Yellow }
}

# 2. Documentos del bucket -> documents\<CLAVE>\ (opcional). Usa los valores del modo anterior (.env.gcp).
if ($TraerDocumentos) {
  $gcp = Leer-Env $gcpFile
  $bucket = $gcp['AGENT_DOCS_BUCKET']
  if (-not $bucket) { Write-Host 'No hay AGENT_DOCS_BUCKET en .env.gcp: copia los archivos a mano a documents\<CLAVE>\' -ForegroundColor Yellow }
  elseif (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) { Write-Host 'gcloud no esta instalado: copia los archivos a mano.' -ForegroundColor Yellow }
  else {
    $map = @{}
    foreach ($par in ($gcp['AGENT_DOCS_MAP'] -split ';')) { if ($par -match '^\s*([^=]+?)\s*=(.+)$') { $map[$Matches[1].ToUpper()] = $Matches[2] } }
    if ($map.Count -eq 0) { Write-Host 'AGENT_DOCS_MAP esta vacio: copia los archivos a mano a documents\<CLAVE>\' -ForegroundColor Yellow }
    foreach ($k in $map.Keys) {
      $dest = Join-Path $repo "documents\$k"
      New-Item -ItemType Directory -Force $dest | Out-Null
      $src = "gs://$bucket/$($map[$k])*"
      Write-Host "Copiando $src -> documents\$k"
      & gcloud storage cp $src "$dest\" 2>&1 | Out-Host
      if ($LASTEXITCODE -ne 0) { Write-Host "  No se pudo copiar (permisos o nombre). Copia esos archivos a mano a documents\$k\" -ForegroundColor Yellow }
    }
  }
}

# 3. .env en modo local. Las variables de GCP quedan vacias: la app no arranca si se mezclan carpeta y bucket.
$valores = [ordered]@{
  AGENT_PROVIDER = 'adk'; AGENT_MODEL = 'gemini-2.5-flash'; GOOGLE_API_KEY = $clave
  GOOGLE_GENAI_USE_VERTEXAI = 'false'; GOOGLE_CLOUD_PROJECT = ''; GOOGLE_CLOUD_LOCATION = ''
  AGENT_DOCS_SOURCE = 'folder'; AGENT_DOCS_BUCKET = ''; AGENT_DOCS_PREFIX = ''; AGENT_DOCS_MAP = ''; GCP_PROJECT = ''
  AGENT_PRICE_IN_PER_1M = '0.30'; AGENT_PRICE_OUT_PER_1M = '2.50'; AGENT_EXECUTION = 'background'
}
$lineas = New-Object System.Collections.Generic.List[string]
$puestos = @{}
foreach ($l in [IO.File]::ReadAllLines($envFile, $utf8)) {
  if ($l -match '^\s*([A-Z0-9_]+)\s*=' -and $valores.Contains($Matches[1])) {
    $k = $Matches[1]
    if (-not $puestos[$k]) { $lineas.Add("$k=$($valores[$k])"); $puestos[$k] = $true }   # repetidas: se quitan
  } elseif ($l -like '# Se usa con docker-compose.gcp.yml*') {
    $lineas.Add('# Modo local (docker-compose.yml). Copia del modo GCP en .env.gcp. Nunca commitear este archivo.')
  } else { $lineas.Add($l) }
}
foreach ($k in $valores.Keys) { if (-not $puestos[$k]) { $lineas.Add("$k=$($valores[$k])") } }
[IO.File]::WriteAllLines($envFile, $lineas, $utf8)

Write-Host ''
Write-Host 'Listo: .env en modo local (Gemini con API key, documentos en documents\<CLAVE>\).' -ForegroundColor Green
$docs = Get-ChildItem (Join-Path $repo 'documents') -Directory | ForEach-Object { "$($_.Name) ($((Get-ChildItem $_.FullName -File).Count) archivos)" }
Write-Host "Carpetas de documentos: $($docs -join ', ')"
Write-Host 'Siguiente paso: windows\INICIAR.cmd'
