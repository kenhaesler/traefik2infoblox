FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY traefik2infoblox ./traefik2infoblox

VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
    CMD ["python", "-m", "traefik2infoblox.healthcheck"]

ENTRYPOINT ["python", "-m", "traefik2infoblox"]
