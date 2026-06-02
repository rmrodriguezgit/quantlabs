# Harness Hybrid Specialists

## Objetivo

Los especialistas deben combinar lógica propia, herramientas locales y LLM Local. La regla de diseño es:

1. La herramienta obtiene datos o ejecuta una validación objetiva.
2. El especialista aplica reglas de negocio y formatea evidencia.
3. LLM Local sintetiza la respuesta final sin inventar datos ni cambiar candados.

## Agentes que ya usaban LLM Local

- Planner
- Research
- Execution
- Ramas genéricas de Coding y Finance

Estos heredan de `BaseAgent` y usan `AgentLoop -> LlamaClient`.

## Agentes ajustados a híbrido

- Coding: pruebas GPU/Jupyter ahora hacen herramienta + síntesis LLM.
- Codex4U: hereda el comportamiento híbrido de Coding.
- File Analyst: microservicio local + síntesis LLM.
- Polymrkt: señal determinística + research + síntesis LLM. El LLM no puede cambiar ejecución ni candados.
- Dexter: research local + síntesis LLM.
- Finance: scalping técnico, deep research, paper trading, Polymarket y MEXC ahora hacen herramienta + síntesis LLM.
- Validation: observabilidad real + síntesis LLM.

## Finance Scalping

Se agregó `financial.technical_scalping` con:

- RSI14
- MACD histogram
- EMA20/EMA50
- VWAP
- ATR14
- soporte/resistencia 20 velas
- señal LONG WATCH, SHORT WATCH o NO TRADE
- stop/target sugeridos por ATR

Prompt esperado:

`finance: análisis de BTC-USD scalping 1d`

Flujo esperado:

`financial.technical_scalping -> llm_local_synthesis`

## Seguridad

- El LLM Local no ejecuta órdenes.
- Polymarket live sigue bajo candados operativos.
- Si LLM Local falla, el Harness conserva la respuesta determinística.
- Cada síntesis se registra como evento `llm_local_synthesis`.

## Decision Router

Se agregó `agents.decision_router.DecisionRouter` como primera capa del Harness. Su objetivo es escoger el camino más rápido sin perder control:

- `rule`: respuesta determinística inmediata, sin LLM ni herramienta.
- `tool`: reservado para herramientas directas cuando no se necesita síntesis.
- `hybrid`: herramienta/regla del especialista primero y LLM Local después para explicación.
- `llm_local`: preguntas conceptuales o ambiguas sin regla confiable.

Cada ejecución registra un primer evento `decision_router` con `route`, `confidence`, `reason`, `tool`, `risk` y `expected_latency_ms`.

Ejemplo: `como reinicio nginx en docker` responde por regla directa con `docker restart quantlab_nginx`. Polymarket y finance scalping quedan en ruta `hybrid` porque necesitan datos reales, reglas de negocio y explicación.
