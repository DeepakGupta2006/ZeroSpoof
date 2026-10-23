FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY zerospoof ./zerospoof
COPY models ./models
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
# One worker per container: a WebSocket session is sticky to the process that owns it.
# Scale out with more containers behind the gateway, not with --workers.
CMD ["uvicorn", "zerospoof.main:app", "--host", "0.0.0.0", "--port", "8000"]
