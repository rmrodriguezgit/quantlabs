FROM nvidia/cuda:12.6.3-runtime-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
RUN apt-get update && apt-get install -y python3 python3-pip curl git tesseract-ocr && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --break-system-packages -r requirements.txt
COPY . .
CMD ["gunicorn","-k","gevent","-w","2","-b","0.0.0.0:5000","api.app:app"]
