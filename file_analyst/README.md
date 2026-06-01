# file_analyst

Microservicio privado de QuantLabs para analizar archivos dentro de la infraestructura local.

No usa Anthropic ni servicios externos de IA. Extrae texto de documentos y llama al endpoint OpenAI-compatible del LLM local (`llama.cpp`) en `http://llm:8080/v1/chat/completions`. Si el LLM no responde con JSON válido, devuelve un análisis extractivo de respaldo.

## Endpoints

- `GET /health`
- `POST /analyze` con multipart `file`, `mode`, `language`
- `POST /analyze/text` con JSON `{ "text": "...", "mode": "specialist", "language": "es" }`

## Modos

- `chatbot`: análisis rápido para conversación.
- `live`: baja latencia, mismo formato.
- `automata`: batch estable.
- `specialist`: análisis profundo con más observaciones y acciones.

## Uso desde Harness

Harness usa la herramienta `file_analyst`, que llama internamente a `http://file_analyst:8010`.

Ejemplo lógico:

1. El usuario sube un PDF, DOCX, TXT, XLSX, CSV, JSON o MD al Harness.
2. Harness guarda el archivo en `/app/uploads`.
3. El agente llama `file_analyst` con `file_id` o `path`.
4. El microservicio extrae contenido, analiza con LLM local y devuelve JSON estructurado.
5. Harness resume el resultado en la conversación y puede guardar artefacto JSON.

## Seguridad

- No expone secrets.
- No acepta formatos ejecutables.
- No llama APIs externas de IA.
- Se comunica solo dentro de Docker network.
