<#
.SYNOPSIS
  Publica «Ruta de historias» y SmartBacklogIA en Cloud Run (proyecto servi-modelos-ia-dev) para pruebas.

.DESCRIPTION
  Crea (o actualiza, si ya existen) estos recursos y muestra el plan antes de tocar nada:
    - APIs: Cloud Run, Cloud Build, Artifact Registry, Cloud SQL Admin, Secret Manager, Vertex AI (+ IAP si aplica).
    - Repositorio de imágenes «ruta-historias» en Artifact Registry.
    - Los dos servicios corren con la cuenta de servicio de Compute por defecto del proyecto, que ya tiene
      Vertex AI, Storage, Secret Manager y Cloud Run Admin: el script no asigna permisos IAM (basta el rol Editor).
    - Cloud SQL PostgreSQL 16 «spb-pg» (db-f1-micro, 10 GB) para SmartBacklogIA.
    - Secretos: clave de sesión y de admin de SmartBacklogIA, URL de la base, API key del puente y usuarios de prueba.
    - Servicios Cloud Run «smartbacklog» y «hu-flujo», y el job «smartbacklog-seed» (crea el admin).
    - Usuarios de prueba en los dos servicios y proyecto SINCHIHU en SmartBacklogIA con ellos como miembros.

  Se puede volver a ejecutar: lo que ya existe se reutiliza, las imágenes se reconstruyen y los servicios se
  actualizan. Las claves generadas se guardan en deploy\estado_despliegue.json (no se sube a git): consérvalo,
  porque con rol Editor no se pueden leer de vuelta desde Secret Manager.

  Para abrir los servicios al público (cada uno tiene su propio inicio de sesión), un paso de Cloud Build corre
  como la cuenta de Compute, que sí tiene Cloud Run Admin.

.PARAMETER Testers
  Correos de quienes van a probar, separados por coma. Cada uno recibe una clave, la misma para los dos servicios.

.EXAMPLE
  .\deploy\desplegar_gcp.ps1 -Testers "ana@correounivalle.edu.co,pedro@gmail.com"
  .\deploy\desplegar_gcp.ps1 -Testers "nuevo@gmail.com"            # agrega un tester sin tocar a los demás
  .\deploy\desplegar_gcp.ps1 -SubirResultados                       # sube también salidas_hu local al bucket
#>
param(
    [string]$Testers = "",
    [string]$Proyecto = "servi-modelos-ia-dev",
    [string]$Region = "us-central1",
    [string]$Bucket = "adk_ing",
    [string]$RutaSmartBacklog = "E:\Doctorado\ing_soft_IA\SmartBacklogIA",
    [string]$ClaveSbia = "SINCHIHU",
    [switch]$SubirResultados,
    [switch]$SinConfirmar
)

$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
$Ar = "ruta-historias"
$Sql = "spb-pg"
$Acceso = "clave"
$EstadoArchivo = Join-Path $Repo "deploy\estado_despliegue.json"
$Tag = Get-Date -Format "yyyyMMdd-HHmmss"
$ImgHu = "$Region-docker.pkg.dev/$Proyecto/$Ar/hu-flujo:$Tag"
$ImgSb = "$Region-docker.pkg.dev/$Proyecto/$Ar/smartbacklog:$Tag"
$Tmp = Join-Path $env:TEMP "despliegue_ruta_$Tag"
New-Item -ItemType Directory -Force $Tmp | Out-Null

