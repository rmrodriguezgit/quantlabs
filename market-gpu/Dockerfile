FROM quantlab-ai-capital-jupyter:py311-cuda12
WORKDIR /app
COPY app.py /app/app.py
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
