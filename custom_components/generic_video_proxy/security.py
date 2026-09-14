"""Media URL tokens.

Browser elements cannot send an ``Authorization`` header, so every media URL
handed to the browser carries a *self contained* token instead. The token is an
HMAC signed JSON payload that binds

* the config entry it belongs to,
* the resource it may access,
* the upstream URL (for playlists and segments),
* an optional byte range, and
* an expiry timestamp.

Two properties matter:

* **No open proxy.** The upstream URL lives inside the signed payload, so a
  client cannot point the proxy at an arbitrary address; only URLs that this
  integration itself rewrote into a playlist are fetchable.
* **Robust in a URL.** The token is a path segment, so players that append
  query parameters of their own (``hls.js`` adds ``_HLS_msn``/``_HLS_part`` when
  a server advertises blocking playlist reload) cannot break authentication.

The signing secret is generated once per Home Assistant instance and stored with
``helpers.storage.Store`` (``.storage/generic_video_proxy.tokens``), so tokens
survive restarts without ever being derived from user input.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import API_BASE, DOMAIN, SIGNED_URL_TTL
from .signer import InvalidSignature, PayloadSigner, is_safe_upstream_url, new_secret

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.tokens"
_STORAGE_SECRET_FIELD = "secret"

DATA_SIGNER = f"{DOMAIN}_signer"
DATA_SIGNER_LOCK = f"{DOMAIN}_signer_lock"


class InvalidToken(Exception):
    """Raised when a media token is missing, malformed, expired or tampered with."""


@dataclass(frozen=True, slots=True)
class MediaToken:
    """A verified media token."""

    entry_id: str
    resource: str
    upstream: str | None = None
    byte_range: str | None = None
    expires: int = 0


class TokenManager:
    """Issue and verify media URL tokens."""

    def __init__(self, signer: PayloadSigner, *, ttl: int = SIGNED_URL_TTL) -> None:
        """Create a manager around an existing signer."""
        self._signer = signer
        self._ttl = ttl

    @classmethod
    async def async_create(cls, hass: HomeAssistant, *, ttl: int = SIGNED_URL_TTL) -> TokenManager:
        """Return the instance wide token manager, creating the secret if needed."""
        if (manager := hass.data.get(DATA_SIGNER)) is not None:
            return manager
        lock: asyncio.Lock = hass.data.setdefault(DATA_SIGNER_LOCK, asyncio.Lock())
        async with lock:
            if (manager := hass.data.get(DATA_SIGNER)) is not None:
                return manager
            store: Store[dict[str, str]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
            stored = await store.async_load() or {}
            secret = stored.get(_STORAGE_SECRET_FIELD)
            if not secret:
                secret = new_secret()
                await store.async_save({_STORAGE_SECRET_FIELD: secret})
            manager = cls(PayloadSigner(secret), ttl=ttl)
            hass.data[DATA_SIGNER] = manager
            return manager

    # ------------------------------------------------------------------ issuing

    def async_issue(
        self,
        *,
        entry_id: str,
        resource: str,
        upstream: str | None = None,
        byte_range: str | None = None,
        ttl: int | None = None,
    ) -> str:
        """Return a signed token for one media resource."""
        if upstream is not None and not is_safe_upstream_url(upstream):
            raise InvalidToken(f"refusing to sign unsupported upstream url: {upstream!r}")
        payload: dict[str, object] = {
            "e": entry_id,
            "r": resource,
            "x": int(time.time()) + (ttl if ttl is not None else self._ttl),
        }
        if upstream is not None:
            payload["u"] = upstream
        if byte_range is not None:
            payload["b"] = byte_range
        return self._signer.sign(json.dumps(payload, separators=(",", ":"), sort_keys=True))

    def async_url(
        self,
        *,
        entry_id: str,
        resource: str,
        upstream: str | None = None,
        byte_range: str | None = None,
        ttl: int | None = None,
    ) -> str:
        """Return a ready to use, relative media URL."""
        token = self.async_issue(
            entry_id=entry_id,
            resource=resource,
            upstream=upstream,
            byte_range=byte_range,
            ttl=ttl,
        )
        return f"{API_BASE}/{entry_id}/{resource}/{token}"

    # --------------------------------------------------------------- verifying

    def async_verify(self, token: str | None, *, entry_id: str) -> MediaToken:
        """Verify ``token`` and return its payload, or raise :class:`InvalidToken`."""
        if not token:
            raise InvalidToken("missing token")
        try:
            payload = json.loads(self._signer.unsign(token))
        except (InvalidSignature, ValueError) as err:
            raise InvalidToken("invalid token") from err
        if not isinstance(payload, dict):
            raise InvalidToken("malformed token payload")

        try:
            token_entry = str(payload["e"])
            resource = str(payload["r"])
            expires = int(payload["x"])
        except (KeyError, TypeError, ValueError) as err:
            raise InvalidToken("incomplete token payload") from err

        if token_entry != entry_id:
            raise InvalidToken("token does not belong to this entry")
        if expires and expires < time.time():
            raise InvalidToken("token expired")

        upstream = payload.get("u")
        if upstream is not None and not is_safe_upstream_url(str(upstream)):
            raise InvalidToken("token carries an unsupported upstream url")

        byte_range = payload.get("b")
        return MediaToken(
            entry_id=token_entry,
            resource=resource,
            upstream=str(upstream) if upstream is not None else None,
            byte_range=str(byte_range) if byte_range is not None else None,
            expires=expires,
        )
