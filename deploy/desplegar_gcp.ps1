<#
.SYNOPSIS
  Publica «Ruta de historias» y SmartBacklogIA en Cloud Run (proyecto servi-modelos-ia-dev) para pruebas.

.DESCRIPTION
  Crea (o actualiza, si ya existen) estos recursos y muestra el plan antes de tocar nada:
    - APIs: Cloud Run, Cloud Build, Artifact Registry, Cloud SQL Admin, Secret Manager, Vertex AI (+ IAP si aplica).
    - Repositorio de imágenes «ruta-historias» en Artifact Registry.
    - Cuentas de servicio sa-hu-flujo y sa-smartbacklog con permisos mínimos (Vertex AI, bucket, Cloud SQL, secretos).
    - Cloud SQL PostgreSQL 16 «spb-pg» (db-f1-micro, 10 GB) para SmartBacklogIA.
    - Secretos: clave de sesión y de admin de SmartBacklogIA, URL de la base, API key del puente y usuarios de prueba.
    - Servicios Cloud Run «smartbacklog» y «hu-flujo», y el job «smartbacklog-seed» (crea el admin).
    - Usuarios de prueba en los dos servicios y proyecto SINCHIHU en SmartBacklogIA con ellos como miembros.

  Se puede volver a ejecutar: lo que ya existe se reutiliza, las imágenes se reconstruyen y los servicios se
  actualizan. Las claves de los testers que ya existían se conservan.

.PARAMETER Testers
  Correos de quienes van a probar, separados por coma. Con -Acceso clave cada uno recibe una clave
  (la misma para los dos servicios); con -Acceso iap entran a Ruta de historias con su cuenta de Google.

.PARAMETER Acceso
  clave (por defecto): usuario y clave por persona; funciona con cualquier correo.
  iap: Identity-Aware Proxy con cuentas de Google. Para correos fuera de la organización del proyecto
       puede requerir configurar la pantalla de consentimiento OAuth como «externa» en la consola.

.EXAMPLE
  .\deploy\desplegar_gcp.ps1 -Testers "ana@correounivalle.edu.co,pedro@gmail.com"
  .\deploy\desplegar_gcp.ps1 -Testers "nuevo@gmail.com"            # agrega un tester sin tocar a los demás
  .\deploy\desplegar_gcp.ps1 -SubirResultados                       # sube también salidas_hu local al bucket
#>
param(
    [string]$Testers = "",
    [ValidateSet("clave", "iap")][string]$Acceso = "clave",
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
$SaHu = "sa-hu-flujo@$Proyecto.iam.gserviceaccount.com"
$SaSb = "sa-smartbacklog@$Proyecto.iam.gserviceaccount.com"
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
function LeerSecreto($nombre) {
    $v = & gcloud secrets versions access latest --secret=$nombre --project=$Proyecto 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($v -join "`n")
}
function GuardarSecreto($nombre, $valor, [string[]]$lectores) {
    $f = Join-Path $Tmp "s_$nombre"
    Texto $f $valor
    if (Existe secrets describe $nombre) { G secrets versions add $nombre --data-file=$f | Out-Null }
    else { G secrets create $nombre --replication-policy=automatic --data-file=$f | Out-Null }
    Remove-Item $f -Force
    foreach ($sa in $lectores) {
        G secrets add-iam-policy-binding $nombre --member="serviceAccount:$sa" --role=roles/secretmanager.secretAccessor | Out-Null
    }
}
function EnvYaml($ruta, [hashtable]$vars) {
    $lineas = $vars.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name): '$($_.Value -replace "'", "''")'" }
    Texto $ruta (($lineas -join "`n") + "`n")
}
function Desplegar($servicio, [string[]]$extra) {
    # Sin chequeo IAM de invocación (no requiere allUsers, que muchas organizaciones bloquean):
    # la autenticación la hace la propia aplicación. Si la versión de gcloud no lo admite, usa allUsers.
    & gcloud run deploy $servicio @extra --no-invoker-iam-check --project=$Proyecto --region=$Region --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  --no-invoker-iam-check no funcionó; pruebo con --allow-unauthenticated" -ForegroundColor Yellow
        & gcloud run deploy $servicio @extra --allow-unauthenticated --project=$Proyecto --region=$Region --quiet
        if ($LASTEXITCODE -ne 0) {
            Fallo "no se pudo desplegar $servicio. Si la organización prohíbe el acceso público, usa -Acceso iap."
        }
    }
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
$listaTesters = @($Testers -split "[,; ]+" | Where-Object { $_ -match "^[^@\s]+@[^@\s]+\.[^@\s]+$" } | ForEach-Object { $_.ToLower() } | Select-Object -Unique)
if ($Testers -and $listaTesters.Count -eq 0) { Fallo "-Testers no tiene correos válidos." }
# Quien despliega también entra a Ruta de historias con su propio usuario.
$listaTesters = @(@($cuenta.ToLower()) + $listaTesters | Select-Object -Unique)
$sqlExiste = Existe sql instances describe $Sql

