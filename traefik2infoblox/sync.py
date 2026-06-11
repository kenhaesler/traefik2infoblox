"""Reconcile desired hostnames from Docker labels with Infoblox CNAME records."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

from .config import Config
from .infoblox import InfobloxClient, InfobloxError
from .labels import in_zone, normalize_hostname
from .state import State

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    adopted: int = 0
    foreign: int = 0
    errors: int = 0
    tracked: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.deleted or self.adopted or self.errors)


def ensure_record(
    cfg: Config,
    infoblox: InfobloxClient,
    name: str,
    cached_record: Optional[Dict[str, Any]] = None,
    warned: Optional[Set[str]] = None,
) -> str:
    """Make sure a CNAME for `name` exists and points at the configured target."""
    record = cached_record or infoblox.get_cname(name)

    if record is None:
        if cfg.dry_run:
            logger.info("[dry-run] Would create CNAME %s -> %s", name, cfg.cname_target)
            return "created"
        infoblox.create_cname(name, cfg.cname_target, cfg.record_comment, cfg.record_ttl)
        logger.info("Registered CNAME %s -> %s", name, cfg.cname_target)
        return "created"

    if (record.get("comment") or "") != cfg.record_comment:
        if warned is None or name not in warned:
            logger.warning(
                "CNAME %s already exists but is not managed by this instance "
                "(comment=%r); leaving it untouched",
                name,
                record.get("comment"),
            )
            if warned is not None:
                warned.add(name)
        return "foreign"

    updates: Dict[str, Any] = {}
    if normalize_hostname(record.get("canonical") or "") != cfg.cname_target:
        updates["canonical"] = cfg.cname_target
    if cfg.record_ttl is not None and (
        not record.get("use_ttl") or record.get("ttl") != cfg.record_ttl
    ):
        updates["ttl"] = cfg.record_ttl
        updates["use_ttl"] = True

    if not updates:
        return "ok"
    if cfg.dry_run:
        logger.info("[dry-run] Would update CNAME %s with %s", name, updates)
        return "updated"
    infoblox.update(record["_ref"], updates)
    logger.info("Updated CNAME %s -> %s", name, cfg.cname_target)
    return "updated"


def delete_record(cfg: Config, infoblox: InfobloxClient, name: str) -> str:
    record = infoblox.get_cname(name)
    if record is None:
        return "missing"
    if (record.get("comment") or "") != cfg.record_comment:
        return "foreign"
    if cfg.dry_run:
        return "dry-run"
    infoblox.delete(record["_ref"])
    return "deleted"


def sync_once(
    cfg: Config,
    infoblox: InfobloxClient,
    state: State,
    desired: Set[str],
    now: float,
    warned: Optional[Set[str]] = None,
) -> SyncResult:
    """Run one reconcile cycle.

    1. Adopt managed records found in Infoblox that we are not tracking yet
       (e.g. after a lost state file), so they get the normal expiry countdown.
    2. Ensure a CNAME exists for every hostname currently present in labels.
    3. Delete managed records whose hostname has not been seen for longer
       than the configured expiry.
    """
    result = SyncResult()

    # Drop tracked hostnames that no longer belong to the configured zone.
    for name in [n for n in state.last_seen if not in_zone(n, cfg.zone)]:
        logger.info("Forgetting %s: outside managed zone %s", name, cfg.zone)
        state.remove(name)

    managed_by_name: Dict[str, Dict[str, Any]] = {}
    try:
        for record in infoblox.list_managed_cnames(cfg.zone, cfg.record_comment):
            managed_by_name[normalize_hostname(record.get("name") or "")] = record
    except InfobloxError as exc:
        logger.error("Failed to list managed CNAME records: %s", exc)
        result.errors += 1

    for name in managed_by_name:
        if name not in state.last_seen and name not in desired:
            logger.info(
                "Adopted existing managed CNAME %s; it will be removed if its "
                "label stays absent for %d days",
                name,
                cfg.expire_after // 86400,
            )
            state.mark_seen(name, now)
            result.adopted += 1

    for name in sorted(desired):
        state.mark_seen(name, now)
        try:
            outcome = ensure_record(cfg, infoblox, name, managed_by_name.get(name), warned)
        except InfobloxError as exc:
            logger.error("Failed to ensure CNAME %s: %s", name, exc)
            result.errors += 1
            continue
        if outcome == "created":
            result.created += 1
        elif outcome == "updated":
            result.updated += 1
        elif outcome == "foreign":
            result.foreign += 1

    for name, last_seen in sorted(state.last_seen.items()):
        if name in desired:
            continue
        age = now - last_seen
        if age <= cfg.expire_after:
            continue
        try:
            outcome = delete_record(cfg, infoblox, name)
        except InfobloxError as exc:
            logger.error("Failed to delete expired CNAME %s: %s", name, exc)
            result.errors += 1
            continue
        if outcome == "deleted":
            logger.info("Deleted CNAME %s (label unseen for %.1f days)", name, age / 86400)
            state.remove(name)
            result.deleted += 1
        elif outcome == "missing":
            logger.info("CNAME %s already gone; no longer tracking it", name)
            state.remove(name)
        elif outcome == "foreign":
            logger.warning(
                "Not deleting CNAME %s: it is no longer marked as managed by "
                "this instance; dropping it from tracking",
                name,
            )
            state.remove(name)
        elif outcome == "dry-run":
            logger.info("[dry-run] Would delete CNAME %s (label unseen for %.1f days)", name, age / 86400)

    result.tracked = len(state.last_seen)
    state.save()
    return result
