#!/bin/bash
COMPOSE="/home/quantlab/quantlab-ai-capital/docker-compose.yml"

case "$1" in
  nous-hermes)
    MODEL="Nous-Hermes-2-Mistral-7B-DPO.Q4_K_M.gguf"
    TEMPLATE="chatml"
    ;;
  llama3)
    MODEL="Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
    TEMPLATE="llama3"
    ;;
  mistral)
    MODEL="Mistral-Nemo-Instruct-2407-Q4_K_M.gguf"
    TEMPLATE="mistral"
    ;;
  deepseek)
    MODEL="DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf"
    TEMPLATE="chatml"
    ;;
  qwen)
    MODEL="Qwen2.5-14B-Instruct-Q4_K_M.gguf"
    TEMPLATE="chatml"
    ;;
  *)
    echo "Uso: $0 {nous-hermes|llama3|mistral|deepseek|qwen}"
    echo ""
    echo "Modelos disponibles:"
    ls /home/quantlab/quantlab-ai-capital/models/*.gguf | xargs -I{} basename {}
    exit 1
    ;;
esac

# Actualizar .env
sed -i "s/^LLM_MODEL=.*/LLM_MODEL=${MODEL}/" .env
sed -i "s/^LLM_CHAT_TEMPLATE=.*/LLM_CHAT_TEMPLATE=${TEMPLATE}/" .env

sudo python3 -c "
import json
    c = json.load(f)
c['agents']['defaults']['model']['primary'] = 'custom-llm-8080/${MODEL}'
c['models']['providers']['custom-llm-8080']['models'][0]['id'] = '${MODEL}'
    json.dump(c, f, indent=2)
"

echo "✅ Cambiando a: $1 ($MODEL)"

# Reiniciar servicios
docker compose -f ${COMPOSE} up -d --force-recreate llm

echo "⏳ Esperando que cargue el modelo..."
sleep 8

# Prueba rápida
echo "🧪 Probando modelo..."
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"system\",\"content\":\"Eres un asistente útil. Responde en español.\"},{\"role\":\"user\",\"content\":\"Hola, responde en una sola oración.\"}],\"max_tokens\":50}" \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print('🤖 Respuesta:', r['choices'][0]['message']['content'])"
