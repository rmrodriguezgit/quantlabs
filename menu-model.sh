#!/bin/bash

# =========================================================
# QUANTLAB AI — MODEL MENU
# =========================================================

PROJECT_DIR="/home/quantlab/quantlab-ai-capital"
MODELS_DIR="${QUANTLAB_RUNTIME_DIR:-/home/quantlab/quantlab-runtime}/models"

CURRENT_LINK="${MODELS_DIR}/current-model.gguf"

# =========================================================
# MODELOS
# =========================================================

declare -A MODELS

MODELS[qwen]="Qwen2.5-14B-Instruct-Q4_K_M.gguf"
MODELS[qwen-coder]="Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"
MODELS[phi4]="phi-4-Q4_K_M.gguf"
MODELS[nous-hermes]="Nous-Hermes-2-Mistral-7B-DPO.Q4_K_M.gguf"

# =========================================================
# HELP
# =========================================================

show_help() {

echo ""
echo "==============================================="
echo "🧠 QUANTLAB AI — MODEL MENU"
echo "==============================================="
echo ""
echo "Uso:"
echo ""
echo "./model-menu.sh qwen"
echo "./model-menu.sh qwen-coder"
echo "./model-menu.sh phi4"
echo "./model-menu.sh nous-hermes"
echo ""
echo "./model-menu.sh status"
echo "./model-menu.sh logs"
echo "./model-menu.sh test"
echo "./model-menu.sh menu"
echo "./model-menu.sh --help"
echo ""
echo "==============================================="
echo "MODELOS"
echo "==============================================="
echo ""
echo "qwen         → Agentes + Finanzas"
echo "qwen-coder   → Código + automatización"
echo "phi4         → Razonamiento general"
echo "nous-hermes  → Más estable"
echo ""
}

# =========================================================
# STATUS
# =========================================================

show_status() {

echo ""
echo "==============================================="
echo "📌 MODELO ACTUAL"
echo "==============================================="
echo ""

ls -lh $CURRENT_LINK

echo ""

docker ps | grep quantlab

echo ""
}

# =========================================================
# LOGS
# =========================================================

show_logs() {

docker logs -f quantlab_llm

}

# =========================================================
# TEST
# =========================================================

test_model() {

echo ""
echo "🧪 Probando modelo..."
echo ""

RESPONSE=$(curl -s http://localhost:8080/v1/chat/completions \
-H "Content-Type: application/json" \
-d '{
  "messages": [
    {
      "role":"user",
      "content":"Di hola desde QuantLab"
    }
  ],
  "max_tokens":30
}')

echo "$RESPONSE" | python3 -c '
import json,sys

try:
    r=json.load(sys.stdin)

    if "choices" in r:
        print("🤖 RESPUESTA:")
        print(r["choices"][0]["message"]["content"])

    elif "error" in r:
        print("❌ ERROR:")
        print(r["error"])

    else:
        print("⚠️ RESPUESTA DESCONOCIDA:")
        print(r)

except Exception as e:
    print("❌ JSON inválido")
    print(e)
'

echo ""

}

# =========================================================
# SWITCH
# =========================================================

switch_model() {

KEY=$1

MODEL=${MODELS[$KEY]}

if [ -z "$MODEL" ]; then
    echo "❌ Modelo inválido"
    exit 1
fi

echo ""
echo "==============================================="
echo "🧠 CAMBIANDO MODELO → $KEY"
echo "==============================================="
echo ""

if [ ! -f "${MODELS_DIR}/${MODEL}" ]; then
    echo "❌ Archivo no encontrado:"
    echo "${MODELS_DIR}/${MODEL}"
    exit 1
fi

rm -f $CURRENT_LINK

ln -s "${MODELS_DIR}/${MODEL}" $CURRENT_LINK

echo "✅ Link actualizado"
echo ""

docker compose restart llm

echo ""
echo "⏳ Esperando carga del modelo..."
sleep 15

test_model

}

# =========================================================
# MENU INTERACTIVO
# =========================================================

interactive_menu() {

clear

echo ""
echo "==============================================="
echo "🧠 QUANTLAB MODEL SELECTOR"
echo "==============================================="
echo ""
echo "1) QWEN"
echo "2) QWEN CODER 14B"
echo "3) PHI-4 14B"
echo "4) NOUS-HERMES"
echo "5) STATUS"
echo "6) TEST"
echo "7) LOGS"
echo "0) SALIR"
echo ""

read -p "Selecciona opción: " OPTION

case $OPTION in

1)
    switch_model qwen
    ;;

2)
    switch_model qwen-coder
    ;;

3)
    switch_model phi4
    ;;

4)
    switch_model nous-hermes
    ;;

5)
    show_status
    ;;

6)
    test_model
    ;;

7)
    show_logs
    ;;

0)
    exit 0
    ;;

*)
    echo "❌ Opción inválida"
    ;;

esac

}

# =========================================================
# MAIN
# =========================================================

case "$1" in

qwen)
    switch_model qwen
    ;;

qwen-coder)
    switch_model qwen-coder
    ;;

phi4)
    switch_model phi4
    ;;

nous-hermes)
    switch_model nous-hermes
    ;;

status)
    show_status
    ;;

logs)
    show_logs
    ;;

test)
    test_model
    ;;

menu)
    interactive_menu
    ;;

--help)
    show_help
    ;;

*)
    show_help
    ;;

esac
