#!/bin/bash
# =============================================================
# QuantLab AI Capital — Switch Model
# Cambia el modelo LLM activo sin tocar docker-compose.yml
# =============================================================

MODELS_DIR="/home/quantlab/quantlab-ai-capital/models"
ENV_FILE="/home/quantlab/quantlab-ai-capital/.env"
COMPOSE_FILE="/home/quantlab/quantlab-ai-capital/docker-compose.yml"
SYMLINK="$MODELS_DIR/current-model.gguf"

# Colores
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     QuantLab — Selector de Modelo LLM   ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# Leer modelo actual desde .env
CURRENT=$(grep "^LLM_MODEL=" "$ENV_FILE" | cut -d'=' -f2)
echo -e "  Modelo activo: ${GREEN}$CURRENT${NC}"
echo ""

# Listar modelos disponibles
echo -e "${YELLOW}Modelos disponibles:${NC}"
echo ""
MODELS=()
i=1
for f in "$MODELS_DIR"/*.gguf; do
    NAME=$(basename "$f")
    # Saltar el symlink
    if [ -L "$f" ]; then
        continue
    fi
    if [ "$NAME" = "$CURRENT" ]; then
        echo -e "  ${GREEN}[$i] $NAME  ← activo${NC}"
    else
        echo -e "  [$i] $NAME"
    fi
    MODELS+=("$NAME")
    ((i++))
done

echo ""
echo -e "  [0] Cancelar"
echo ""
read -rp "  Selecciona número de modelo: " CHOICE

# Validar
if [ "$CHOICE" = "0" ]; then
    echo -e "\n  ${YELLOW}Cancelado.${NC}\n"
    exit 0
fi

if ! [[ "$CHOICE" =~ ^[0-9]+$ ]] || [ "$CHOICE" -gt "${#MODELS[@]}" ]; then
    echo -e "\n  ${RED}Opción inválida.${NC}\n"
    exit 1
fi

SELECTED="${MODELS[$((CHOICE-1))]}"

if [ "$SELECTED" = "$CURRENT" ]; then
    echo -e "\n  ${YELLOW}Ya está activo: $SELECTED${NC}\n"
    exit 0
fi

# Elegir parámetros según modelo
echo ""
echo -e "  Configurando parámetros para ${CYAN}$SELECTED${NC}..."

case "$SELECTED" in
    *Qwen2.5-14B*)
        TEMPLATE="chatml"
        CTX=8192
        GPU_LAYERS=40
        THREADS=6
        ;;
    *DeepSeek*)
        TEMPLATE="chatml"
        CTX=8192
        GPU_LAYERS=32
        THREADS=6
        ;;
    *Mistral-Nemo*)
        TEMPLATE="mistral"
        CTX=16384
        GPU_LAYERS=35
        THREADS=6
        ;;
    *Nous-Hermes*|*Mistral*)
        TEMPLATE="chatml"
        CTX=8192
        GPU_LAYERS=32
        THREADS=6
        ;;
    *)
        TEMPLATE="chatml"
        CTX=8192
        GPU_LAYERS=32
        THREADS=6
        ;;
esac

# Actualizar .env
sed -i "s/^LLM_MODEL=.*/LLM_MODEL=$SELECTED/" "$ENV_FILE"
sed -i "s/^LLM_CHAT_TEMPLATE=.*/LLM_CHAT_TEMPLATE=$TEMPLATE/" "$ENV_FILE"
sed -i "s/^LLM_CTX_SIZE=.*/LLM_CTX_SIZE=$CTX/" "$ENV_FILE"
sed -i "s/^LLM_GPU_LAYERS=.*/LLM_GPU_LAYERS=$GPU_LAYERS/" "$ENV_FILE"
sed -i "s/^LLM_THREADS=.*/LLM_THREADS=$THREADS/" "$ENV_FILE"

echo -e "  ${GREEN}✔ .env actualizado${NC}"

# Actualizar symlink
ln -sf "$MODELS_DIR/$SELECTED" "$SYMLINK"
echo -e "  ${GREEN}✔ Symlink actualizado → $SELECTED${NC}"

# Reiniciar contenedor LLM
echo ""
echo -e "  Reiniciando ${CYAN}quantlab_llm${NC}..."
docker compose -f "$COMPOSE_FILE" up -d --no-deps --force-recreate llm

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Modelo cambiado con éxito        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Modelo : ${CYAN}$SELECTED${NC}"
echo -e "  Template: $TEMPLATE"
echo -e "  Contexto: $CTX tokens"
echo -e "  GPU layers: $GPU_LAYERS"
echo ""
echo -e "  Verifica con: ${YELLOW}docker logs quantlab_llm --follow${NC}"
echo ""
