"""HTTP endpoints that proxy upstream media through Home Assistant.

Every byte of video, image and playlist data reaches the browser from Home
Assistant itself, at the same origin and scheme the dashboard is served from.
That is what makes a stream work identically on ``http://homeassistant.local``,
on an ``https://`` reverse proxy and through Nabu Casa - the browser never sees
the upstream address, so there is no mixed content to block.

Requests are authorised by the tokens minted in :mod:`security` (path segments,
never query parameters, so players that append query parameters cannot break
authentication), and the views deliberately declare ``requires_auth = False``:
the Home Assistant authentication middleware would otherwise reject a plain
``<img src>`` request before the token could be checked.
"""

from __future__ import annotations

import logging
from contextlib import aclosing, suppress
from http import HTTPStatus
from typing import ClassVar, Final
from urllib.parse import urlsplit

from aiohttp import hdrs, web
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONTENT_TYPE_MULTIPART
from homeassistant.core import HomeAssistant

from .const import (
    API_BASE,
    RESOURCE_MEDIA,
    RESOURCE_PLAYLIST,
    RESOURCE_POSTER,
    RESOURCE_SEGMENT,
    RESOURCE_SNAPSHOT,
    RESOURCE_STATUS,
    RESOURCE_STREAM,
)
from .hls import is_playlist, rewrite_playlist
from .runtime import ProxyRuntime, get_runtime
from .security import InvalidToken, MediaToken
from .upstream import MAX_TEXT_BYTES, UpstreamError

_LOGGER = logging.getLogger(__name__)

#: Boundary used for the MJPEG streams this integration produces.
STREAM_BOUNDARY: Final = "frameboundary"

#: Chunk size used when piping an upstream body to the browser.
STREAM_CHUNK: Final = 65536

#: Response headers copied verbatim from the upstream response.
_PASSTHROUGH_HEADERS: Final = (
    hdrs.CONTENT_TYPE,
    hdrs.CONTENT_LENGTH,
    hdrs.CONTENT_RANGE,
    hdrs.ACCEPT_RANGES,
    hdrs.ETAG,
    hdrs.LAST_MODIFIED,
    hdrs.CACHE_CONTROL,
)

HLS_CONTENT_TYPE: Final = "application/vnd.apple.mpegurl"


def is_playlist_url(url: str) -> bool:
    """Return ``True`` when the URL path looks like an HLS playlist."""
    path = urlsplit(url).path.lower()
    return path.endswith(".m3u8") or path.endswith(".m3u")


class ProxyView(HomeAssistantView):
    """Base class mapping errors and tokens for all proxy views."""

    #: Browser media elements cannot send an Authorization header, so these views
    #: authenticate themselves with the signed token in the URL path. Declaring
    #: ``requires_auth = False`` is what lets the request reach :meth:`handle`:
    #: Home Assistant's request handler would otherwise answer 401 before the
    #: token is ever looked at.
    requires_auth = False

    #: Tokens of these resource kinds are accepted by the view.
    resources: frozenset[str] = frozenset()

    def __init__(self, hass: HomeAssistant) -> None:
        """Store the Home Assistant instance for later use."""
        self.hass = hass

    async def get(self, request: web.Request, **kwargs: str) -> web.StreamResponse:
        """Authorise the request and delegate to :meth:`handle`."""
        try:
            return await self.handle(request, **kwargs)
        except InvalidToken as err:
            _LOGGER.debug("%s: rejected media request: %s", self.name, err)
            raise _rejected(request) from err
        except UpstreamError as err:
            _LOGGER.warning("%s: %s", self.name, err)
            return web.json_response({"error": str(err)}, status=HTTPStatus.BAD_GATEWAY)

    async def handle(self, request: web.Request, **kwargs: str) -> web.StreamResponse:
        """Serve the request; implemented by subclasses."""
        raise NotImplementedError

    # ------------------------------------------------------------------ helpers

    def _runtime(self, entry_id: str) -> ProxyRuntime:
        runtime = get_runtime(self.hass, entry_id)
        if runtime is None:
            raise web.HTTPNotFound
        return runtime

    def _token(self, runtime: ProxyRuntime, entry_id: str, token: str) -> MediaToken:
        media = runtime.tokens.async_verify(token, entry_id=entry_id)
        if media.resource not in self.resources:
            raise InvalidToken(f"token for '{media.resource}' cannot be used on '{self.name}'")
        return media


