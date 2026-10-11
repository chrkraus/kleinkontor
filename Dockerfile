# Buchhaltung als Container – Daten liegen im Volume /data.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BUCHHALTUNG_DATA=/data \
    BUCHHALTUNG_SCHEDULER=1 \
    TZ=Europe/Berlin

# Zeitzonendaten: ohne sie ignoriert Python TZ=Europe/Berlin und rechnet in UTC
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home --uid 1000 buchhaltung \
 && mkdir -p /data && chown buchhaltung:buchhaltung /data
USER buchhaltung

VOLUME ["/data"]
EXPOSE 5000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/anmelden', timeout=3)"
CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "--no-clear-untrusted-proxy-headers", "app:app"]
