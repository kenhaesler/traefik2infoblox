# Docker Hardened Images (https://docs.docker.com/dhi/) require authentication:
#   docker login dhi.io
# If your organization mirrors DHI into its own namespace, override the bases:
#   docker build --build-arg BUILD_IMAGE=docker.io/<org>/dhi-python:3.13-dev \
#                --build-arg RUNTIME_IMAGE=docker.io/<org>/dhi-python:3.13 .
ARG BUILD_IMAGE=dhi.io/python:3.13-dev
ARG RUNTIME_IMAGE=dhi.io/python:3.13

FROM ${BUILD_IMAGE} AS build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN python -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY traefik2infoblox ./traefik2infoblox

# The runtime stage has no shell, so prepare the state directory here with
# the ownership the nonroot runtime user (uid 65532) needs. Named volumes
# inherit this ownership on first use.
RUN mkdir -p /data && chown 65532:65532 /data

FROM ${RUNTIME_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/venv/bin:${PATH}"

WORKDIR /app

COPY --from=build --chown=65532:65532 /app/venv /app/venv
COPY --from=build --chown=65532:65532 /app/traefik2infoblox ./traefik2infoblox
COPY --from=build --chown=65532:65532 /data /data

# DHI runtime images already run as nonroot (uid 65532); keeping it explicit
# means the image stays non-root even when RUNTIME_IMAGE is overridden.
USER 65532:65532

VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
    CMD ["python", "-m", "traefik2infoblox.healthcheck"]

ENTRYPOINT ["python", "-m", "traefik2infoblox"]
