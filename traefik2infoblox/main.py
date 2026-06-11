"""Application entry point: run the sync loop against Docker and Infoblox."""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
import time
from dataclasses import replace
from typing import Set

import docker

from .config import Config, ConfigError, default_record_comment
from .hostinfo import resolve_host_fqdn
from .infoblox import InfobloxClient, InfobloxError
from .labels import collect_desired_hostnames
from .state import State
from .sync import sync_once

logger = logging.getLogger("traefik2infoblox")

# Container lifecycle events that can change the set of active Traefik labels.
_INTERESTING_EVENTS = {"start", "stop", "die", "destroy", "update", "pause", "unpause", "rename"}

# How long to wait after a docker event before syncing, so that a stack
# starting many containers at once results in a single sync cycle.
_EVENT_SETTLE_SECONDS = 2


def watch_docker_events(client: docker.DockerClient, wake: threading.Event, stop: threading.Event) -> None:
    """Wake the sync loop whenever a container starts or stops."""
    while not stop.is_set():
        try:
            for event in client.events(decode=True, filters={"type": "container"}):
                if stop.is_set():
                    return
                action = str(event.get("Action", "")).split(":", 1)[0]
                if action in _INTERESTING_EVENTS:
                    wake.set()
        except Exception as exc:  # noqa: BLE001 - keep the watcher alive at all costs
            if stop.is_set():
                return
            logger.warning("Docker event stream error (%s); reconnecting in 5s", exc)
            stop.wait(5)


def _touch(path: str) -> None:
    try:
        with open(path, "a", encoding="utf-8"):
            os.utime(path, None)
    except OSError as exc:
        logger.debug("Could not update heartbeat file %s: %s", path, exc)


def run() -> int:
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        logging.basicConfig(level="ERROR", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logger.error("Invalid configuration: %s", exc)
        return 2

    logging.basicConfig(
        level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logger.info(
        "Starting traefik2infoblox: zone=%s view=%s interval=%ss expire=%ss dry_run=%s",
        cfg.zone,
        cfg.view,
        cfg.sync_interval,
        cfg.expire_after,
        cfg.dry_run,
    )

    try:
        docker_client = docker.from_env()
        docker_client.ping()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Cannot connect to the Docker daemon (%s). Is /var/run/docker.sock mounted "
            "and readable by this user? The container runs as a non-root user, so the "
            "socket's group must be granted (see group_add / DOCKER_GID in "
            "docker-compose.yml).",
            exc,
        )
        return 1

    if cfg.cname_target:
        target_source = "CNAME_TARGET environment variable"
    else:
        daemon_name = ""
        try:
            daemon_name = docker_client.info().get("Name") or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read the host name from the Docker daemon: %s", exc)
        try:
            target, target_source = resolve_host_fqdn(daemon_name, cfg.zone)
        except ValueError as exc:
            logger.error(
                "Could not auto-detect the host FQDN (%s); set CNAME_TARGET explicitly", exc
            )
            return 2
        cfg = replace(
            cfg,
            cname_target=target,
            record_comment=cfg.record_comment or default_record_comment(target),
        )
    logger.info("CNAME target: %s (from %s)", cfg.cname_target, target_source)
    try:
        socket.gethostbyname(cfg.cname_target)
    except OSError:
        logger.warning(
            "CNAME target %s does not currently resolve from inside the container; "
            "records will still point at it — set CNAME_TARGET explicitly if this "
            "is not the intended host FQDN",
            cfg.cname_target,
        )

    infoblox = InfobloxClient(
        wapi_url=cfg.wapi_url,
        username=cfg.username,
        password=cfg.password,
        view=cfg.view,
        ssl_verify=cfg.ssl_verify,
        timeout=cfg.timeout,
    )
    try:
        if infoblox.zone_exists(cfg.zone):
            logger.info("Confirmed authoritative zone %s in view %s", cfg.zone, cfg.view)
        else:
            logger.warning(
                "Zone %s not visible via WAPI (missing zone or missing zone_auth "
                "read permission); continuing anyway",
                cfg.zone,
            )
    except InfobloxError as exc:
        if exc.status_code == 401:
            logger.error("Infoblox rejected the credentials: %s", exc)
            return 1
        logger.warning("Could not verify zone %s (%s); continuing anyway", cfg.zone, exc)

    state = State.load(cfg.state_file)

    stop = threading.Event()
    wake = threading.Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("Received signal %d; shutting down", signum)
        stop.set()
        wake.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    threading.Thread(
        target=watch_docker_events, args=(docker_client, wake, stop), daemon=True
    ).start()

    warned_foreign: Set[str] = set()
    warned_out_of_zone: Set[str] = set()

    while not stop.is_set():
        try:
            containers = docker_client.containers.list()
            desired, out_of_zone = collect_desired_hostnames(
                containers, cfg.zone, cfg.require_traefik_enable
            )
            for hostname in sorted(out_of_zone - warned_out_of_zone):
                logger.warning(
                    "Ignoring %s: outside managed zone %s", hostname, cfg.zone
                )
                warned_out_of_zone.add(hostname)

            result = sync_once(cfg, infoblox, state, desired, time.time(), warned=warned_foreign)
            message = (
                "Sync done: %d hostnames in labels, %d created, %d updated, "
                "%d deleted, %d adopted, %d foreign, %d errors, %d tracked"
            )
            args = (
                len(desired),
                result.created,
                result.updated,
                result.deleted,
                result.adopted,
                result.foreign,
                result.errors,
                result.tracked,
            )
            if result.changed:
                logger.info(message, *args)
            else:
                logger.debug(message, *args)
        except Exception:  # noqa: BLE001 - a failed cycle must not kill the service
            logger.exception("Sync cycle failed; retrying in %ss", cfg.sync_interval)

        _touch(cfg.heartbeat_file)

        if wake.wait(timeout=cfg.sync_interval):
            stop.wait(_EVENT_SETTLE_SECONDS)
            wake.clear()

    logger.info("Stopped")
    return 0


def run_cli() -> None:
    sys.exit(run())
