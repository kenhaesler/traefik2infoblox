"""Best-effort detection of the Docker host's FQDN.

Inside a container, socket.getfqdn() describes the container itself, so the
host name has to come from the Docker daemon (which runs on the host). The
daemon often only knows the short hostname; in that case we try a DNS lookup
and finally fall back to qualifying the short name with the managed zone.
"""

from __future__ import annotations

import socket
from typing import Callable, Tuple

from .labels import normalize_hostname


def resolve_host_fqdn(
    daemon_name: str,
    zone: str,
    dns_lookup: Callable[[str], str] = socket.getfqdn,
) -> Tuple[str, str]:
    """Return (fqdn, source) for the Docker host, or raise ValueError."""
    name = normalize_hostname(daemon_name)
    if not name:
        raise ValueError("the Docker daemon reported an empty hostname")

    if "." in name:
        return name, "Docker daemon hostname"

    try:
        resolved = normalize_hostname(dns_lookup(name))
    except OSError:
        resolved = ""
    # getfqdn() commonly yields localhost.localdomain when /etc/hosts lists
    # 127.0.0.1 first; that is never the name we want records to point at.
    if "." in resolved and not resolved.startswith("localhost"):
        return resolved, f"DNS lookup of daemon hostname {name!r}"

    return f"{name}.{zone}", f"daemon hostname {name!r} qualified with zone {zone!r}"
