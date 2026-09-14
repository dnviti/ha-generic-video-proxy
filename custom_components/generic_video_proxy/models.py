"""Validated configuration model for a single proxy entry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .const import (
    AUTH_TYPE_BASIC,
    AUTH_TYPE_DIGEST,
    AUTH_TYPES,
    CONF_AUTH_TYPE,
    CONF_FRAME_BUFFER,
    CONF_HEADERS,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_POSTER_URL,
    CONF_RECONNECT_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SNAPSHOT_URL,
    CONF_SOURCE_TYPE,
    CONF_STALL_TIMEOUT,
    CONF_UPSTREAM_TIMEOUT,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_AUTH_TYPE,
    DEFAULT_FRAME_BUFFER,
    DEFAULT_RECONNECT_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOURCE_TYPE,
    DEFAULT_STALL_TIMEOUT,
    DEFAULT_UPSTREAM_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    MAX_FRAME_BUFFER,
    MAX_RECONNECT_BACKOFF,
    MAX_RECONNECT_INTERVAL,
    MAX_SCAN_INTERVAL,
    MAX_UPSTREAM_TIMEOUT,
    MIN_FRAME_BUFFER,
    MIN_RECONNECT_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_UPSTREAM_TIMEOUT,
    SOURCE_TYPE_RTSP,
    SOURCE_TYPES,
)


class ConfigError(ValueError):
    """Raised when user supplied configuration is not usable."""


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    """Immutable, validated description of one proxied stream."""

    name: str
    url: str
    source_type: str = DEFAULT_SOURCE_TYPE
    snapshot_url: str | None = None
    poster_url: str | None = None
    auth_type: str = DEFAULT_AUTH_TYPE
    username: str | None = None
    password: str | None = None
    verify_ssl: bool = DEFAULT_VERIFY_SSL
    headers: Mapping[str, str] = field(default_factory=dict)
    scan_interval: int = DEFAULT_SCAN_INTERVAL
    reconnect_interval: int = DEFAULT_RECONNECT_INTERVAL
    upstream_timeout: int = DEFAULT_UPSTREAM_TIMEOUT
    stall_timeout: int = DEFAULT_STALL_TIMEOUT
    frame_buffer: int = DEFAULT_FRAME_BUFFER

    @property
    def host(self) -> str:
        """Return the upstream host, used for diagnostics and device info."""
        return urlsplit(self.url).hostname or ""

    @property
    def uses_credentials(self) -> bool:
        """Return ``True`` when the upstream requires HTTP authentication."""
        return self.auth_type in (AUTH_TYPE_BASIC, AUTH_TYPE_DIGEST)

    @property
    def is_rtsp(self) -> bool:
        """Return ``True`` when the upstream is delegated to HA's stream component."""
        return self.source_type == SOURCE_TYPE_RTSP

    @property
    def max_backoff(self) -> int:
        """Return the reconnect backoff ceiling in seconds."""
        return min(
            max(self.reconnect_interval * 12, self.reconnect_interval), MAX_RECONNECT_BACKOFF
        )

    def with_overrides(self, data: Mapping[str, Any]) -> ProxyConfig:
        """Return a copy of this config with ``data`` applied (options flow)."""
        return parse_config({**self.as_dict(include_secrets=True), **data})

    def as_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        """Return the config as a plain dict, optionally without credentials."""
        data: dict[str, Any] = {
            CONF_NAME: self.name,
            CONF_URL: self.url,
            CONF_SOURCE_TYPE: self.source_type,
            CONF_SNAPSHOT_URL: self.snapshot_url,
            CONF_POSTER_URL: self.poster_url,
            CONF_AUTH_TYPE: self.auth_type,
            CONF_VERIFY_SSL: self.verify_ssl,
            CONF_HEADERS: dict(self.headers),
            CONF_SCAN_INTERVAL: self.scan_interval,
            CONF_RECONNECT_INTERVAL: self.reconnect_interval,
            CONF_UPSTREAM_TIMEOUT: self.upstream_timeout,
            CONF_STALL_TIMEOUT: self.stall_timeout,
            CONF_FRAME_BUFFER: self.frame_buffer,
        }
        if include_secrets:
            data[CONF_USERNAME] = self.username
            data[CONF_PASSWORD] = self.password
        return data


