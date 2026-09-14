"""Upstream HTTP client used to fetch and stream remote media.

Every request made by the integration goes through :class:`UpstreamClient`, so
credentials, custom headers, TLS verification and timeouts are applied in one
place. Two details matter for streaming:

* the ``total`` timeout must be disabled for endless MJPEG streams, while a
  ``sock_read`` timeout still detects a silently dead upstream;
* digest authentication needs a challenge/response round trip, which is
  implemented as a single transparent retry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp
from homeassistant.core import callback
from homeassistant.util import ssl as ssl_util

from .const import (
    AUTH_TYPE_BASIC,
    AUTH_TYPE_DIGEST,
    SOURCE_TYPE_HLS,
    SOURCE_TYPE_IMAGE,
    SOURCE_TYPE_MJPEG,
    SOURCE_TYPE_PROGRESSIVE,
    USER_AGENT,
)
from .digest import DigestChallenge, DigestError, build_authorization
from .models import ProxyConfig

_LOGGER = logging.getLogger(__name__)

#: Read timeout applied to streaming requests when the user configured none.
STREAM_SOCK_READ_TIMEOUT = 30

#: Maximum number of bytes read for a probe / playlist / snapshot response.
MAX_TEXT_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


@callback
def create_session(verify_ssl: bool = True) -> aiohttp.ClientSession:
    """Create the HTTP session used for one proxy's upstream connections.

    A dedicated session, rather than Home Assistant's shared one, keeps the
    long-lived streaming connections of this integration out of the shared
    connection pool and lets every config entry decide whether upstream TLS
    certificates must be verified. Connections are opened on demand: a proxy
    only talks to its upstream while somebody is watching it.
    """
    ssl_context = (
        ssl_util.client_context(ssl_util.SSLCipherList.PYTHON_DEFAULT)
        if verify_ssl
        else ssl_util.client_context_no_verify(ssl_util.SSLCipherList.PYTHON_DEFAULT)
    )
    connector = aiohttp.TCPConnector(
        limit=0,
        limit_per_host=0,
        ssl=ssl_context,
    )
    return aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=None))


async def async_close_session(session: aiohttp.ClientSession) -> None:
    """Close an upstream session, ignoring an already closed session."""
    if not session.closed:
        await session.close()


class UpstreamError(Exception):
    """Raised when the upstream stream cannot be used."""


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What the upstream answered to a probe request."""

    status: int
    content_type: str | None
    headers: Mapping[str, str]
    is_multipart: bool
    is_playlist: bool
    is_image: bool
    is_video: bool
    body_prefix: bytes = b""

    @property
    def ok(self) -> bool:
        """Return ``True`` when the upstream answered successfully."""
        return 200 <= self.status < 400


