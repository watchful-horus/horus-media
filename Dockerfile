# Stage 1: build the web UI (Vite writes to ../src/horus_media/static, i.e. /src/horus_media/static).
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: the app.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    HORUS_CONTAINER=1 \
    HORUS_HOST=0.0.0.0 \
    HORUS_PORT=8000 \
    HORUS_DB_PATH=/data/horus.db
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY --from=web /src/horus_media/static ./src/horus_media/static
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --home-dir /data horus \
    && mkdir /data && chown horus /data
USER horus
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:'+os.environ.get('HORUS_PORT','8000')+'/api/health', timeout=4)"
CMD ["python", "-m", "horus_media"]