def parse_headers(raw: Any) -> dict[str, str]:
    """Parse a ``Key: Value`` header block (or mapping) into a dict."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, Mapping):
        return {str(key): str(value) for key, value in raw.items()}
    if not isinstance(raw, str):
        raise ConfigError("headers must be a mapping or a 'Key: Value' text block")
    headers: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition(":")
        if not sep:
            raise ConfigError(f"invalid header line: {line!r}")
        headers[name.strip()] = value.strip()
    return headers


def _as_int(value: Any, default: int, minimum: int, maximum: int, label: str) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as err:
        raise ConfigError(f"{label} must be a number") from err
    if not minimum <= number <= maximum:
        raise ConfigError(f"{label} must be between {minimum} and {maximum}")
    return number


def validate_url(
    url: Any, *, schemes: tuple[str, ...] = ("http", "https"), label: str = "url"
) -> str:
    """Validate an absolute URL with one of the allowed schemes."""
    if not isinstance(url, str) or not url.strip():
        raise ConfigError(f"{label} is required")
    cleaned = url.strip()
    parts = urlsplit(cleaned)
    if parts.scheme.lower() not in schemes or not parts.netloc:
        allowed = ", ".join(schemes)
        raise ConfigError(f"{label} must be an absolute URL using one of: {allowed}")
    return cleaned


def parse_config(data: Mapping[str, Any], *, existing: ProxyConfig | None = None) -> ProxyConfig:
    """Validate raw config entry data/options into a :class:`ProxyConfig`."""
    if not isinstance(data, Mapping):
        raise ConfigError("configuration must be a mapping")

    source_type = str(data.get(CONF_SOURCE_TYPE) or DEFAULT_SOURCE_TYPE).lower()
    if source_type not in SOURCE_TYPES:
        raise ConfigError(f"unsupported source type: {source_type}")

    if source_type == SOURCE_TYPE_RTSP:
        url = validate_url(data.get(CONF_URL), schemes=("rtsp", "rtsps"), label="url")
    else:
        url = validate_url(data.get(CONF_URL))

    auth_type = str(data.get(CONF_AUTH_TYPE) or DEFAULT_AUTH_TYPE).lower()
    if auth_type not in AUTH_TYPES:
        raise ConfigError(f"unsupported authentication type: {auth_type}")

    username = data.get(CONF_USERNAME) or None
    password = data.get(CONF_PASSWORD) or None
    if existing is not None:
        # Empty means "keep the stored value" when editing an entry.
        username = username if username not in (None, "") else existing.username
        password = password if password not in (None, "") else existing.password
        if data.get(CONF_PASSWORD) == "":
            password = existing.password

    if auth_type in (AUTH_TYPE_BASIC, AUTH_TYPE_DIGEST) and not username:
        raise ConfigError("a username is required when HTTP authentication is enabled")

    snapshot_url = data.get(CONF_SNAPSHOT_URL) or None
    if snapshot_url:
        snapshot_url = validate_url(snapshot_url, label="snapshot url")

    poster_url = data.get(CONF_POSTER_URL) or None
    if poster_url:
        poster_url = validate_url(poster_url, label="poster url")

    name = str(data.get(CONF_NAME) or "").strip()
    if not name:
        raise ConfigError("a name is required")

    verify_ssl = data.get(CONF_VERIFY_SSL)
    if isinstance(verify_ssl, str):
        verify_ssl = verify_ssl.strip().lower() not in ("false", "0", "no", "off")

    return ProxyConfig(
        name=name,
        url=url,
        source_type=source_type,
        snapshot_url=snapshot_url,
        poster_url=poster_url,
        auth_type=auth_type,
        username=username,
        password=password,
        verify_ssl=DEFAULT_VERIFY_SSL if verify_ssl is None else bool(verify_ssl),
        headers=parse_headers(data.get(CONF_HEADERS)),
        scan_interval=_as_int(
            data.get(CONF_SCAN_INTERVAL),
            DEFAULT_SCAN_INTERVAL,
            MIN_SCAN_INTERVAL,
            MAX_SCAN_INTERVAL,
            "snapshot interval",
        ),
        reconnect_interval=_as_int(
            data.get(CONF_RECONNECT_INTERVAL),
            DEFAULT_RECONNECT_INTERVAL,
            MIN_RECONNECT_INTERVAL,
            MAX_RECONNECT_INTERVAL,
            "reconnect interval",
        ),
        upstream_timeout=_as_int(
            data.get(CONF_UPSTREAM_TIMEOUT),
            DEFAULT_UPSTREAM_TIMEOUT,
            MIN_UPSTREAM_TIMEOUT,
            MAX_UPSTREAM_TIMEOUT,
            "upstream timeout",
        ),
        stall_timeout=_as_int(
            data.get(CONF_STALL_TIMEOUT),
            DEFAULT_STALL_TIMEOUT,
            MIN_UPSTREAM_TIMEOUT,
            MAX_UPSTREAM_TIMEOUT,
            "stall timeout",
        ),
        frame_buffer=_as_int(
            data.get(CONF_FRAME_BUFFER),
            DEFAULT_FRAME_BUFFER,
            MIN_FRAME_BUFFER,
            MAX_FRAME_BUFFER,
            "frame buffer",
        ),
    )