function Paso($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Fallo($t) { Write-Host ""; Write-Host "ERROR: $t" -ForegroundColor Red; Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue; exit 1 }
function G {
    # gcloud con el proyecto fijo; detiene el script si falla.
    & gcloud @args --project=$Proyecto --quiet
    if ($LASTEXITCODE -ne 0) { Fallo "gcloud $($args -join ' ')" }
}
function Existe { & gcloud @args --project=$Proyecto --quiet 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) }
function Texto([string]$ruta, [string]$contenido) { [IO.File]::WriteAllText($ruta, $contenido, (New-Object Text.UTF8Encoding $false)) }
function NuevaClave([int]$n = 20) {
    $c = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    $b = New-Object byte[] $n
    (New-Object Security.Cryptography.RNGCryptoServiceProvider).GetBytes($b)
    -join ($b | ForEach-Object { $c[$_ % $c.Length] })
}
# Estado local: las claves generadas (con rol Editor no se pueden leer de Secret Manager).
$Estado = @{}
if (Test-Path $EstadoArchivo) {
    $leido = Get-Content $EstadoArchivo -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $leido.PSObject.Properties) { $Estado[$p.Name] = $p.Value }
}
function GuardarEstado { Texto $EstadoArchivo ($Estado | ConvertTo-Json -Depth 5) }
function GuardarSecreto($nombre, $valor) {
    # Crea el secreto o le agrega una versión. La cuenta de Compute lo lee (secretAccessor del proyecto).
    $f = Join-Path $Tmp "s_$nombre"
    Texto $f $valor
    if (Existe secrets describe $nombre) { G secrets versions add $nombre --data-file=$f | Out-Null }
    else { G secrets create $nombre --replication-policy=automatic --data-file=$f | Out-Null }
    Remove-Item $f -Force
}
function EnvYaml($ruta, [hashtable]$vars) {
    $lineas = $vars.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name): '$($_.Value -replace "'", "''")'" }
    Texto $ruta (($lineas -join "`n") + "`n")
}
function AbrirAlPublico([string[]]$servicios) {
    # Quitar el chequeo IAM de invocación exige run.services.setIamPolicy (no lo tiene Editor). Lo hace un paso
    # de Cloud Build que corre como la cuenta de Compute (Cloud Run Admin). Cada servicio pide su propio login.
    $cmds = ($servicios | ForEach-Object {
        "(gcloud run services update $_ --region=$Region --no-invoker-iam-check --quiet || " +
        "gcloud run services add-iam-policy-binding $_ --region=$Region --member=allUsers --role=roles/run.invoker --quiet)"
    }) -join " && "
    $yaml = Join-Path $Tmp "abrir.yaml"
    Texto $yaml @"
steps:
- name: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
  entrypoint: bash
  args: ['-c', '$($cmds -replace "'", "''")']
serviceAccount: projects/$Proyecto/serviceAccounts/$SaRun
options:
  logging: CLOUD_LOGGING_ONLY
"@
    G builds submit --no-source --config=$yaml --region=$Region
}
function EsPublico($url) {
    # Google responde 403 antes de llegar al servicio si sigue privado; la app responde 200 o 401.
    try { Invoke-WebRequest -Uri $url -UseBasicParsing -Method Get -TimeoutSec 60 | Out-Null; return $true }
    catch { $c = [int]$_.Exception.Response.StatusCode; return ($c -ne 403 -and $c -ne 0) }
}

# ------------------------------------------------------------------ verificaciones
Paso "Verificaciones"
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) { Fallo "no encuentro gcloud. Instala Google Cloud SDK." }
$cuenta = & gcloud config get-value account 2>$null
if (-not $cuenta) { Fallo "no hay sesión en gcloud. Ejecuta: gcloud auth login" }
if (-not (Test-Path (Join-Path $RutaSmartBacklog "Dockerfile"))) { Fallo "no encuentro SmartBacklogIA en $RutaSmartBacklog (usa -RutaSmartBacklog)." }
$rama = & git -C $RutaSmartBacklog rev-parse --abbrev-ref HEAD 2>$null
$numero = & gcloud projects describe $Proyecto --format="value(projectNumber)" 2>$null
if (-not $numero) { Fallo "no tengo acceso al proyecto $Proyecto con $cuenta." }
$SaRun = "$numero-compute@developer.gserviceaccount.com"
$listaTesters = @($Testers -split "[,; ]+" | Where-Object { $_ -match "^[^@\s]+@[^@\s]+\.[^@\s]+$" } | ForEach-Object { $_.ToLower() } | Select-Object -Unique)
if ($Testers -and $listaTesters.Count -eq 0) { Fallo "-Testers no tiene correos válidos." }
# Quien despliega también entra a Ruta de historias con su propio usuario.
$listaTesters = @(@($cuenta.ToLower()) + $listaTesters | Select-Object -Unique)
$sqlExiste = Existe sql instances describe $Sql

