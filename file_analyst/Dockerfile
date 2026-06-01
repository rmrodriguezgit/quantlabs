FROM python:3.12-slim

LABEL maintainer="quantlabs"
LABEL description="file_analyst — private document analysis service"

WORKDIR /service

# Dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código del microservicio
COPY app/ ./app/

# Variables de entorno por defecto (override con docker run -e o .env)
ENV ANTHROPIC_API_KEY=""
ENV LLM_API_URL="http://llm:8080/v1/chat/completions"
ENV HOST="0.0.0.0"
ENV PORT=8010
ENV PYTHONUNBUFFERED=1

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=5)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
