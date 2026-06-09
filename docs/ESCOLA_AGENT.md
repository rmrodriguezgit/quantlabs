# ESCOLA

ESCOLA es un agente RAG documental dentro del Harness de QuantLabs. Su objetivo es crear una base NoSQL de archivos del programa y responder consultas con evidencia, inferencias simples y salida lista para copiar y pegar.

## Arquitectura

```text
harness/escola/
  supervisor.py
harness/tools/escola.py
harness/api/app.py
storage/artifacts/escola/
  documents.jsonl
  chunks.jsonl
  audit.jsonl
nginx/html/dashboard/escola/
```

ESCOLA usa:

- `UploadStore` para recibir archivos desde el dashboard.
- JSONL como base NoSQL simple.
- Chunks de texto con embeddings hash locales.
- Búsqueda híbrida: similitud vectorial y coincidencia de términos.
- Respuesta formateada para copiar: resumen, respuesta, confianza, evidencia y pendientes.

## Roles

- `admin`: puede subir e ingestar archivos.
- `admin`, `teacher`, `trader`: pueden consultar ESCOLA y ver documentos indexados.

## Endpoints

Formatos: `pdf`, `docx`, `csv`, `xls`, `xlsx`, `txt`, `md`, `json`, `ipynb`, `png`, `jpg`, `jpeg`.

Subir archivo:

```http
POST /v1/files
```

Ingestar en ESCOLA:

```http
POST /v1/escola/ingest
Content-Type: application/json

{
  "file_id": "uuid-del-archivo",
  "tags": ["programa", "reglamento"]
}
```

Consultar:

```http
POST /v1/escola/query
Content-Type: application/json

{
  "question": "Que requisitos tiene el programa?",
  "top_k": 6
}
```

Listar documentos:

```http
GET /v1/escola/documents
```

Reglas:

```http
GET /v1/escola/rules
```

## Salida

La respuesta API conserva `formatted_json` para integraciones, pero la salida
principal `copy_ready` está pensada para pegar directamente en chats, reportes o
documentos: muestra encabezados, texto en negritas y evidencia humanizada, sin
volcar el JSON de la base NoSQL ni separadores de tabla crudos.

````markdown
# ESCOLA

## Respuesta
**Consulta:** ...

**Resumen:** ...

**Respuesta:**
...

**Confianza:** media

**Pendientes:** Ninguno

## Evidencia consultada
1. archivo.pdf (score 0.42): ...
````

## Seguridad

ESCOLA no ejecuta acciones externas ni modifica documentos fuente. Si la evidencia es débil, marca la confianza como baja y agrega pendientes para revisión humana.
