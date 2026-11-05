# Despliegue de pruebas en Cloud Run

Publica los dos servicios en `servi-modelos-ia-dev` para que otras personas prueben el flujo completo:

| Servicio | Qué es | Acceso |
|---|---|---|
| `hu-flujo` | **Ruta de historias** (este repo): documentos, análisis multiagente, revisión, estimación, priorización y publicación | Usuario y clave por persona |
| `smartbacklog` | **SmartBacklogIA** (rama `gcp-vertex`): recibe las historias publicadas y sus 5 etapas se aprueban por rol | Su propio inicio de sesión; cada tester es product owner del proyecto `SINCHIHU` |

## Requisitos (una vez)

- Google Cloud SDK con sesión: `gcloud auth login`. Basta el rol **Editor** del proyecto: el script no cambia permisos IAM.
  Los servicios corren con la cuenta de Compute por defecto, que en `servi-modelos-ia-dev` ya tiene Vertex AI,
  Storage, Secret Manager y Cloud Run Admin. El único paso que necesita Cloud Run Admin (abrir los servicios al
  público, cada uno con su propio inicio de sesión) lo ejecuta Cloud Build como esa cuenta.
- SmartBacklogIA actualizado en `E:\Doctorado\ing_soft_IA\SmartBacklogIA` (rama `gcp-vertex`, con el commit
  «Despliegue en Cloud Run»).

## Desplegar

Desde la raíz de este repo, en PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\desplegar_gcp.ps1 -Testers "ana@correounivalle.edu.co,pedro@gmail.com"
```

(`-ExecutionPolicy Bypass` evita el bloqueo de Windows a scripts sin firma, como los que vienen de Google Drive.)

El script muestra el plan y pide escribir `SI`. La primera vez tarda unos 20 minutos (Cloud SQL y las dos imágenes).
Al final escribe `deploy\credenciales_pruebas.csv` con las URL, usuarios y claves (no se sube a git).
Tú quedas incluido con tu cuenta de gcloud. Las claves generadas quedan en `deploy\estado_despliegue.json`
(tampoco se sube a git): **consérvalo**, porque con rol Editor no se pueden leer de vuelta desde Secret Manager y
las siguientes ejecuciones lo necesitan.

- Agregar testers después: vuelve a ejecutarlo con los correos nuevos; los demás conservan su clave.
- Llevar los resultados locales (`salidas_hu`) a la nube: agrega `-SubirResultados`.

## Costos y cómo controlarlos

- Cloud SQL `db-f1-micro`: costo fijo de unos 8–10 USD/mes mientras exista (más el disco).
- Cloud Run: se cobra por uso; sin tráfico escala a cero.
- Gemini en Vertex AI: por uso, cada análisis o generación. Conviene una alerta de presupuesto en el proyecto.

```powershell
.\deploy\pausar_gcp.ps1 -Accion pausar      # apaga Cloud SQL entre sesiones de prueba
.\deploy\pausar_gcp.ps1 -Accion reanudar
.\deploy\pausar_gcp.ps1 -Accion eliminar    # borra todo lo creado (no toca el bucket)
```

## Cómo funciona

- Las imágenes se construyen con Cloud Build desde tus carpetas locales y quedan en Artifact Registry `ruta-historias`.
- `hu-flujo` lee los documentos de `gs://adk_ing/` y guarda resultados y estado en `gs://adk_ing/salidas_hu/`.
  Corre con una sola instancia (los análisis se ejecutan en segundo plano dentro del proceso); deja la página
  abierta mientras un análisis está en curso.
- Las decisiones las firma el usuario autenticado (`HU_ACCESO`), no el nombre escrito en el navegador.
- `smartbacklog` usa Cloud SQL por socket y Vertex AI con la cuenta de Compute, y lee los documentos del bucket.
  Su admin (`admin@spb.local`) tiene una clave aleatoria guardada en Secret Manager (`sbia-admin-password`).
- El puente usa una API key de servicio creada automáticamente (`sbia-api-key`).
- Secretos en Secret Manager: `hu-usuarios`, `sbia-api-key`, `sbia-admin-password`, `sbia-session-secret`,
  `sbia-database-url`.
