# Both base images are pinned by digest, so a rebuild of the same commit gets the same base.
# Dependabot proposes the newer digests (see .github/dependabot.yml).

# Stage 1: build the web UI (Vite writes to ../src/horus_media/static, i.e. /src/horus_media/static).
FROM node:24-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: the app.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONUNBUFFERED=1 \
    HORUS_CONTAINER=1 \
    HORUS_HOST=0.0.0.0 \
    HORUS_PORT=8000 \
    HORUS_DB_PATH=/data/horus.db
WORKDIR /app
# The dependencies first, at the exact versions and file hashes in requirements.txt: pip refuses
# anything that does not match. Horus itself is then installed without resolving anything.
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY --from=web /src/horus_media/static ./src/horus_media/static
RUN pip install --no-cache-dir --no-deps . \
    && pip check \
    && useradd --system --uid 10001 --home-dir /data horus \
    && mkdir /data && chown horus /data
USER horus
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:'+os.environ.get('HORUS_PORT','8000')+'/api/health', timeout=4)"
CMD ["python", "-m", "horus_media"]
