"""Minimal, dependency free HTTP Digest authentication (RFC 7616 / RFC 2617).

``aiohttp`` 3.11 ships only basic authentication, the legacy ``DigestAuth``
helper was removed and ``DigestAuthMiddleware`` is unavailable, yet many IP
cameras and NVRs only accept digest authentication. This module implements just
enough of the specification to talk to them:

* challenge parsing (``WWW-Authenticate: Digest ...``);
* ``MD5``, ``MD5-sess``, ``SHA-256`` and ``SHA-256-sess`` algorithms;
* ``qop=auth``, including the legacy ``qop``-less variant;
* ``opaque`` and ``stale`` handling.

The implementation is pure (no I/O) so it is verified against the reference
vectors published in RFC 2617 and RFC 7616.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlparse

_ALGORITHMS = {
    "MD5": hashlib.md5,
    "MD5-SESS": hashlib.md5,
    "SHA-256": hashlib.sha256,
    "SHA-256-SESS": hashlib.sha256,
}


class DigestError(ValueError):
    """Raised when a digest challenge cannot be understood."""


@dataclass(frozen=True, slots=True)
class DigestChallenge:
    """A parsed ``WWW-Authenticate: Digest`` challenge."""

    realm: str
    nonce: str
    qop: str | None = None
    opaque: str | None = None
    algorithm: str = "MD5"
    stale: bool = False

    @property
    def sess(self) -> bool:
        """Return ``True`` for the ``*-sess`` algorithm variants."""
        return self.algorithm.upper().endswith("-SESS")

    @property
    def uses_qop_auth(self) -> bool:
        """Return ``True`` when the server expects ``qop=auth``."""
        if not self.qop:
            return False
        return "auth" in [item.strip().lower() for item in self.qop.split(",")]

    @classmethod
    def parse(cls, header_value: str) -> DigestChallenge:
        """Parse a ``WWW-Authenticate`` header value."""
        if not header_value or not header_value.strip().lower().startswith("digest"):
            raise DigestError("not a digest challenge")
        params = _parse_auth_params(header_value.strip()[len("Digest") :])
        realm = params.get("realm")
        nonce = params.get("nonce")
        if realm is None or nonce is None:
            raise DigestError("digest challenge without realm or nonce")
        algorithm = (params.get("algorithm") or "MD5").upper()
        if algorithm not in _ALGORITHMS:
            raise DigestError(f"unsupported digest algorithm: {algorithm}")
        return cls(
            realm=realm,
            nonce=nonce,
            qop=params.get("qop"),
            opaque=params.get("opaque"),
            algorithm=algorithm,
            stale=(params.get("stale") or "").lower() == "true",
        )


def _parse_auth_params(value: str) -> dict[str, str]:
    """Split ``key=value`` pairs, honouring quoted values with commas."""
    params: dict[str, str] = {}
    key = ""
    buffer = ""
    in_quotes = False
    index = 0
    while index < len(value):
        char = value[index]
        if in_quotes:
            if char == "\\" and index + 1 < len(value):
                buffer += value[index + 1]
                index += 2
                continue
            if char == '"':
                in_quotes = False
            else:
                buffer += char
        elif char == '"':
            in_quotes = True
        elif char == "=" and not key:
            key = buffer.strip().lower()
            buffer = ""
        elif char == "," and key:
            params[key] = buffer.strip()
            key = ""
            buffer = ""
        else:
            buffer += char
        index += 1
    if key:
        params[key] = buffer.strip()
    return params


def build_authorization(
    method: str,
    uri: str,
    username: str,
    password: str,
    challenge: DigestChallenge,
    *,
    nonce_count: int = 1,
    cnonce: str | None = None,
) -> str:
    """Return the value for the ``Authorization`` request header."""
    if not username:
        raise DigestError("digest authentication requires a username")

    hash_func = _ALGORITHMS[challenge.algorithm.upper()]
    path = urlparse(uri).path or uri
    query = urlparse(uri).query
    digest_uri = f"{path}?{query}" if query else path

    cnonce = cnonce or secrets.token_hex(8)
    nc_value = f"{nonce_count:08x}"

    def digest(value: str) -> str:
        return hash_func(value.encode("utf-8")).hexdigest()

    ha1 = digest(f"{username}:{challenge.realm}:{password}")
    if challenge.sess:
        ha1 = digest(f"{ha1}:{challenge.nonce}:{cnonce}")
    ha2 = digest(f"{method.upper()}:{digest_uri}")

    if challenge.uses_qop_auth:
        response = digest(f"{ha1}:{challenge.nonce}:{nc_value}:{cnonce}:auth:{ha2}")
    else:
        response = digest(f"{ha1}:{challenge.nonce}:{ha2}")

    parts = [
        f'username="{username}"',
        f'realm="{challenge.realm}"',
        f'nonce="{challenge.nonce}"',
        f'uri="{digest_uri}"',
        f'response="{response}"',
        f"algorithm={challenge.algorithm}",
    ]
    if challenge.uses_qop_auth:
        parts.extend(["qop=auth", f"nc={nc_value}", f'cnonce="{cnonce}"'])
    if challenge.opaque is not None:
        parts.append(f'opaque="{challenge.opaque}"')
    return "Digest " + ", ".join(parts)
