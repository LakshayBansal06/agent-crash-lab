FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CRASHLAB_DB=/data/crashlab.db CRASHLAB_AUTH=required
COPY pyproject.toml README.md requirements.lock ./
COPY crashlab ./crashlab
COPY server ./server
COPY web ./web
RUN pip install --no-cache-dir -r requirements.lock ".[server]" && useradd -m crashlab && mkdir /data && chown crashlab /data
USER crashlab
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/ready')"
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
