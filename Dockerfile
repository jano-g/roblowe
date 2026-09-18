FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    TZ=Europe/Bratislava

RUN groupadd -r roblowe && useradd -r -g roblowe -d /srv -s /usr/sbin/nologin roblowe \
 && mkdir -p /data && chown roblowe:roblowe /data

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

USER roblowe
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

# Jeden proces zámerne: SQLite + scheduler thread. nginx na hostiteľovi robí TLS.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