Write-Host "Cuenta:            $cuenta"
Write-Host "Proyecto:          $Proyecto ($numero), región $Region"
Write-Host "SmartBacklogIA:    $RutaSmartBacklog (rama $rama)"
Write-Host "Cuenta de servicio: $SaRun"
Write-Host "Usuarios:          $($listaTesters -join ', ')"
Write-Host ""
Write-Host "Se va a crear o actualizar:" -ForegroundColor Yellow
Write-Host "  - APIs, repositorio de imágenes '$Ar' y secretos en Secret Manager (sin cambios de permisos IAM)"
Write-Host "  - Los servicios usan la cuenta de Compute: leen y escriben en gs://$Bucket (documentos y salidas_hu/)"
if (-not $sqlExiste) {
    Write-Host "  - Cloud SQL '$Sql' PostgreSQL 16, db-f1-micro, 10 GB, solo IP privada. COSTO FIJO aprox. 8-10 USD/mes mientras exista." -ForegroundColor Yellow
    Write-Host "  - Red privada: rango reservado para Cloud SQL en la VPC (default o una nueva 'ruta-vpc') y conexión de servicios" -ForegroundColor Yellow
}
Write-Host "  - Imágenes con Cloud Build y servicios Cloud Run 'smartbacklog' y 'hu-flujo' (costo por uso; 0 sin tráfico)"
Write-Host "  - Gemini (Vertex AI) se cobra por uso cada vez que alguien analiza o genera historias"
if ($SubirResultados) { Write-Host "  - Copia de salidas_hu local a gs://$Bucket/salidas_hu/" }
if (-not $SinConfirmar) {
    $r = Read-Host "`nEscribe SI para continuar"
    if ($r -ne "SI") { Fallo "cancelado; no se creó nada." }
}

# ------------------------------------------------------------------ base
Paso "APIs"
$apis = @("run.googleapis.com", "cloudbuild.googleapis.com", "artifactregistry.googleapis.com", "sqladmin.googleapis.com",
          "secretmanager.googleapis.com", "aiplatform.googleapis.com", "iam.googleapis.com", "compute.googleapis.com",
          "servicenetworking.googleapis.com")
G services enable @apis

Paso "Repositorio de imágenes"
if (-not (Existe artifacts repositories describe $Ar --location=$Region)) {
    G artifacts repositories create $Ar --repository-format=docker --location=$Region --description="Ruta de historias y SmartBacklogIA"
}

