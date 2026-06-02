# QuantLabs Agent Harness

## Propósito

El Harness es la capa de agentes de QuantLabs: recibe prompts, selecciona especialistas, agrega contexto, ejecuta herramientas locales y conserva evidencia operativa. La unidad persistente ya no debe ser el chat; el chat es interfaz. La unidad estable es el proyecto.

## Capas

1. Interfaz: `/dashboard/harness/`
   - Chat conversacional.
   - Selector de especialista.
   - Adjuntos.
   - Panel de proyecto.
   - Catálogo visual de microservicios.

2. API Harness: `/harness-api/v1/*`
   - Conversaciones.
   - Proyectos.
   - Memoria RAG.
   - Catálogo de microservicios.
   - Automatizaciones y reglas.

3. Engine
   - `HarnessEngine.chat()` crea tarea, ejecuta agente, sintetiza respuesta y registra trayectoria.

4. Especialistas
   - `planner`: planeación.
   - `coding`: implementación general.
   - `codex4u`: Ubuntu, Docker, Python, shell, JS, Node, HTML/CSS. Es preferido cuando el modelo activo es Qwen Coder.
   - `finance`: mercado accionario y análisis financiero.
   - `polymrkt`: Polymarket BTC Up/Down, señal 5m/15m, CLOB, Kelly y validación.
   - `dexter`: research financiero contextual.
   - `file_analyst`: documentos privados con infraestructura local.
   - `research`, `validation`, `execution`: investigación, verificación y operación.

## Proyecto Persistente

El proyecto conserva conocimiento aunque se borren chats.

Ruta interna:

`/app/conversations/_projects/<usuario>/<project_id>/`

Archivos principales:

- `project.json`: metadata, nombre, política de memoria, archivos vinculados.
- `PROJECT.md`: documentación viva del proyecto.

Proyecto por defecto:

`QuantLabs Workspace` (`quantlabs-workspace`)

Este nombre reemplaza el uso genérico anterior de “QuantLab AI Capital” como nombre fijo de panel.

## RAG

La memoria RAG se etiqueta con:

- `user_id`
- `project_id`
- `session_id`
- `agent`
- `model`
- `text_hash`

Reglas:

- El RAG se recupera por especialista.
- El RAG se recupera por proyecto.
- Se deduplican textos por hash.
- Los bloques RAG incrustados no se vuelven a memorizar.
- Borrar un chat no elimina memoria del proyecto.
- `polymrkt` no usa RAG conversacional por defecto para evitar contaminar señales de trading con contexto viejo.

## Microservicios

El catálogo vive en `/harness-api/v1/microservices/catalog` y alimenta la vista tipo n8n del panel Complementos.

Cada nodo declara:

- `id`
- `label`
- `category`
- `purpose`
- `inputs`
- `outputs`
- `reusable_for`
- `roles`

Flujos recomendados iniciales:

- Polymarket Predictivo: `dexter_research -> polymarket -> paper_trading`
- Documentos Privados: `file_analyst -> research -> planner`
- GPU Model Lab: `jupyter_gpu -> python -> file`

## Reglas de Negocio Harness

1. No mezclar memoria de especialistas.
2. No usar RAG de otro proyecto.
3. No exponer secretos ni `.env`.
4. No ejecutar trading live desde agentes conversacionales.
5. Polymarket operativo queda bajo `paper_trading` y sus candados.
6. Las herramientas visibles dependen del rol JWT.
7. Toda tarea conserva metadata, eventos, duración, herramienta principal y trayectoria.
8. File Analyst trabaja local, sin APIs externas.

## Endpoints Clave

- `GET /harness-api/v1/status?session_id=...`
- `GET /harness-api/v1/conversations`
- `POST /harness-api/v1/conversations`
- `DELETE /harness-api/v1/conversations/<id>`
- `GET /harness-api/v1/projects`
- `POST /harness-api/v1/projects`
- `GET /harness-api/v1/projects/<project_id>`
- `PATCH /harness-api/v1/projects/<project_id>`
- `POST /harness-api/v1/projects/<project_id>/memory`
- `GET /harness-api/v1/microservices/catalog`
- `GET /harness-api/v1/tools`
- `GET /harness-api/v1/agents`
- `POST /harness-api/v1/chat`

## Operación

Después de cambios Python del Harness:

```bash
cd /home/quantlab/quantlab-ai-capital
docker compose up -d --build harness
docker exec quantlab_nginx nginx -t && docker exec quantlab_nginx nginx -s reload
```

Verificación mínima:

```bash
docker compose ps
docker logs --since=10m quantlab_harness
curl -s http://127.0.0.1:5000/healthz
```

Desde nginx público, las rutas requieren sesión JWT.

## Cómo Crear un Nuevo Proyecto

1. Abrir Harness.
2. Panel Proyecto.
3. Nuevo proyecto.
4. Vincular archivos activos si aplica.
5. Usar especialistas normalmente.
6. El RAG nuevo quedará etiquetado con ese proyecto.

## Cómo Reutilizar Microservicios

1. Abrir Complementos.
2. Revisar nodos por categoría.
3. Identificar entradas y salidas.
4. Reusar un flujo recomendado o combinar nodos.
5. Documentar la decisión en `PROJECT.md`.

Ejemplo:

`file_analyst -> research -> planner` sirve para revisar contratos, reportes, PDFs o documentos internos y convertirlos en plan operativo.

`dexter_research -> polymarket -> paper_trading` sirve para separar contexto, señal y ejecución controlada.

## Auditoría Pendiente Recomendada

- Añadir editor visual drag-and-drop real para flujos.
- Guardar workflows reutilizables como JSON/YAML.
- Agregar pruebas unitarias para `ProjectStore` y aislamiento RAG.
- Añadir endpoint para purga selectiva de RAG por proyecto/especialista.
