#!/bin/bash
echo "====== DIAGNÓSTICO POLYMARKET BOT ======"

echo ""
echo "--- 1. Estado del contenedor harness ---"
docker compose ps harness

echo ""
echo "--- 2. Variables de entorno clave ---"
docker compose exec -T harness sh -c 'env | grep -iE "dry|live|mode|polymarket|clob|api_key|private_key|funder" 2>/dev/null || echo "No se encontraron variables relevantes"'

echo ""
echo "--- 3. Últimas 50 líneas de logs ---"
docker compose logs harness --tail=50 --no-log-prefix

echo ""
echo "--- 4. Crontab activo ---"
docker compose exec -T harness crontab -l 2>/dev/null || echo "Sin crontab"

echo ""
echo "--- 5. Procesos activos ---"
docker compose exec -T harness ps aux 2>/dev/null | grep -v grep

echo ""
echo "--- 6. Archivos del agente polymarket ---"
docker compose exec -T harness find /app -name "*poly*" -o -name "*market*" -o -name "*cron*" 2>/dev/null

echo "====== FIN DEL DIAGNÓSTICO ======"
