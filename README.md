# QuantLabs

QuantLabs es una plataforma privada de investigacion, automatizacion y operacion financiera con agentes de IA, herramientas de analisis cuantitativo, integracion GPU/Jupyter, API propia y modulos especializados para MEXC y Polymarket.

## Componentes principales

- Auth: autenticacion JWT, usuarios, roles e invitaciones.
- API QuantLab: servicios financieros, indicadores, modelos y endpoints internos.
- Agent Harness: orquestador de agentes para finance, polymrkt, coding, planner, research, validation y execution.
- Polymrkt Agent: flujo especializado para senales de Polymarket, analisis BTC Up/Down, validacion y riesgo.
- Jupyter GPU: entorno prioritario para tareas de Deep Learning, Torch, CUDA y entrenamiento de modelos.
- Nginx: gateway web con proteccion por autenticacion.
- Websocket: canal de comunicacion en tiempo real.

## Runtime externo

Los datos persistentes viven fuera del repositorio:

    /home/quantlab/quantlab-runtime

Importante: no aplicar chown -R quantlab:quantlab sobre todo quantlab-runtime, porque Postgres requiere ownership interno 999:999 en postgres_auth_data.

## Seguridad

Este repositorio no debe contener secretos, tokens, bases de datos, modelos pesados ni artefactos runtime.

Usar .env.example como plantilla de configuracion.

Archivos sensibles excluidos:

- .env
- flask/.env
- harness/storage
- postgres_auth_data
- ollama_data
- models
- backups
- certificados, llaves privadas y wallets

## Operacion

Levantar servicios:

    docker compose up -d

Ver estado:

    docker compose ps

Ejecutar pruebas criticas:

    docker compose exec -T harness python3 -m pytest -q /app/tests/test_status_payload.py /app/tests/test_finance_agent.py /app/tests/test_polymarket_tool.py

## Git

Repositorio remoto:

    git@github.com:rmrodriguezgit/quantlabs.git
