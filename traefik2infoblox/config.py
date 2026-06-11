"""Configuration loading from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional, Union

DEFAULT_WAPI_VERSION = "v2.10"
DEFAULT_STATE_FILE = "/data/state.json"
DEFAULT_HEARTBEAT_FILE = "/tmp/traefik2infoblox.heartbeat"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class ConfigError(ValueError):
    """Raised when the environment configuration is invalid."""


def parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {value!r}")


def parse_duration(value: str, name: str) -> int:
    """Parse a duration like '30', '90s', '5m', '12h', '7d', '2w' into seconds."""
    normalized = value.strip().lower()
    if normalized and normalized[-1] in _DURATION_UNITS:
        number, unit = normalized[:-1], _DURATION_UNITS[normalized[-1]]
    else:
        number, unit = normalized, 1
    try:
        seconds = int(float(number) * unit)
    except ValueError:
        raise ConfigError(
            f"{name} must be a duration like '300', '90s', '5m', '12h' or '7d', got {value!r}"
        ) from None
    if seconds <= 0:
        raise ConfigError(f"{name} must be positive, got {value!r}")
    return seconds


def normalize_fqdn(value: str) -> str:
    return value.strip().strip(".").lower()


def build_wapi_url(raw_url: str, version: str) -> str:
    url = raw_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"INFOBLOX_URL must start with http:// or https://, got {raw_url!r}")
    if not version.startswith("v"):
        version = f"v{version}"
    if "/wapi/" in url:
        return url
    if url.endswith("/wapi"):
        return f"{url}/{version}"
    return f"{url}/wapi/{version}"


def default_record_comment(cname_target: str) -> str:
    return f"Managed by traefik2infoblox for {cname_target}"


def _env_or_file(env: Mapping[str, str], name: str) -> Optional[str]:
    """Read a value from NAME, or from the file referenced by NAME_FILE (docker secrets)."""
    direct = env.get(name) or None
    file_path = env.get(f"{name}_FILE") or None
    if direct and file_path:
        raise ConfigError(f"Set either {name} or {name}_FILE, not both")
    if file_path:
        try:
            with open(file_path, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError as exc:
            raise ConfigError(f"Could not read {name}_FILE ({file_path}): {exc}") from exc
    return direct


@dataclass(frozen=True)
class Config:
    wapi_url: str
    username: str
    password: str
    zone: str
    # None means: auto-detect the host FQDN from the Docker daemon at startup.
    cname_target: Optional[str] = None
    view: str = "default"
    ssl_verify: Union[bool, str] = True
    timeout: int = 30
    sync_interval: int = 60
    expire_after: int = 7 * 86400
    record_comment: str = ""
    record_ttl: Optional[int] = None
    state_file: str = DEFAULT_STATE_FILE
    heartbeat_file: str = DEFAULT_HEARTBEAT_FILE
    require_traefik_enable: bool = False
    dry_run: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        env = os.environ if env is None else env

        username = _env_or_file(env, "INFOBLOX_USERNAME")
        password = _env_or_file(env, "INFOBLOX_PASSWORD")
        raw_url = env.get("INFOBLOX_URL") or None
        zone = env.get("INFOBLOX_ZONE") or None
        cname_target = env.get("CNAME_TARGET") or None

        missing = [
            name
            for name, value in (
                ("INFOBLOX_URL", raw_url),
                ("INFOBLOX_USERNAME", username),
                ("INFOBLOX_PASSWORD", password),
                ("INFOBLOX_ZONE", zone),
            )
            if not value
        ]
        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

        zone = normalize_fqdn(zone)
        cname_target = normalize_fqdn(cname_target) if cname_target else None
        wapi_url = build_wapi_url(raw_url, env.get("INFOBLOX_WAPI_VERSION", DEFAULT_WAPI_VERSION))

        ssl_verify: Union[bool, str] = True
        raw_verify = env.get("INFOBLOX_SSL_VERIFY", "").strip()
        if raw_verify:
            lowered = raw_verify.lower()
            if lowered in _TRUE_VALUES:
                ssl_verify = True
            elif lowered in _FALSE_VALUES:
                ssl_verify = False
            else:
                # Anything else is treated as a path to a CA bundle.
                ssl_verify = raw_verify

        record_ttl: Optional[int] = None
        raw_ttl = env.get("RECORD_TTL", "").strip()
        if raw_ttl:
            try:
                record_ttl = int(raw_ttl)
            except ValueError:
                raise ConfigError(f"RECORD_TTL must be an integer (seconds), got {raw_ttl!r}") from None
            if record_ttl < 0:
                raise ConfigError(f"RECORD_TTL must be >= 0, got {raw_ttl!r}")

        log_level = env.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in _LOG_LEVELS:
            raise ConfigError(f"LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}, got {log_level!r}")

        record_comment = env.get("RECORD_COMMENT", "").strip()
        if not record_comment and cname_target:
            # Include the target so several hosts can safely share one zone:
            # each instance only ever adopts/deletes records carrying its own
            # marker. With an auto-detected target this default is filled in
            # at startup, after detection.
            record_comment = default_record_comment(cname_target)

        return cls(
            wapi_url=wapi_url,
            username=username,
            password=password,
            zone=zone,
            cname_target=cname_target,
            view=env.get("INFOBLOX_VIEW", "default").strip() or "default",
            ssl_verify=ssl_verify,
            timeout=parse_duration(env.get("INFOBLOX_TIMEOUT", "30"), "INFOBLOX_TIMEOUT"),
            sync_interval=parse_duration(env.get("SYNC_INTERVAL", "60"), "SYNC_INTERVAL"),
            expire_after=parse_duration(env.get("EXPIRE_AFTER", "7d"), "EXPIRE_AFTER"),
            record_comment=record_comment,
            record_ttl=record_ttl,
            state_file=env.get("STATE_FILE", DEFAULT_STATE_FILE),
            heartbeat_file=env.get("HEARTBEAT_FILE", DEFAULT_HEARTBEAT_FILE),
            require_traefik_enable=parse_bool(
                env.get("REQUIRE_TRAEFIK_ENABLE", "false"), "REQUIRE_TRAEFIK_ENABLE"
            ),
            dry_run=parse_bool(env.get("DRY_RUN", "false"), "DRY_RUN"),
            log_level=log_level,
        )
