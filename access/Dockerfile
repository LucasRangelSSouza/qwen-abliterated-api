# Estagio 1: constroi o painel React (access/panel). Sem package-lock.json cai para npm install.
FROM node:22-alpine AS panel
WORKDIR /panel
COPY panel/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY panel/ ./
RUN npm run build

# Estagio 2: API Python + painel pronto.
FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 10001 --no-create-home app \
 && mkdir /data && chown 10001:10001 /data
COPY app ./app
COPY --from=panel /panel/dist ./panel/dist
ARG GIT_COMMIT=dev
ENV GIT_COMMIT=${GIT_COMMIT} PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000 ACCESS_DB_PATH=/data/access.db
USER 10001
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=8s --start-period=20s --retries=5 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=5).status == 200 else 1)"
# 1 processo: o limitador de login vive em memoria e o SQLite tem um unico escritor
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 75"]