# ------------------------------------------------------------------ red privada
# La organización prohíbe IP pública en Cloud SQL (constraints/sql.restrictPublicIp): la base solo tiene IP
# privada en una VPC, y Cloud Run llega a ella con salida directa a la VPC (Direct VPC egress).
Paso "Red privada para la base de datos"
if (Existe compute networks describe default) {
    $Red = "default"
    $Subred = & gcloud compute networks subnets list --network=default --regions=$Region --project=$Proyecto --format="value(name)" 2>$null | Select-Object -First 1
    if (-not $Subred) { Fallo "la red default no tiene subred en $Region." }
} else {
    $Red = "ruta-vpc"; $Subred = "ruta-$Region"
    if (-not (Existe compute networks describe $Red)) { G compute networks create $Red --subnet-mode=custom }
    if (-not (Existe compute networks subnets describe $Subred --region=$Region)) {
        G compute networks subnets create $Subred --network=$Red --region=$Region --range=10.20.0.0/24 --enable-private-ip-google-access
    }
}
$Rango = "ruta-sql-psa"
if (-not (Existe compute addresses describe $Rango --global)) {
    G compute addresses create $Rango --global --purpose=VPC_PEERING --prefix-length=20 --network=$Red `
        --description="Rango para Cloud SQL con IP privada (Ruta de historias)"
}
$conectado = & gcloud services vpc-peerings list --network=$Red --project=$Proyecto --format="value(reservedPeeringRanges)" 2>$null
if (-not $conectado) {
    G services vpc-peerings connect --service=servicenetworking.googleapis.com --ranges=$Rango --network=$Red
} elseif ($conectado -notmatch $Rango) {
    G services vpc-peerings update --service=servicenetworking.googleapis.com --ranges="$(($conectado -split '[;,]') -join ','),$Rango" --network=$Red
}
$VpcRun = @("--network=$Red", "--subnet=$Subred", "--vpc-egress=private-ranges-only")

# ------------------------------------------------------------------ base de datos y secretos
Paso "Cloud SQL con IP privada (la primera vez tarda 5-10 minutos)"
if (-not $sqlExiste) {
    G sql instances create $Sql --database-version=POSTGRES_16 --edition=ENTERPRISE --tier=db-f1-micro `
        --region=$Region --storage-size=10 --storage-type=HDD --storage-auto-increase --availability-type=zonal `
        --no-assign-ip --network="projects/$Proyecto/global/networks/$Red" --allocated-ip-range-name=$Rango
}
$ipSql = & gcloud sql instances describe $Sql --project=$Proyecto --format="value(ipAddresses[0].ipAddress)" 2>$null
if (-not $ipSql) { Fallo "la instancia $Sql no tiene IP privada." }
if (-not (Existe sql databases describe spb --instance=$Sql)) { G sql databases create spb --instance=$Sql }
if (-not $Estado["db_password"] -or $Estado["db_ip"] -ne $ipSql) {
    $pg = if ($Estado["db_password"]) { $Estado["db_password"] } else { NuevaClave 24 }
    $hayUsuario = & gcloud sql users list --instance=$Sql --project=$Proyecto --format="value(name)" 2>$null | Where-Object { $_ -eq "spb" }
    if ($hayUsuario) {
        G sql users set-password spb --instance=$Sql --password=$pg
    } else {
        G sql users create spb --instance=$Sql --password=$pg
    }
    GuardarSecreto "sbia-database-url" "postgresql+psycopg://spb:$pg@$($ipSql):5432/spb"
    $Estado["db_password"] = $pg; $Estado["db_ip"] = $ipSql; GuardarEstado
}

Paso "Secretos de SmartBacklogIA"
if (-not $Estado["session_secret"]) {
    $Estado["session_secret"] = NuevaClave 48; GuardarSecreto "sbia-session-secret" $Estado["session_secret"]; GuardarEstado
}
if (-not $Estado["admin_password"]) {
    $Estado["admin_password"] = NuevaClave 16; GuardarSecreto "sbia-admin-password" $Estado["admin_password"]; GuardarEstado
}
$adminPass = $Estado["admin_password"]

# ------------------------------------------------------------------ SmartBacklogIA
Paso "Imagen de SmartBacklogIA (Cloud Build)"
G builds submit $RutaSmartBacklog --tag=$ImgSb --region=$Region

$envSb = Join-Path $Tmp "env_sb.yaml"
EnvYaml $envSb @{
    AGENT_PROVIDER = "adk"; AGENT_MODEL = "gemini-2.5-flash"; AGENT_EXECUTION = "background"; AGENT_AUTO_REFINE = "false"
    AGENT_MAX_DOC_CHARS = "200000"; GOOGLE_GENAI_USE_VERTEXAI = "TRUE"; GOOGLE_CLOUD_PROJECT = $Proyecto
    GOOGLE_CLOUD_LOCATION = $Region; GCP_PROJECT = $Proyecto; AGENT_DOCS_SOURCE = "gcs"; AGENT_DOCS_BUCKET = $Bucket
    AGENT_DOCS_PREFIX = "spb"; AGENT_DOCS_MAP = "SINCHI=SERVI _ SINCHI __;$ClaveSbia=SERVI _ SINCHI __;DOCS=documentos/"
    AGENT_PRICE_IN_PER_1M = "0.30"; AGENT_PRICE_OUT_PER_1M = "2.50"; SESSION_HTTPS_ONLY = "true"
    SEED_ADMIN_EMAIL = "admin@spb.local"; SEED_DEMO = "false"
}
$secretosSb = "DATABASE_URL=sbia-database-url:latest,SESSION_SECRET=sbia-session-secret:latest,SEED_ADMIN_PASSWORD=sbia-admin-password:latest"

Paso "Servicio smartbacklog"
G run deploy smartbacklog @VpcRun @("--image=$ImgSb", "--service-account=$SaRun", "--region=$Region",
    "--env-vars-file=$envSb", "--set-secrets=$secretosSb", "--memory=1Gi", "--cpu=1", "--no-cpu-throttling",
    "--min-instances=0", "--max-instances=2", "--timeout=900")
$urlSb = & gcloud run services describe smartbacklog --region=$Region --project=$Proyecto --format="value(status.url)"

Paso "Admin de SmartBacklogIA (job de seed)"
G run jobs deploy smartbacklog-seed @VpcRun --image=$ImgSb --region=$Region --service-account=$SaRun `
    --env-vars-file=$envSb --set-secrets=$secretosSb `
    --command=sh --args="-c,alembic upgrade head && python scripts/seed.py" --max-retries=0 --task-timeout=600
G run jobs execute smartbacklog-seed --region=$Region --wait

Paso "Acceso público a smartbacklog (Cloud Build como la cuenta de Compute)"
if (-not (EsPublico "$urlSb/health")) { AbrirAlPublico @("smartbacklog") }
if (-not (EsPublico "$urlSb/health")) {
    Fallo "smartbacklog sigue privado. La organización puede estar bloqueando el acceso público a Cloud Run."
}

Paso "Conexión del puente (API key de servicio)"
$sesion = $null
try {
    Invoke-RestMethod -Method Post "$urlSb/api/v1/auth/login" -ContentType "application/json" -SessionVariable sesion `
        -Body (@{ email = "admin@spb.local"; password = $adminPass } | ConvertTo-Json) | Out-Null
} catch { Fallo "no pude iniciar sesión como admin en $urlSb : $($_.Exception.Message). Si perdiste deploy\estado_despliegue.json, la clave del admin ya no coincide." }
if (-not $Estado["api_key"]) {
    $k = Invoke-RestMethod -Method Post "$urlSb/api/v1/admin/api-keys" -WebSession $sesion -ContentType "application/json" `
        -Body (@{ name = "hu-flujo-$Tag" } | ConvertTo-Json)
    $Estado["api_key"] = $k.api_key; GuardarEstado
    GuardarSecreto "sbia-api-key" $Estado["api_key"]
}

# ------------------------------------------------------------------ testers
Paso "Usuarios de prueba"
$claves = [ordered]@{}
if ($Estado["usuarios"]) { foreach ($p in $Estado["usuarios"].PSObject.Properties) { $claves[$p.Name] = $p.Value } }
$nuevos = @()
foreach ($t in $listaTesters) {
    if (-not $claves.Contains($t)) { $claves[$t] = NuevaClave 14; $nuevos += $t }
}
$Estado["usuarios"] = [pscustomobject]$claves; GuardarEstado
if ($nuevos.Count -or -not (Existe secrets describe "hu-usuarios")) {
    GuardarSecreto "hu-usuarios" ((($claves.GetEnumerator() | ForEach-Object { "$($_.Key):$($_.Value)" }) -join ";"))
}
foreach ($t in $nuevos) {
    $nombre = ($t -split "@")[0]
    if ($nombre.Length -lt 2) { $nombre = "$nombre-tester" }
    try {
        Invoke-RestMethod -Method Post "$urlSb/api/v1/admin/users" -WebSession $sesion -ContentType "application/json" `
            -Body (@{ email = $t; name = $nombre; password = $claves[$t] } | ConvertTo-Json) | Out-Null
    } catch { Write-Host "  $t ya existía en SmartBacklogIA: conserva la clave que tenía allá." -ForegroundColor Yellow }
}
if ($claves.Count) {
    $apiKey = $Estado["api_key"]
    $miembros = @($claves.Keys | ForEach-Object { @{ email = $_; role = "product_owner" } })
    $cuerpo = @{ key = $ClaveSbia; name = "SINCHI (desde Ruta de historias)"; members = $miembros } | ConvertTo-Json -Depth 4
    try {
        Invoke-RestMethod -Method Post "$urlSb/api/v1/ingest/projects" -Headers @{ "X-API-Key" = $apiKey } `
            -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($cuerpo)) | Out-Null
    } catch { Write-Host "  No pude preparar el proyecto ${ClaveSbia}: $($_.Exception.Message)" -ForegroundColor Yellow }
}

# ------------------------------------------------------------------ Ruta de historias
if ($SubirResultados -and (Test-Path (Join-Path $Repo "salidas_hu"))) {
    Paso "Resultados locales al bucket"
    G storage cp --recursive (Join-Path $Repo "salidas_hu\*") "gs://$Bucket/salidas_hu/"
}

Paso "Imagen de Ruta de historias (Cloud Build)"
G builds submit $Repo --tag=$ImgHu --region=$Region

$envHu = Join-Path $Tmp "env_hu.yaml"
EnvYaml $envHu @{
    HU_ACCESO = $Acceso; HU_INPUT_URI = "gs://$Bucket/"; HU_RESULTS_URI = "gs://$Bucket/salidas_hu/"
    GCS_BUCKET = $Bucket; GOOGLE_CLOUD_PROJECT = $Proyecto; GOOGLE_CLOUD_LOCATION = $Region
    GOOGLE_GENAI_USE_VERTEXAI = "TRUE"; ADK_MODEL = "gemini-2.5-flash"; ADK_SESSION_URI = "memory://"
    SBIA_URL = $urlSb; SBIA_CLAVES = "SERVI_SINCHI=$ClaveSbia"
}
$secretosHu = "SBIA_API_KEY=sbia-api-key:latest,HU_USUARIOS=hu-usuarios:latest"
# max-instances=1: los análisis corren en segundo plano dentro del proceso y el estado usa bloqueos en memoria.
$opcionesHu = @("--image=$ImgHu", "--service-account=$SaRun", "--region=$Region", "--env-vars-file=$envHu", "--set-secrets=$secretosHu",
    "--memory=2Gi", "--cpu=1", "--no-cpu-throttling", "--min-instances=0", "--max-instances=1",
    "--concurrency=40", "--timeout=3600")

Paso "Servicio hu-flujo"
G run deploy hu-flujo @opcionesHu
$urlHu = & gcloud run services describe hu-flujo --region=$Region --project=$Proyecto --format="value(status.url)"
if (-not (EsPublico "$urlHu/flujo")) { AbrirAlPublico @("hu-flujo") }
if (-not (EsPublico "$urlHu/flujo")) {
    Fallo "hu-flujo sigue privado. La organización puede estar bloqueando el acceso público a Cloud Run."
}

# ------------------------------------------------------------------ resumen
Paso "Listo"
$archivo = Join-Path $Repo "deploy\credenciales_pruebas.csv"
$filas = @("servicio,url,usuario,clave")
$filas += "smartbacklog (admin),$urlSb,admin@spb.local,$adminPass"
foreach ($e in $claves.GetEnumerator()) {
    $filas += "hu-flujo,$urlHu/flujo,$($e.Key),$($e.Value)"
    $filas += "smartbacklog,$urlSb,$($e.Key),$($e.Value)"
}
Texto $archivo (($filas -join "`r`n") + "`r`n")
Write-Host "Ruta de historias:  $urlHu/flujo" -ForegroundColor Green
Write-Host "SmartBacklogIA:     $urlSb" -ForegroundColor Green
Write-Host "Credenciales en:    $archivo  (compártelas por un canal privado; el archivo no se sube a git)"
Write-Host "Estado (consérvalo): $EstadoArchivo"
if ($nuevos.Count) { Write-Host "Testers nuevos:     $($nuevos -join ', ')" }
Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