Write-Host "Cuenta:            $cuenta"
Write-Host "Proyecto:          $Proyecto ($numero), región $Region"
Write-Host "SmartBacklogIA:    $RutaSmartBacklog (rama $rama)"
Write-Host "Acceso:            $Acceso"
Write-Host "Usuarios:          $($listaTesters -join ', ')"
Write-Host ""
Write-Host "Se va a crear o actualizar:" -ForegroundColor Yellow
Write-Host "  - APIs, repositorio de imágenes '$Ar', cuentas sa-hu-flujo y sa-smartbacklog con sus permisos"
Write-Host "  - Lectura y escritura de sa-hu-flujo en gs://$Bucket (sube documentos y guarda resultados en salidas_hu/)"
if (-not $sqlExiste) {
    Write-Host "  - Cloud SQL '$Sql' PostgreSQL 16, db-f1-micro, 10 GB. COSTO FIJO aprox. 8-10 USD/mes mientras exista." -ForegroundColor Yellow
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
          "secretmanager.googleapis.com", "aiplatform.googleapis.com", "iam.googleapis.com")
if ($Acceso -eq "iap") { $apis += "iap.googleapis.com" }
G services enable @apis

Paso "Repositorio de imágenes"
if (-not (Existe artifacts repositories describe $Ar --location=$Region)) {
    G artifacts repositories create $Ar --repository-format=docker --location=$Region --description="Ruta de historias y SmartBacklogIA"
}
# Cloud Build construye con la cuenta de servicio de Compute por defecto en proyectos recientes.
$saBuild = "$numero-compute@developer.gserviceaccount.com"
G artifacts repositories add-iam-policy-binding $Ar --location=$Region --member="serviceAccount:$saBuild" --role=roles/artifactregistry.writer | Out-Null
foreach ($rol in @("roles/logging.logWriter", "roles/storage.objectViewer")) {
    G projects add-iam-policy-binding $Proyecto --member="serviceAccount:$saBuild" --role=$rol --condition=None | Out-Null
}

Paso "Cuentas de servicio y permisos"
foreach ($sa in @(@("sa-hu-flujo", "Ruta de historias (Cloud Run)"), @("sa-smartbacklog", "SmartBacklogIA (Cloud Run)"))) {
    if (-not (Existe iam service-accounts describe "$($sa[0])@$Proyecto.iam.gserviceaccount.com")) {
        G iam service-accounts create $sa[0] --display-name="$($sa[1])"
    }
}
foreach ($sa in @($SaHu, $SaSb)) {
    G projects add-iam-policy-binding $Proyecto --member="serviceAccount:$sa" --role=roles/aiplatform.user --condition=None | Out-Null
}
G projects add-iam-policy-binding $Proyecto --member="serviceAccount:$SaSb" --role=roles/cloudsql.client --condition=None | Out-Null
G storage buckets add-iam-policy-binding "gs://$Bucket" --member="serviceAccount:$SaHu" --role=roles/storage.objectAdmin | Out-Null
G storage buckets add-iam-policy-binding "gs://$Bucket" --member="serviceAccount:$SaSb" --role=roles/storage.objectViewer | Out-Null

