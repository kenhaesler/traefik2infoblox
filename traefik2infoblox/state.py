"""Persistent last-seen tracking for hostnames, stored as JSON."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Dict

logger = logging.getLogger(__name__)

STATE_VERSION = 1


class State:
    """Maps hostname -> unix timestamp of the last time its label was seen."""

    def __init__(self, path: str):
        self.path = path
        self.last_seen: Dict[str, float] = {}

    @classmethod
    def load(cls, path: str) -> "State":
        state = cls(path)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            raw = data.get("last_seen", {})
            state.last_seen = {
                str(name): float(timestamp)
                for name, timestamp in raw.items()
                if isinstance(timestamp, (int, float))
            }
            logger.info("Loaded state from %s (%d hostnames tracked)", path, len(state.last_seen))
        except FileNotFoundError:
            logger.info("No state file at %s yet; starting fresh", path)
        except (OSError, ValueError) as exc:
            logger.warning("Could not read state file %s (%s); starting fresh", path, exc)
        return state

    def save(self) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        payload = {"version": STATE_VERSION, "last_seen": self.last_seen}
        # Write atomically so a crash mid-write never corrupts the state.
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def mark_seen(self, hostname: str, timestamp: float) -> None:
        self.last_seen[hostname] = timestamp

    def remove(self, hostname: str) -> None:
        self.last_seen.pop(hostname, None)
