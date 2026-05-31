#!/bin/bash
set -euo pipefail

PROJECT_DIR="/home/quantlab/quantlab-ai-capital"
RUNTIME_DIR="${QUANTLAB_RUNTIME_DIR:-/home/quantlab/quantlab-runtime}"
MODELS_DIR="${RUNTIME_DIR}/models"
ENV_FILE="${PROJECT_DIR}/.env"
COMPOSE="${PROJECT_DIR}/docker-compose.yml"
CURRENT_LINK="${MODELS_DIR}/current-model.gguf"

case "${1:-}" in
  nous-hermes)
    MODEL="Nous-Hermes-2-Mistral-7B-DPO.Q4_K_M.gguf"
    TEMPLATE="chatml"
    ;;
  qwen)
    MODEL="Qwen2.5-14B-Instruct-Q4_K_M.gguf"
    TEMPLATE="chatml"
    ;;
  qwen-coder)
    MODEL="Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"
    TEMPLATE="chatml"
    ;;
  phi4)
    MODEL="phi-4-Q4_K_M.gguf"
    TEMPLATE="chatml"
    ;;
  *)
    echo "Uso: $0 {qwen|qwen-coder|phi4|nous-hermes}"
    echo ""
    echo "Modelos disponibles:"
    find "$MODELS_DIR" -maxdepth 1 -type f -name "*.gguf" -printf "%f\n" | sort
    exit 1
    ;;
esac

if [ ! -f "${MODELS_DIR}/${MODEL}" ]; then
  echo "Modelo no encontrado: ${MODELS_DIR}/${MODEL}"
  exit 1
fi

cd "$PROJECT_DIR"
sed -i "s/^LLM_MODEL=.*/LLM_MODEL=${MODEL}/" "$ENV_FILE"
sed -i "s/^LLM_CHAT_TEMPLATE=.*/LLM_CHAT_TEMPLATE=${TEMPLATE}/" "$ENV_FILE"
ln -sfn "${MODELS_DIR}/${MODEL}" "$CURRENT_LINK"

echo "Cambiando modelo a: ${MODEL}"
docker compose -f "$COMPOSE" up -d --force-recreate llm

echo "Esperando que el modelo responda..."
sleep 8
curl -fsS http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"system\",\"content\":\"Responde en español.\"},{\"role\":\"user\",\"content\":\"Confirma el modelo activo en una frase.\"}],\"max_tokens\":60}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