def _rejected(request: web.Request) -> web.HTTPException:
    """Return the right rejection status for a media request.

    Home Assistant core answers ``401`` only when credentials were actually
    offered, so that a stale ``<img>`` URL is not counted as a failed login by
    the IP ban middleware; unsigned media requests get a plain ``403``.
    """
    if hdrs.AUTHORIZATION in request.headers:
        return web.HTTPUnauthorized
    return web.HTTPForbidden


async def async_write_eof(response: web.StreamResponse) -> None:
    """Close a stream response, ignoring an already gone peer."""
    with suppress(ConnectionResetError, RuntimeError):
        await response.write_eof()


async def async_proxy_mjpeg_stream(
    request: web.Request, runtime: ProxyRuntime
) -> web.StreamResponse:
    """Serve the shared MJPEG stream of ``runtime`` as multipart JPEG."""
    response = web.StreamResponse(status=HTTPStatus.OK)
    response.content_type = CONTENT_TYPE_MULTIPART.format(STREAM_BOUNDARY)
    response.headers[hdrs.CACHE_CONTROL] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers[hdrs.PRAGMA] = "no-cache"
    response.headers[hdrs.EXPIRES] = "0"
    await response.prepare(request)

    boundary = STREAM_BOUNDARY.encode("ascii")
    try:
        first = True
        # aclosing makes the hub release this viewer deterministically instead of
        # waiting for the garbage collector to finalise the generator.
        async with aclosing(runtime.hub.async_subscribe()) as frames:
            async for frame in frames:
                part = (
                    b"--"
                    + boundary
                    + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode("ascii")
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
                await response.write(part)
                if first:
                    # Chrome renders frame n-1, so the first frame is sent twice.
                    await response.write(part)
                    first = False
    except ConnectionResetError:
        _LOGGER.debug("%s: viewer disconnected from the MJPEG stream", runtime.config.name)
    finally:
        await async_write_eof(response)
    return response


class StreamView(ProxyView):
    """Multipart MJPEG stream of a proxied source."""

    url = f"{API_BASE}/{{entry_id}}/stream.mjpeg/{{token}}"
    name = "api:generic_video_proxy:stream"
    resources = frozenset({RESOURCE_STREAM})

    async def handle(self, request: web.Request, entry_id: str, token: str) -> web.StreamResponse:
        """Stream MJPEG frames to the browser."""
        runtime = self._runtime(entry_id)
        self._token(runtime, entry_id, token)
        return await async_proxy_mjpeg_stream(request, runtime)


class PlaylistView(ProxyView):
    """HLS playlist with every URI rewritten to this proxy."""

    url = f"{API_BASE}/{{entry_id}}/stream.m3u8/{{token}}"
    name = "api:generic_video_proxy:playlist"
    resources = frozenset({RESOURCE_PLAYLIST})

    async def handle(self, request: web.Request, entry_id: str, token: str) -> web.StreamResponse:
        """Fetch, rewrite and return an HLS playlist."""
        runtime = self._runtime(entry_id)
        media = self._token(runtime, entry_id, token)
        upstream = media.upstream or runtime.config.url

        body, _headers, _status = await runtime.client.read_bytes(
            upstream, limit=MAX_TEXT_BYTES, what="playlist"
        )
        text = body.decode("utf-8", errors="replace")
        if not is_playlist(text):
            raise UpstreamError(
                f"{runtime.config.name}: the configured source is not an HLS playlist"
            )

        def rewrite(uri: str, byte_range: str | None) -> str:
            resource = RESOURCE_PLAYLIST if is_playlist_url(uri) else RESOURCE_SEGMENT
            return runtime.tokens.async_url(
                entry_id=entry_id,
                resource=resource,
                upstream=uri,
                byte_range=byte_range,
            )

        return web.Response(
            text=rewrite_playlist(text, upstream, rewrite),
            content_type=HLS_CONTENT_TYPE,
            headers={hdrs.CACHE_CONTROL: "no-store"},
        )


class MediaProxyView(ProxyView):
    """Progressive media and HLS segments, with byte range support."""

    url = f"{API_BASE}/{{entry_id}}/media/{{token}}"
    extra_urls: ClassVar[list[str]] = [f"{API_BASE}/{{entry_id}}/segment/{{token}}"]
    name = "api:generic_video_proxy:media"
    resources = frozenset({RESOURCE_MEDIA, RESOURCE_SEGMENT})

    async def handle(self, request: web.Request, entry_id: str, token: str) -> web.StreamResponse:
        """Pipe an upstream media resource to the browser."""
        runtime = self._runtime(entry_id)
        media = self._token(runtime, entry_id, token)
        upstream = media.upstream or runtime.config.url

        # A player that knows about #EXT-X-BYTERANGE sends its own Range header;
        # the range recorded in the token is the fallback.
        byte_range = request.headers.get(hdrs.RANGE) or media.byte_range
        request_headers = {hdrs.RANGE: byte_range} if byte_range else None
        timeout = runtime.client.timeout(total=None, sock_read=runtime.config.stall_timeout)

        async with runtime.client.open(
            url=upstream, headers=request_headers, timeout=timeout
        ) as upstream_response:
            if upstream_response.status == HTTPStatus.NOT_FOUND:
                raise web.HTTPNotFound
            if upstream_response.status >= HTTPStatus.BAD_REQUEST:
                raise UpstreamError(
                    f"{runtime.config.name}: upstream returned "
                    f"HTTP {upstream_response.status} for the media resource"
                )

            response = web.StreamResponse(status=upstream_response.status)
            for header in _PASSTHROUGH_HEADERS:
                if (value := upstream_response.headers.get(header)) is not None:
                    response.headers[header] = value
            await response.prepare(request)
            try:
                async for chunk in upstream_response.content.iter_chunked(STREAM_CHUNK):
                    await response.write(chunk)
            except ConnectionResetError:
                # The browser closed the connection (seek, tab closed, ...).
                pass
            finally:
                await async_write_eof(response)
            return response


class StillView(ProxyView):
    """Still images: the current frame of the stream, or a configured poster."""

    url = f"{API_BASE}/{{entry_id}}/snapshot.jpg/{{token}}"
    extra_urls: ClassVar[list[str]] = [f"{API_BASE}/{{entry_id}}/poster.jpg/{{token}}"]
    name = "api:generic_video_proxy:still"
    resources = frozenset({RESOURCE_SNAPSHOT, RESOURCE_POSTER})

    async def handle(self, request: web.Request, entry_id: str, token: str) -> web.StreamResponse:
        """Return a single JPEG image."""
        runtime = self._runtime(entry_id)
        media = self._token(runtime, entry_id, token)

        if media.resource == RESOURCE_POSTER:
            if runtime.config.poster_url is None:
                raise web.HTTPNotFound
            body, _headers, _status = await runtime.client.read_bytes(
                runtime.config.poster_url, what="poster"
            )
        else:
            frame = await runtime.async_snapshot()
            if frame is None:
                raise web.HTTPNotFound
            body = frame

        return web.Response(
            body=body,
            content_type="image/jpeg",
            headers={hdrs.CACHE_CONTROL: "no-store"},
        )


class StatusView(ProxyView):
    """JSON health report for one proxy, used by the card and for debugging."""

    url = f"{API_BASE}/{{entry_id}}/status/{{token}}"
    name = "api:generic_video_proxy:status"
    resources = frozenset({RESOURCE_STATUS})

    async def handle(self, request: web.Request, entry_id: str, token: str) -> web.StreamResponse:
        """Return the current stream status."""
        runtime = self._runtime(entry_id)
        self._token(runtime, entry_id, token)
        return self.json(runtime.status())


VIEWS: Final = (
    StreamView,
    PlaylistView,
    MediaProxyView,
    StillView,
    StatusView,
)


def async_register_views(hass: HomeAssistant) -> None:
    """Register every proxy view once per Home Assistant instance."""
    for view in VIEWS:
        hass.http.register_view(view(hass))