# ------------------------------------------------------------------ base de datos y secretos
Paso "Cloud SQL (la primera vez tarda 5-10 minutos)"
if (-not $sqlExiste) {
    G sql instances create $Sql --database-version=POSTGRES_16 --edition=ENTERPRISE --tier=db-f1-micro `
        --region=$Region --storage-size=10 --storage-type=HDD --storage-auto-increase --availability-type=zonal
}
$conexion = "$($Proyecto):$($Region):$Sql"
if (-not (Existe sql databases describe spb --instance=$Sql)) { G sql databases create spb --instance=$Sql }
if (-not (LeerSecreto "sbia-database-url")) {
    $pg = NuevaClave 24
    $hayUsuario = & gcloud sql users list --instance=$Sql --project=$Proyecto --format="value(name)" 2>$null | Where-Object { $_ -eq "spb" }
    if ($hayUsuario) {
        G sql users set-password spb --instance=$Sql --password=$pg
    } else {
        G sql users create spb --instance=$Sql --password=$pg
    }
    GuardarSecreto "sbia-database-url" "postgresql+psycopg://spb:$pg@/spb?host=/cloudsql/$conexion" @($SaSb)
}

Paso "Secretos de SmartBacklogIA"
if (-not (LeerSecreto "sbia-session-secret")) { GuardarSecreto "sbia-session-secret" (NuevaClave 48) @($SaSb) }
$adminPass = LeerSecreto "sbia-admin-password"
if (-not $adminPass) { $adminPass = NuevaClave 16; GuardarSecreto "sbia-admin-password" $adminPass @($SaSb) }

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
Desplegar "smartbacklog" @("--image=$ImgSb", "--service-account=$SaSb", "--add-cloudsql-instances=$conexion",
    "--env-vars-file=$envSb", "--set-secrets=$secretosSb", "--memory=1Gi", "--cpu=1", "--no-cpu-throttling",
    "--min-instances=0", "--max-instances=2", "--timeout=900")
$urlSb = & gcloud run services describe smartbacklog --region=$Region --project=$Proyecto --format="value(status.url)"

Paso "Admin de SmartBacklogIA (job de seed)"
G run jobs deploy smartbacklog-seed --image=$ImgSb --region=$Region --service-account=$SaSb `
    --set-cloudsql-instances=$conexion --env-vars-file=$envSb --set-secrets=$secretosSb `
    --command=sh --args="-c,alembic upgrade head && python scripts/seed.py" --max-retries=0 --task-timeout=600
G run jobs execute smartbacklog-seed --region=$Region --wait

Paso "Conexión del puente (API key de servicio)"
$sesion = $null
try {
    Invoke-RestMethod -Method Post "$urlSb/api/v1/auth/login" -ContentType "application/json" -SessionVariable sesion `
        -Body (@{ email = "admin@spb.local"; password = $adminPass } | ConvertTo-Json) | Out-Null
} catch { Fallo "no pude iniciar sesión como admin en $urlSb : $($_.Exception.Message)" }
if (-not (LeerSecreto "sbia-api-key")) {
    $k = Invoke-RestMethod -Method Post "$urlSb/api/v1/admin/api-keys" -WebSession $sesion -ContentType "application/json" `
        -Body (@{ name = "hu-flujo-$Tag" } | ConvertTo-Json)
    GuardarSecreto "sbia-api-key" $k.api_key @($SaHu)
}

# ------------------------------------------------------------------ testers
Paso "Usuarios de prueba"
$claves = [ordered]@{}
$prev = LeerSecreto "hu-usuarios"
if ($prev) {
    foreach ($par in ($prev -split ";")) { if ($par -match "^([^:]+):(.+)$") { $claves[$matches[1]] = $matches[2] } }
}
$nuevos = @()
foreach ($t in $listaTesters) {
    if (-not $claves.Contains($t)) { $claves[$t] = NuevaClave 14; $nuevos += $t }
}
if ($claves.Count) {
    GuardarSecreto "hu-usuarios" ((($claves.GetEnumerator() | ForEach-Object { "$($_.Key):$($_.Value)" }) -join ";")) @($SaHu)
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
    $apiKey = LeerSecreto "sbia-api-key"
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
$secretosHu = "SBIA_API_KEY=sbia-api-key:latest"
if ($Acceso -eq "clave") { $secretosHu += ",HU_USUARIOS=hu-usuarios:latest" }
# max-instances=1: los análisis corren en segundo plano dentro del proceso y el estado usa bloqueos en memoria.
$opcionesHu = @("--image=$ImgHu", "--service-account=$SaHu", "--env-vars-file=$envHu", "--set-secrets=$secretosHu",
    "--memory=2Gi", "--cpu=1", "--no-cpu-throttling", "--min-instances=0", "--max-instances=1",
    "--concurrency=40", "--timeout=3600")

Paso "Servicio hu-flujo"
if ($Acceso -eq "iap") {
    & gcloud beta services identity create --service=iap.googleapis.com --project=$Proyecto 2>$null | Out-Null
    G run deploy hu-flujo @opcionesHu --region=$Region --no-allow-unauthenticated --iap
    G run services add-iam-policy-binding hu-flujo --region=$Region `
        --member="serviceAccount:service-$numero@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker | Out-Null
    foreach ($t in $listaTesters) {
        G iap web add-iam-policy-binding --resource-type=cloud-run --service=hu-flujo --region=$Region `
            --member="user:$t" --role=roles/iap.httpsResourceAccessor | Out-Null
    }
} else {
    Desplegar "hu-flujo" $opcionesHu
}
$urlHu = & gcloud run services describe hu-flujo --region=$Region --project=$Proyecto --format="value(status.url)"

# ------------------------------------------------------------------ resumen
Paso "Listo"
$archivo = Join-Path $Repo "deploy\credenciales_pruebas.csv"
$filas = @("servicio,url,usuario,clave")
$filas += "smartbacklog (admin),$urlSb,admin@spb.local,$adminPass"
foreach ($e in $claves.GetEnumerator()) {
    if ($Acceso -eq "clave") { $filas += "hu-flujo,$urlHu/flujo,$($e.Key),$($e.Value)" }
    else { $filas += "hu-flujo,$urlHu/flujo,$($e.Key),(cuenta de Google)" }
    $filas += "smartbacklog,$urlSb,$($e.Key),$($e.Value)"
}
Texto $archivo (($filas -join "`r`n") + "`r`n")
Write-Host "Ruta de historias:  $urlHu/flujo" -ForegroundColor Green
Write-Host "SmartBacklogIA:     $urlSb" -ForegroundColor Green
Write-Host "Credenciales en:    $archivo  (compártelas por un canal privado; el archivo no se sube a git)"
if ($nuevos.Count) { Write-Host "Testers nuevos:     $($nuevos -join ', ')" }
Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
