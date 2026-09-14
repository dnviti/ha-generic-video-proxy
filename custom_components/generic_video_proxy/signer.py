"""Signed URL payloads.

Media URLs are consumed by browser elements (``<img>``, ``<video>``, hls.js)
that cannot attach an ``Authorization`` header. The integration therefore hands
out *signed* URLs: an authenticated call over the Home Assistant websocket API
mints a URL that the browser can fetch without credentials.

Two independent layers protect an upstream fetch:

1. the Home Assistant signed-path mechanism (``authSig``) authenticates the
   request against this Home Assistant instance;
2. this module signs the *upstream* URL that is embedded in the request, so a
   client cannot swap it for an arbitrary target. Without that second layer a
   media endpoint accepting ``?u=<url>`` would be an open proxy / SSRF vector.

The scheme is a compact ``<base64url(payload)>.<base64url(hmac)>`` token, which
keeps upstream URLs opaque and tamper proof while staying URL safe.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

_SIGNATURE_BYTES = 16


class InvalidSignature(Exception):
    """Raised when a signed payload fails verification."""


def new_secret() -> str:
    """Return a fresh random secret suitable for :class:`PayloadSigner`."""
    return secrets.token_hex(32)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class PayloadSigner:
    """Sign and verify opaque payloads with an HMAC-SHA256 signature."""

    def __init__(self, secret: str | bytes) -> None:
        """Create a signer bound to ``secret``."""
        self._secret = secret.encode("utf-8") if isinstance(secret, str) else secret
        if not self._secret:
            raise ValueError("secret must not be empty")

    def sign(self, payload: str) -> str:
        """Return a URL-safe token carrying ``payload``."""
        encoded = _b64encode(payload.encode("utf-8"))
        return f"{encoded}.{self._signature(encoded)}"

    def unsign(self, token: str) -> str:
        """Return the payload of ``token`` or raise :class:`InvalidSignature`."""
        encoded, _, signature = token.partition(".")
        if not encoded or not signature:
            raise InvalidSignature("malformed token")
        if not hmac.compare_digest(signature, self._signature(encoded)):
            raise InvalidSignature("signature mismatch")
        try:
            return _b64decode(encoded).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as err:  # pragma: no cover - defensive
            raise InvalidSignature("undecodable payload") from err

    def _signature(self, encoded: str) -> str:
        digest = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest()
        return _b64encode(digest[:_SIGNATURE_BYTES])


def is_safe_upstream_url(url: str) -> bool:
    """Return ``True`` when ``url`` may be fetched as a proxy target.

    Only absolute ``http``/``https`` URLs are accepted; anything else (including
    ``file://`` and relative references) is rejected.
    """
    if not url:
        return False
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.netloc)