class UpstreamClient:
    """Authenticated HTTP client bound to one proxy configuration."""

    def __init__(self, config: ProxyConfig, session: aiohttp.ClientSession) -> None:
        """Create a client for ``config`` using an existing aiohttp session."""
        self._config = config
        self._session = session
        self._digest_challenge: DigestChallenge | None = None
        self._nonce_count = 0

    @property
    def session(self) -> aiohttp.ClientSession:
        """Return the underlying aiohttp session."""
        return self._session

    # ------------------------------------------------------------- header/auth

    def build_headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return the headers to send upstream for this proxy."""
        headers = {
            "User-Agent": USER_AGENT,
            # Keep byte counts identical to the upstream payload: aiohttp would
            # transparently decompress a gzip body and invalidate Content-Length
            # and byte ranges that we forward verbatim.
            "Accept-Encoding": "identity",
        }
        headers.update(self._config.headers)
        if extra:
            headers.update(extra)
        return headers

    @property
    def safe_url(self) -> str:
        """Return the configured upstream URL without credentials or query."""
        return _redact(self._config.url)

    def timeout(
        self,
        *,
        total: float | None = None,
        sock_read: float | None = None,
    ) -> aiohttp.ClientTimeout:
        """Build a client timeout, defaulting to the configured values."""
        return aiohttp.ClientTimeout(
            total=total,
            sock_connect=float(self._config.upstream_timeout),
            sock_read=(float(self._config.stall_timeout) if sock_read is None else sock_read),
        )

    def _basic_auth(self) -> aiohttp.BasicAuth | None:
        if self._config.auth_type == AUTH_TYPE_BASIC and self._config.username:
            return aiohttp.BasicAuth(self._config.username, self._config.password or "")
        return None

    def _digest_auth_header(self, method: str, url: str) -> str | None:
        if self._config.auth_type != AUTH_TYPE_DIGEST or self._digest_challenge is None:
            return None
        self._nonce_count += 1
        try:
            return build_authorization(
                method,
                url,
                self._config.username or "",
                self._config.password or "",
                self._digest_challenge,
                nonce_count=self._nonce_count,
            )
        except DigestError as err:
            raise UpstreamError(f"digest authentication failed: {err}") from err

    @asynccontextmanager
    async def open(
        self,
        url: str | None = None,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
        stream: bool = True,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """Open an upstream request, handling digest authentication retries."""
        target = url or self._config.url
        request_headers = self.build_headers(headers)
        request_timeout = timeout or self.timeout(
            total=None if stream else float(self._config.upstream_timeout)
        )

        for attempt in (0, 1):
            auth_header = self._digest_auth_header(method, target) if attempt else None
            if auth_header:
                request_headers["Authorization"] = auth_header
            try:
                async with self._session.request(
                    method,
                    target,
                    headers=request_headers,
                    auth=self._basic_auth(),
                    timeout=request_timeout,
                    allow_redirects=True,
                ) as response:
                    if (
                        attempt == 0
                        and response.status == 401
                        and self._config.auth_type == AUTH_TYPE_DIGEST
                    ):
                        challenge_header = response.headers.get("WWW-Authenticate", "")
                        try:
                            self._digest_challenge = DigestChallenge.parse(challenge_header)
                        except DigestError as err:
                            raise UpstreamError(
                                f"upstream requested digest authentication "
                                f"but sent an unusable challenge: {err}"
                            ) from err
                        self._nonce_count = 0
                        _LOGGER.debug(
                            "%s: retrying with digest authentication (realm=%s)",
                            self._config.name,
                            self._digest_challenge.realm,
                        )
                        continue
                    yield response
                    return
            except aiohttp.ClientError as err:
                raise UpstreamError(f"cannot reach {_redact(target)}: {err}") from err
            except TimeoutError as err:
                raise UpstreamError(f"timeout while contacting {_redact(target)}") from err

        raise UpstreamError("digest authentication renegotiation failed")

    # ------------------------------------------------------------------ helpers

    async def probe(self, url: str | None = None) -> ProbeResult:
        """Inspect the upstream response headers without consuming the stream."""
        target = url or self._config.url
        headers = self.build_headers({"Accept": "*/*"})
        timeout = self.timeout(total=float(self._config.upstream_timeout))
        async with self.open(url=target, headers=headers, timeout=timeout) as response:
            content_type = _normalise_content_type(response.headers.get("Content-Type"))
            prefix = b""
            if response.status < 400 and _needs_body_probe(content_type):
                with suppress(asyncio.IncompleteReadError, TimeoutError, OSError):
                    prefix = await response.content.readexactly(512)
            return ProbeResult(
                status=response.status,
                content_type=content_type,
                headers={key.lower(): value for key, value in response.headers.items()},
                is_multipart=bool(content_type and "multipart/" in content_type),
                is_playlist=bool(
                    content_type
                    and (
                        "mpegurl" in content_type
                        or "vnd.apple" in content_type
                        or prefix.lstrip().startswith(b"#EXTM3U")
                    )
                ),
                is_image=bool(content_type and content_type.startswith("image/")),
                is_video=bool(content_type and content_type.startswith("video/")),
                body_prefix=prefix,
            )

    async def read_bytes(
        self,
        url: str | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        limit: int = MAX_TEXT_BYTES,
        what: str = "resource",
    ) -> tuple[bytes, Mapping[str, str], int]:
        """Fetch a small upstream resource entirely into memory."""
        target = url or self._config.url
        request_headers = self.build_headers(headers)
        timeout = self.timeout(total=float(self._config.upstream_timeout))
        async with self.open(
            url=target, headers=request_headers, timeout=timeout, stream=False
        ) as response:
            if response.status >= 400:
                raise UpstreamError(f"upstream returned HTTP {response.status} for the {what}")
            body = await response.content.read(limit + 1)
            if len(body) > limit:
                raise UpstreamError(f"the upstream {what} is larger than {limit} bytes")
            return body, response.headers, response.status


def _needs_body_probe(content_type: str | None) -> bool:
    return content_type is None or "octet-stream" in content_type


async def async_detect_source_type(client: UpstreamClient) -> tuple[str, str | None]:
    """Probe the upstream and resolve its concrete source type.

    Returns a ``(source_type, content_type)`` tuple where the source type is one
    of ``mjpeg``, ``image``, ``hls`` or ``progressive``.
    """
    result = await client.probe()
    content_type = result.content_type
    if not result.ok:
        raise UpstreamError(f"upstream returned HTTP {result.status}")
    if result.is_multipart:
        return SOURCE_TYPE_MJPEG, content_type
    if content_type and content_type.startswith("image/"):
        return SOURCE_TYPE_IMAGE, content_type
    if result.is_playlist:
        return SOURCE_TYPE_HLS, content_type
    if result.is_video:
        return SOURCE_TYPE_PROGRESSIVE, content_type
    raise UpstreamError(
        "could not detect the stream type "
        f"(Content-Type: {content_type or 'unknown'}, HTTP {result.status})"
    )


def _normalise_content_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower()


def _redact(url: str) -> str:
    """Return a URL safe for logs (no credentials, no query string)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}{parts.path}"


def redact_url(url: str) -> str:
    """Return a URL safe for logs and diagnostics (no credentials, no query)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}{parts.path}"
