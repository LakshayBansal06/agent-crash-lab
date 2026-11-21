FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CRASHLAB_DB=/data/crashlab.db CRASHLAB_AUTH=required
COPY pyproject.toml README.md requirements.lock ./
COPY crashlab ./crashlab
COPY server ./server
COPY web ./web
COPY infrastructure/entrypoint.sh /entrypoint.sh
RUN pip install --no-cache-dir -r requirements.lock ".[server]" && useradd -m crashlab && mkdir /data && chown crashlab /data \
    && chmod 755 /entrypoint.sh
# Starts as root only long enough to give a host-mounted disk (Render) to the app user, then drops to it.
# docker-compose runs it as `crashlab` from the start, with every capability dropped.
EXPOSE 8000
HEALTHCHECK CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/ready')"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000} --no-server-header"]
