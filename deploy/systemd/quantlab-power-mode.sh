#!/usr/bin/env bash
set -Eeuo pipefail

ACTION="${1:-status}"
ROOT="/home/quantlab/quantlab-ai-capital"
LOG_DIR="${ROOT}/logs"
LOG_FILE="${LOG_DIR}/power-mode.log"

mkdir -p "${LOG_DIR}"

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "${LOG_FILE}"
}

docker_safe() {
  if ! command -v docker >/dev/null 2>&1; then
    log "docker_not_found"
    return 1
  fi
  docker "$@" 2>&1 | tee -a "${LOG_FILE}" || true
}

systemctl_safe() {
  systemctl "$@" 2>&1 | tee -a "${LOG_FILE}" || true
}

night_mode() {
  log "night_mode_start"
  systemctl_safe stop quantlab-paper-trading.timer
  systemctl_safe stop quantlab-paper-trading.service
  systemctl_safe stop quantlab-gpu-idle-governor.timer
  systemctl_safe stop quantlab-gpu-idle-governor.service

  docker_safe stop \
    quantlab_llm \
    quantlab_ollama \
    quantlab_market_gpu \
    jupyter_quantlab_gpu \
    jupyter_quantlab_lite \
    quantlab_file_analyst \
    quantlab_harness \
    quantlab_websocket \
    bitcoind

  log "night_mode_done"
}

day_mode() {
  log "day_mode_start"
  docker_safe start \
    quantlab_postgres_auth \
    quantlab_auth \
    quantlab_nginx \
    quantlab_api \
    quantlab_websocket \
    quantlab_harness \
    quantlab_file_analyst \
    quantlab_llm \
    quantlab_ollama \
    quantlab_market_gpu \
    jupyter_quantlab_gpu \
    jupyter_quantlab_lite \
    bitcoind

  systemctl_safe start quantlab-gpu-idle-governor.timer
  systemctl_safe start quantlab-paper-trading.timer
  log "day_mode_done"
}

status_mode() {
  log "status"
  docker_safe ps --format '{{.Names}}\t{{.Status}}'
  systemctl_safe list-timers quantlab-night-mode.timer quantlab-day-mode.timer --no-pager
}

case "${ACTION}" in
  night) night_mode ;;
  day) day_mode ;;
  status) status_mode ;;
  *)
    echo "usage: $0 {night|day|status}" >&2
    exit 2
    ;;
esac
