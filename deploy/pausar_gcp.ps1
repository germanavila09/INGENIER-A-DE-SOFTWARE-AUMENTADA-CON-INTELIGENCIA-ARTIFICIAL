<#
.SYNOPSIS
  Controla el costo del despliegue de pruebas.

.EXAMPLE
  .\deploy\pausar_gcp.ps1 -Accion pausar     # apaga Cloud SQL (deja de cobrar CPU; el disco sí se cobra)
  .\deploy\pausar_gcp.ps1 -Accion reanudar   # vuelve a encender Cloud SQL
  .\deploy\pausar_gcp.ps1 -Accion eliminar   # borra servicios, job, Cloud SQL, secretos e imágenes
#>
param(
    [Parameter(Mandatory)][ValidateSet("pausar", "reanudar", "eliminar")][string]$Accion,
    [string]$Proyecto = "servi-modelos-ia-dev",
    [string]$Region = "us-central1"
)
$ErrorActionPreference = "Continue"
function G { & gcloud @args --project=$Proyecto --quiet; if ($LASTEXITCODE -ne 0) { Write-Host "  (falló: gcloud $($args -join ' '))" -ForegroundColor Yellow } }

switch ($Accion) {
    "pausar" {
        G sql instances patch spb-pg --activation-policy=NEVER
        Write-Host "Cloud SQL apagado. SmartBacklogIA no funcionará hasta reanudar; Ruta de historias sigue disponible (sin publicar)."
    }
    "reanudar" {
        G sql instances patch spb-pg --activation-policy=ALWAYS
        Write-Host "Cloud SQL encendido."
    }
    "eliminar" {
        Write-Host "Se borrarán: servicios hu-flujo y smartbacklog, job smartbacklog-seed, Cloud SQL spb-pg (con TODOS los datos"
        Write-Host "de SmartBacklogIA), los secretos y el repositorio de imágenes ruta-historias." -ForegroundColor Yellow
        Write-Host "No se tocan el bucket adk_ing ni los resultados en gs://adk_ing/salidas_hu/."
        if ((Read-Host "Escribe ELIMINAR para continuar") -ne "ELIMINAR") { Write-Host "Cancelado."; exit 1 }
        G run services delete hu-flujo --region=$Region
        G run services delete smartbacklog --region=$Region
        G run jobs delete smartbacklog-seed --region=$Region
        G sql instances delete spb-pg
        foreach ($s in @("hu-usuarios", "sbia-api-key", "sbia-admin-password", "sbia-session-secret", "sbia-database-url")) {
            G secrets delete $s
        }
        G artifacts repositories delete ruta-historias --location=$Region
        Write-Host "Listo. Las cuentas de servicio sa-hu-flujo y sa-smartbacklog se conservan (no cuestan)."
    }
}
