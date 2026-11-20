FROM python:3.12-slim-bookworm

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=3000 \
    HOME=/home/docuflow \
    HF_HOME=/app/cache/huggingface \
    XDG_CACHE_HOME=/app/cache \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg libass9 fonts-noto-core libgomp1 gosu \
    && rm -rf /var/lib/apt/lists/* \
    && ffmpeg -hide_banner -filters 2>/dev/null | grep -q ' subtitles '

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY public ./public
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN groupadd --gid "${APP_GID}" docuflow \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
       --home-dir /home/docuflow --create-home --shell /usr/sbin/nologin docuflow \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/data/outputs /app/data/work /app/cache/huggingface \
    && chown -R "${APP_UID}:${APP_GID}" /app /home/docuflow

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/health', timeout=3)"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-3000} --workers 1"]
