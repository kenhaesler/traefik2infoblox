"""Docker HEALTHCHECK helper: verify that the sync loop is still alive.

The main loop touches the heartbeat file after every cycle; this script fails
when the file is missing or older than three sync intervals.
"""

from __future__ import annotations

import os
import sys
import time

from .config import DEFAULT_HEARTBEAT_FILE, ConfigError, parse_duration

_MINIMUM_GRACE_SECONDS = 180


def main() -> int:
    path = os.environ.get("HEARTBEAT_FILE", DEFAULT_HEARTBEAT_FILE)
    try:
        interval = parse_duration(os.environ.get("SYNC_INTERVAL", "60"), "SYNC_INTERVAL")
    except ConfigError:
        interval = 60
    try:
        age = time.time() - os.stat(path).st_mtime
    except OSError:
        return 1
    return 0 if age < max(interval * 3, _MINIMUM_GRACE_SECONDS) else 1


if __name__ == "__main__":
    sys.exit(main())
