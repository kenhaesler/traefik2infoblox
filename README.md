# traefik2infoblox

A small Docker container that watches **all Traefik labels on a Docker host** (across every
Compose stack) and keeps **Infoblox IPAM/DNS** in sync:

- 🔍 Discovers every hostname declared in `Host()` / `HostSNI()` rules of Traefik router labels
- ➕ **Automatically registers** a CNAME in Infoblox for each hostname, pointing at the FQDN of
  the Docker host (**auto-detected** via the Docker daemon, or set explicitly), as soon as a
  container with a new label starts
- 🧹 **Automatically deletes** the CNAME once the label has not been seen for **7 days**
  (configurable)
- 🔒 Only ever touches records it created itself (identified by an ownership comment on the
  record) — manually created DNS entries are never modified or deleted

## How it works

```
┌────────────────────────┐     labels      ┌──────────────────┐    WAPI     ┌──────────┐
│ Docker host            │ ──────────────▶ │ traefik2infoblox │ ──────────▶ │ Infoblox │
│  ├─ stack A (compose)  │  docker events  │  - parse Host()  │   CNAMEs    │   IPAM   │
│  ├─ stack B (compose)  │                 │  - track seen    │             └──────────┘
│  └─ traefik            │                 │  - expire >7d    │
└────────────────────────┘                 └──────────────────┘
```

1. Every sync cycle (default: 60 s, plus instantly on container start/stop events) the
   container lists all **running** containers via the Docker socket and extracts hostnames from
   labels such as:

   ```yaml
   labels:
     - traefik.http.routers.myapp.rule=Host(`myapp.example.com`)
     - traefik.tcp.routers.mydb.rule=HostSNI(`db.example.com`)
   ```

2. For every hostname inside the configured zone, a CNAME record
   `myapp.example.com → <CNAME_TARGET>` is created in Infoblox (if it does not exist yet).
   Records get a comment like `Managed by traefik2infoblox for dockerhost01.example.com` that
   marks them as owned by this instance.

3. The timestamp of the last time each hostname was seen is persisted in a state file
   (`/data/state.json`). When a hostname has been absent for longer than `EXPIRE_AFTER`
   (default 7 days), its CNAME is deleted — but only if the record still carries this
   instance's ownership comment.

4. If the state file is ever lost, managed records found in Infoblox are *adopted* and get a
   fresh 7-day countdown, so nothing is deleted prematurely and orphans are still cleaned up.

## Quick start

```yaml
services:
  traefik2infoblox:
    image: ghcr.io/kenhaesler/traefik2infoblox:latest
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - traefik2infoblox-data:/data
    environment:
      INFOBLOX_URL: https://infoblox.example.com   # Grid Manager / WAPI endpoint
      INFOBLOX_USERNAME: api-user                  # API user
      INFOBLOX_PASSWORD: ${INFOBLOX_PASSWORD}
      INFOBLOX_ZONE: example.com                   # zone to manage
      # CNAME_TARGET: dockerhost01.example.com     # optional — auto-detected by default

volumes:
  traefik2infoblox-data:
```

Start it with `docker compose up -d`. Try it risk-free first with `DRY_RUN: "true"` — every
create/update/delete is then only logged.

## Configuration

All configuration is done through environment variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `INFOBLOX_URL` | ✅ | – | Infoblox Grid Manager base URL (e.g. `https://gm.example.com`) or a full WAPI URL (e.g. `https://gm.example.com/wapi/v2.10`) |
| `INFOBLOX_USERNAME` | ✅ | – | WAPI API user (or use `INFOBLOX_USERNAME_FILE` for Docker secrets) |
| `INFOBLOX_PASSWORD` | ✅ | – | WAPI password (or use `INFOBLOX_PASSWORD_FILE`) |
| `INFOBLOX_ZONE` | ✅ | – | DNS zone to manage (e.g. `example.com`). Only hostnames inside this zone are synced; others are logged and ignored |
| `CNAME_TARGET` | | auto-detected | FQDN of the Docker host. Every discovered hostname becomes a CNAME pointing here. When unset, it is detected automatically (see below) |
| `INFOBLOX_WAPI_VERSION` | | `v2.10` | WAPI version appended when `INFOBLOX_URL` contains no `/wapi/` path |
| `INFOBLOX_VIEW` | | `default` | Infoblox DNS view |
| `INFOBLOX_SSL_VERIFY` | | `true` | `true`, `false`, or a path to a CA bundle inside the container |
| `INFOBLOX_TIMEOUT` | | `30` | HTTP timeout for WAPI requests |
| `SYNC_INTERVAL` | | `60s` | Full reconcile interval. Accepts `30`, `90s`, `5m`, `1h`, … (container start/stop events trigger an immediate sync regardless) |
| `EXPIRE_AFTER` | | `7d` | Delete a record once its label has been unseen for this long |
| `RECORD_TTL` | | zone default | TTL (seconds) for created CNAME records |
| `RECORD_COMMENT` | | `Managed by traefik2infoblox for <CNAME_TARGET>` | Ownership marker written into each record's comment field |
| `STATE_FILE` | | `/data/state.json` | Where last-seen timestamps are persisted — mount a volume here |
| `REQUIRE_TRAEFIK_ENABLE` | | `false` | Set to `true` if Traefik runs with `exposedByDefault: false`, so only containers with `traefik.enable=true` are synced |
| `DRY_RUN` | | `false` | Log all create/update/delete actions without performing them |
| `LOG_LEVEL` | | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `DOCKER_HOST` | | local socket | Standard Docker SDK variable, if the socket is not at `/var/run/docker.sock` |

