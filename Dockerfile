# Slim runtime: no browser inside — images are fetched over httpx, and the
# cdp cookie source talks to an external Chrome over a plain websocket.

FROM python:3.12-slim

# Only used while building (pip / apt behind a proxy); not baked into the image.
ARG GOP_BUILD_HTTP_PROXY=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=4982 \
    GOP_COOKIE_SOURCE=env \
    GOP_PROBE_ON_START=false \
    GOP_LOG_FORMAT=json \
    GOP_LOG_LEVEL=INFO \
    GOP_DATA_DIR=/app/data

RUN export http_proxy="$GOP_BUILD_HTTP_PROXY" https_proxy="$GOP_BUILD_HTTP_PROXY" && \
    apt-get update && apt-get install -y --no-install-recommends tini && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Dependencies first (read from pyproject.toml) so a code change keeps this layer cached.
# git is only for the gemini-webapi git pin (see pyproject.toml) and leaves in the same layer.
COPY pyproject.toml ./
RUN export http_proxy="$GOP_BUILD_HTTP_PROXY" https_proxy="$GOP_BUILD_HTTP_PROXY" && \
    apt-get update && apt-get install -y --no-install-recommends git && \
    python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
        > requirements.txt && \
    pip install -r requirements.txt && \
    apt-get purge -y --auto-remove git && rm -rf /var/lib/apt/lists/*

COPY README.md LICENSE NOTICE ./
COPY src ./src
RUN export http_proxy="$GOP_BUILD_HTTP_PROXY" https_proxy="$GOP_BUILD_HTTP_PROXY" && \
    pip install --no-deps . && \
    rm -rf /build

RUN mkdir -p /app/data
WORKDIR /app
VOLUME ["/app/data"]

EXPOSE 4982

HEALTHCHECK --interval=30s --timeout=8s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4982/health', timeout=5)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "gemini_webapi_proxy", "--host", "0.0.0.0", "--port", "4982"]
