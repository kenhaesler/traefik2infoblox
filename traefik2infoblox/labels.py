"""Extract hostnames from Traefik router rules in container labels."""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Set, Tuple

# Router rule labels for HTTP and TCP routers, e.g.
#   traefik.http.routers.myapp.rule = Host(`app.example.com`)
#   traefik.tcp.routers.mytcp.rule  = HostSNI(`db.example.com`)
_RULE_LABEL_RE = re.compile(r"^traefik\.(?:http|tcp)\.routers\.[^.]+\.rule$")

# Host()/HostSNI() matchers inside a rule. HostRegexp() is intentionally not
# matched: its arguments are patterns, not concrete hostnames.
_HOST_MATCHER_RE = re.compile(r"\b(?:Host|HostSNI)\(([^)]*)\)")

# Traefik accepts backticks or quotes around matcher values.
_QUOTED_VALUE_RE = re.compile(r"[`'\"]([^`'\"]+)[`'\"]")

_FALSE_VALUES = {"false", "0", "no", "off"}
_TRUE_VALUES = {"true", "1", "yes", "on"}


def normalize_hostname(value: str) -> str:
    return value.strip().rstrip(".").lower()


def hostnames_from_rule(rule: str) -> Set[str]:
    """Return all concrete hostnames referenced by Host()/HostSNI() matchers."""
    hostnames: Set[str] = set()
    for arguments in _HOST_MATCHER_RE.findall(rule):
        for raw in _QUOTED_VALUE_RE.findall(arguments):
            hostname = normalize_hostname(raw)
            # Skip wildcards (HostSNI(`*`)) and regex-style placeholders.
            if not hostname or "*" in hostname or "{" in hostname:
                continue
            hostnames.add(hostname)
    return hostnames


def hostnames_from_labels(
    labels: Mapping[str, str], require_traefik_enable: bool = False
) -> Set[str]:
    """Return all hostnames declared by a single container's Traefik labels."""
    enable = labels.get("traefik.enable")
    if enable is not None and enable.strip().lower() in _FALSE_VALUES:
        return set()
    if require_traefik_enable and (enable is None or enable.strip().lower() not in _TRUE_VALUES):
        return set()

    hostnames: Set[str] = set()
    for key, value in labels.items():
        if _RULE_LABEL_RE.match(key):
            hostnames |= hostnames_from_rule(value)
    return hostnames


def in_zone(hostname: str, zone: str) -> bool:
    return hostname == zone or hostname.endswith(f".{zone}")


def collect_desired_hostnames(
    containers: Iterable[object], zone: str, require_traefik_enable: bool = False
) -> Tuple[Set[str], Set[str]]:
    """Gather hostnames from all containers, split into in-zone and out-of-zone."""
    desired: Set[str] = set()
    out_of_zone: Set[str] = set()
    for container in containers:
        labels = getattr(container, "labels", None) or {}
        for hostname in hostnames_from_labels(labels, require_traefik_enable):
            if in_zone(hostname, zone):
                desired.add(hostname)
            else:
                out_of_zone.add(hostname)
    return desired, out_of_zone