### Host FQDN auto-detection

When `CNAME_TARGET` is not set, the container determines the Docker host's FQDN on its own
(a container cannot use its *own* hostname — that would name the container, not the host):

1. It asks the **Docker daemon** for the host's hostname (`docker info`). If that is already
   fully qualified (contains a dot), it is used directly.
2. Otherwise the short hostname is **resolved via DNS** from inside the container.
3. If that fails too, the short hostname is **qualified with the managed zone**:
   `<hostname>.<INFOBLOX_ZONE>`.

The chosen target and its source are logged at startup, and a warning is logged if the result
does not resolve in DNS. Set `CNAME_TARGET` explicitly when the detection would be ambiguous
(e.g. the host's FQDN lives in a different domain than the zone and is not resolvable, or you
use a Docker socket proxy that blocks the `info` endpoint).

### What gets picked up

- `traefik.http.routers.<name>.rule` labels with `Host(...)` matchers (backticks or quotes,
  multiple hostnames, combined with `&&`/`||` — all hostnames are extracted)
- `traefik.tcp.routers.<name>.rule` labels with `HostSNI(...)` matchers
- Containers with `traefik.enable=false` are skipped
- Wildcards (`HostSNI(\`*\`)`, `*.example.com`) and `HostRegexp(...)` patterns are skipped —
  no concrete hostname can be derived from them
- Only **running** containers count as "seen"

### Safety model

Every record this tool creates carries an ownership comment (`RECORD_COMMENT`). Records whose
comment does not match are **never updated and never deleted** — an existing manually created
`myapp.example.com` record is left untouched and a warning is logged instead.

Running **multiple Docker hosts against the same zone** is safe out of the box: the default
comment includes each host's CNAME target (explicit or auto-detected), so each instance only
manages its own records. If you override `RECORD_COMMENT`, give each host a distinct value.

> **Note:** a CNAME cannot be created at the zone apex (`example.com` itself) — that is a DNS
> limitation and Infoblox will reject it; the error is logged and the rest of the sync
> continues. Swarm service labels are not inspected (plain Docker / Compose only).

### Hardened image

The container is built on [Docker Hardened Images](https://docs.docker.com/dhi/)
(`dhi.io/python`), using the official DHI multi-stage pattern: dependencies are installed in
the `-dev` build stage and only the virtual environment is copied into the minimal runtime
image. That means:

- It runs as the **non-root** user (uid `65532`) — hence the `group_add`/`DOCKER_GID` entry in
  the compose file to grant access to the Docker socket
  (`export DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)`).
- The runtime image contains **no shell and no package manager**; the healthcheck and
  entrypoint use exec form and need neither.
- The compose file additionally enables `read_only` root filesystem (with a `tmpfs` for the
  heartbeat file), `cap_drop: ALL` and `no-new-privileges`.
- If you bind-mount a host directory to `/data` instead of using the named volume, `chown`
  it to uid `65532` first. Named volumes inherit the correct ownership automatically.

Pulling DHI base images requires authentication (`docker login dhi.io` with Docker Hub
credentials that have DHI access). For the GitHub Actions image publish, set the
`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` repository secrets. If your organization mirrors
DHI into its own namespace, override the bases at build time:

```bash
docker build \
  --build-arg BUILD_IMAGE=docker.io/<org>/dhi-python:3.13-dev \
  --build-arg RUNTIME_IMAGE=docker.io/<org>/dhi-python:3.13 .
```

### Required Infoblox permissions

The API user needs read/write permission for **CNAME records** in the managed zone (and
ideally read access to `zone_auth` so the startup zone check can succeed — it only logs a
warning otherwise).

## Operations

- **State**: persist `/data` in a volume. Without it the expiry countdown restarts after every
  container restart (records are then adopted again and live up to 7 extra days — nothing is
  deleted early).
- **Healthcheck**: the image ships a Docker `HEALTHCHECK` that fails when the sync loop has
  stopped making progress.
- **Logs**: every registration, update, deletion and adoption is logged at `INFO`. A regular
  no-change cycle only logs at `DEBUG`.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest                      # run the test suite

docker login dhi.io         # required: hardened base images need authentication
docker build -t traefik2infoblox .
```

Pushes to `main` and version tags (`v*.*.*`) build and publish the image to
`ghcr.io/kenhaesler/traefik2infoblox` via GitHub Actions.
