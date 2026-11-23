# Carpeta de documentos para los agentes IA

Cada proyecto tiene su subcarpeta con la **clave del proyecto** (en mayúsculas):

```
documents/
  PAGO/
    proceso-pago.bpmn      ← diagrama BPMN exportado desde Camunda, Bizagi, draw.io, etc.
    reunion-12-09.pdf      ← transcripción o acta en PDF (con texto, no escaneado)
    requisitos.md          ← notas en Markdown
    acta.docx              ← documento de Word
    reunion.txt            ← texto plano
```

Formatos aceptados: `.pdf`, `.docx`, `.bpmn`, `.md`, `.txt` (máximo 20 MB por archivo).

- Un proyecto solo puede leer su propia subcarpeta.
- El PDF debe tener texto seleccionable. Si es un escaneo, primero pásalo por OCR.
- Los archivos se envían a Google (Gemini) al generar HU: usa solo documentos que tu cuenta esté autorizada a procesar.
  Con una API key del nivel gratuito de AI Studio, Google puede usar el contenido para mejorar sus productos; para documentos de clientes usa una clave con facturación activa.
- Esta carpeta se monta en el contenedor como solo lectura (`/data/documents`).
- Los documentos reales no se versionan en git (ver `.gitignore`); los de ejemplo sí.
