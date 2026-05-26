#!/usr/bin/env bash
set -euo pipefail

STATE_PATH="/home/quantlab/quantlab-runtime/harness/storage/artifacts/paper_trading_table_exports/active_24h_state.json"

/usr/bin/docker exec quantlab_harness sh -lc "cd /app && python3 /app/storage/scripts/export_polymarket_table.py"

if [ -f "$STATE_PATH" ] && grep -q '"status": "complete"' "$STATE_PATH"; then
  /usr/bin/systemctl disable --now quantlab-polymarket-table-export.timer
fi
