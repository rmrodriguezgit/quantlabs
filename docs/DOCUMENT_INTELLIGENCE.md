# Document Intelligence

Document Intelligence es una entidad propia dentro del Harness de QuantLabs. Vive en el mismo runtime para aprovechar autenticación, storage, API y auditoría existentes, pero está separada lógicamente del trading, OCEAN y validación.

## Decisión de arquitectura

Se implementa dentro del Harness como módulo autónomo:

```text
harness/document_intelligence/
  supervisor.py
harness/tools/document_intelligence.py
harness/api/app.py
storage/artifacts/document_intelligence/
  extracted/
  analysis/
  audit/
```

La razón para no separarlo todavía como microservicio es velocidad y control: el MVP puede operar con los archivos, usuarios y permisos actuales. Cuando el volumen de OCR crezca o haga falta escalar workers pesados, el módulo puede extraerse a un servicio propio sin cambiar la lógica de negocio.

## Objetivo

Procesar documentos PDF, imágenes, Excel, CSV, texto o JSON para:

- Extraer texto y tablas.
- Detectar cliente, correo, RFC, teléfono, monto, fecha y concepto.
- Usar una guía opcional de extracción para priorizar campos solicitados.
- Verificar campos críticos.
- Decidir si requiere revisión humana.
- Generar un borrador de correo sin enviarlo automáticamente.
- Guardar evidencia en auditoría.

## Flujo

```text
Archivo subido
  -> IngestAgent
  -> ExtractorAgent
  -> AnalysisAgent
  -> VerificationAgent
  -> ClientAgent
  -> EmailDraftAgent
  -> AuditAgent
```

El supervisor decide el siguiente agente:

- `OCRAgent`: si el documento no tiene texto legible.
- `ClientAgent`: si no se identifica cliente.
- `VerificationAgent`: si faltan datos o hay contradicciones.
- `EmailDraftAgent`: si hay cliente, correo único y confianza suficiente.
- `AuditAgent`: si solo debe registrarse.

## Seguridad

Document Intelligence inicia en modo revisión asistida.

Reglas:

- No envía correos automáticamente.
- No automatiza si falta cliente.
- No automatiza si no hay correo único válido.
- No automatiza si hay contradicciones.
- El prompt de extracción solo guía qué campos buscar; no autoriza acciones ni envío.
- Si el prompt pide campos que no se encuentran, el documento queda en revisión humana.
- Requiere revisión humana si la confianza es menor a `0.85`.
- Siempre guarda auditoría JSONL.

## Formatos soportados

- PDF con texto embebido.
- Imágenes `png`, `jpg`, `jpeg` usando OCR Tesseract.
- Excel `xls`, `xlsx`.
- CSV.
- Texto `txt`, `md`.
- JSON.

Nota: un PDF escaneado sin texto puede requerir una fase posterior de OCR por página. El MVP detecta el caso y lo manda a revisión/OCR.

## Uso por API

Primero se sube el archivo al endpoint existente:

```http
POST /v1/files
```

Después se procesa con:

```http
POST /v1/document-intelligence/process
Content-Type: application/json

{
  "file_id": "uuid-del-archivo",
  "language": "spa",
  "dry_run": true,
  "extraction_prompt": "Extrae cliente, RFC, monto, fecha de vencimiento, folio y riesgos."
}
```

También puede procesarse un path permitido por política:

```json
{
  "path": "/app/uploads/shared/documento.xlsx",
  "language": "spa",
  "dry_run": true,
  "extraction_prompt": "Identifica cliente, correo, total y observaciones."
}
```

`extraction_prompt` es opcional. Sirve para guiar la extracción en documentos
genéricos o variables, por ejemplo facturas, contratos, reportes o capturas. El
supervisor convierte la guía en campos solicitados y los cruza contra el texto
extraído. Si faltan, aparecen en `verification.prompt_missing_fields`.

## Respuesta esperada

La API devuelve un `ToolResult` del Harness. El resultado útil está en `output`:

```json
{
  "ok": true,
  "output": {
    "document_id": "...",
    "status": "draft_ready",
    "analysis": {
      "cliente": "ACME SA de CV",
      "correo": "contacto@acme.mx",
      "rfc": "ACM010203AB1",
      "monto": 12500.5,
      "concepto": "Renovacion de servicio",
      "riesgos": []
    },
    "guidance": {
      "extraction_prompt": "Extrae cliente, RFC, monto, fecha de vencimiento, folio y riesgos.",
      "requested_fields": ["cliente", "fecha_vencimiento", "folio", "monto", "rfc", "riesgos"],
      "prompt_field_hits": {
        "cliente": true,
        "fecha_vencimiento": false
      }
    },
    "verification": {
      "confidence": 0.8,
      "prompt_missing_fields": ["fecha_vencimiento"],
      "requires_human_review": true,
      "safe_to_email": false
    },
    "communication": {
      "status": "ready_to_send_with_approval",
      "to": "contacto@acme.mx",
      "subject": "Seguimiento de documento recibido",
      "body": "...",
      "auto_send_enabled": false
    },
    "next_agent": "EmailDraftAgent"
  }
}
```

## Uso como herramienta Harness

```python
tools.execute(
    "document_intelligence",
    role="teacher",
    file_id="uuid-del-archivo",
    language="spa",
    dry_run=True,
    extraction_prompt="Extrae cliente, correo, monto y fecha de vencimiento.",
)
```

Para ver reglas:

```python
tools.execute("document_intelligence", role="teacher", action="rules")
```

## Auditoría

Cada proceso guarda:

- Texto extraído en `storage/artifacts/document_intelligence/extracted/<document_id>.txt`.
- Análisis completo en `storage/artifacts/document_intelligence/analysis/<document_id>.json`.
- Bitácora diaria en `storage/artifacts/document_intelligence/audit/YYYY-MM-DD.jsonl`.

La bitácora incluye:

- `document_id`
- fecha
- estado
- siguiente agente
- confianza
- si era seguro preparar correo
- fuente del archivo

## Roadmap

Fase 1 actual:

- Extracción PDF/texto/imagen/Excel/CSV/JSON.
- Análisis por reglas.
- Verificación.
- Borrador de correo.
- Auditoría.

Fase 2:

- Dashboard propio en `nginx/html/dashboard/documents/`.
- Cola de documentos pendientes.
- Aprobación humana desde UI.
- Base de clientes/contactos.

Fase 3:

- OCR por página para PDFs escaneados.
- Clasificación de tipo documental.
- Validación contra CRM/ERP.
- Envío de correo con aprobación explícita.

Fase 4:

- Servicio externo con workers OCR si el volumen lo requiere.
