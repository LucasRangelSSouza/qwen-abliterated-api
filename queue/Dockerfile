FROM python:3.12-slim

# openssh-client: tunel para a GPU; curl: healthcheck e diagnostico. Roda como root dentro do contêiner (segredos montados por
# env_file; cap_drop ALL + read_only no compose), igual aos outros servicos da plataforma.
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY remote ./remote
ARG GIT_COMMIT=dev
ENV GIT_COMMIT=${GIT_COMMIT} PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=8s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status == 200 else 1)"
# 1 processo: a fila e as conexoes dos clientes vivem em memoria (Spec 13 §3.1); nao usar varios workers
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "75"]
