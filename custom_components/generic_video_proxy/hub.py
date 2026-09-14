"""Shared upstream connection manager.

The whole point of this integration is that Home Assistant, not the browser,
talks to the camera. :class:`StreamHub` owns exactly one upstream connection per
configured stream and fans the frames out to every connected viewer:

* several browsers/tabs watch the same camera through a single upstream
  connection;
* the upstream connection is opened lazily when somebody watches and closed
  again shortly after the last viewer leaves;
* reconnects use exponential backoff and the connection state is exposed for
  diagnostics and for the status endpoint;
* the most recent frame is cached, which gives the camera entity and the
  ``snapshot.jpg`` endpoint a still image for free.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from homeassistant.core import HomeAssistant

from .const import (
    SOURCE_TYPE_AUTO,
    SOURCE_TYPE_IMAGE,
    SOURCE_TYPE_MJPEG,
)
from .mjpeg import MultipartError, MultipartFrameParser, parse_boundary
from .models import ProxyConfig
from .upstream import MAX_SNAPSHOT_BYTES, UpstreamClient, UpstreamError, redact_url

_LOGGER = logging.getLogger(__name__)

#: Fallback boundary, matching the one Home Assistant uses for its own streams.
DEFAULT_BOUNDARY = "frameboundary"

#: Chunk size used when reading from the upstream socket.
READ_CHUNK = 16384

#: How long the upstream connection is kept alive after the last viewer leaves.
IDLE_STOP_DELAY = 20.0

#: Window used to compute the frames per second figure.
FPS_WINDOW = 10.0

_ACCEPT_HEADER = "multipart/x-mixed-replace, image/jpeg, image/*;q=0.9, */*;q=0.1"


class HubState(StrEnum):
    """State of the managed upstream connection."""

    IDLE = "idle"
    CONNECTING = "connecting"
    LIVE = "live"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class HubStats:
    """Serialisable snapshot of the hub state."""

    state: str
    error: str | None
    clients: int
    frames: int
    bytes_received: int
    fps: float
    connected_at: float | None
    last_frame_at: float | None
    last_frame_bytes: int


class StreamHub:
    """Manage one upstream stream and fan it out to subscribers."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        config: ProxyConfig,
        client: UpstreamClient,
    ) -> None:
        """Create a hub for one config entry."""
        self._hass = hass
        self._entry_id = entry_id
        self._config = config
        self._client = client

        self._subscribers: set[asyncio.Queue[bytes]] = set()
        self._last_frame: bytes | None = None
        self._last_frame_at: float | None = None
        self._frames = 0
        self._bytes = 0
        self._frame_times: deque[float] = deque(maxlen=512)
        self._state = HubState.IDLE
        self._error: str | None = None
        self._connected_at: float | None = None
        self._backoff = config.reconnect_interval
        self._supervisor: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None

    # --------------------------------------------------------------- properties

    @property
    def config(self) -> ProxyConfig:
        """Return the configuration this hub serves."""
        return self._config

    @property
    def state(self) -> HubState:
        """Return the current connection state."""
        return self._state

    @property
    def last_frame(self) -> bytes | None:
        """Return the most recent frame, if any."""
        return self._last_frame

    @property
    def last_frame_at(self) -> float | None:
        """Return the monotonic timestamp of the most recent frame."""
        return self._last_frame_at

    @property
    def is_running(self) -> bool:
        """Return ``True`` while the supervisor task is alive."""
        return self._supervisor is not None and not self._supervisor.done()

    @property
    def subscriber_count(self) -> int:
        """Return the number of active viewers."""
        return len(self._subscribers)

    @property
    def stats(self) -> HubStats:
        """Return a serialisable status snapshot."""
        now = time.monotonic()
        recent = [stamp for stamp in self._frame_times if now - stamp <= FPS_WINDOW]
        fps = len(recent) / FPS_WINDOW if recent else 0.0
        return HubStats(
            state=self._state.value,
            error=self._error,
            clients=len(self._subscribers),
            frames=self._frames,
            bytes_received=self._bytes,
            fps=round(fps, 2),
            connected_at=self._connected_at,
            last_frame_at=self._last_frame_at,
            last_frame_bytes=len(self._last_frame) if self._last_frame else 0,
        )

    def is_frame_fresh(self, max_age: float = 10.0) -> bool:
        """Return ``True`` when the cached frame is recent enough to reuse."""
        if self._last_frame is None or self._last_frame_at is None:
            return False
        return (time.monotonic() - self._last_frame_at) <= max_age

    # ---------------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        """Start the supervisor task if it is not running yet."""
        self._cancel_idle_task()
        if self.is_running:
            return
        self._state = HubState.CONNECTING
        self._supervisor = self._hass.async_create_background_task(
            self._async_run(), name=f"generic_video_proxy {self._config.name}"
        )
        _LOGGER.debug("%s: upstream connection started", self._config.name)

    async def async_stop(self) -> None:
        """Stop the supervisor task and close the upstream connection."""
        self._cancel_idle_task()
        supervisor = self._supervisor
        self._supervisor = None
        if supervisor is not None and not supervisor.done():
            supervisor.cancel()
            with suppress(asyncio.CancelledError):
                await supervisor
        self._state = HubState.STOPPED
        _LOGGER.debug("%s: upstream connection stopped", self._config.name)

    async def async_subscribe(self) -> AsyncIterator[bytes]:
        """Yield upstream frames until the consumer stops iterating."""
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max(1, self._config.frame_buffer))
        await self.async_start()
        self._subscribers.add(queue)
        _LOGGER.debug("%s: viewer attached (%d active)", self._config.name, len(self._subscribers))
        try:
            if self._last_frame is not None:
                yield self._last_frame
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
            _LOGGER.debug(
                "%s: viewer detached (%d active)",
                self._config.name,
                len(self._subscribers),
            )
            if not self._subscribers:
                self._schedule_idle_stop()

    async def async_refresh_frame(self, timeout: float | None = None) -> bytes | None:
        """Fetch a single frame from the upstream, bypassing the supervisor."""
        is_single_shot = self._config.source_type == SOURCE_TYPE_IMAGE or self._config.is_rtsp
        # Multipart sources can reuse the frame the supervisor just cached.
        if not is_single_shot and self.is_frame_fresh():
            return self._last_frame
        try:
            async with asyncio.timeout(timeout or float(self._config.upstream_timeout)):
                frame = await self._async_fetch_frame()
        except (UpstreamError, MultipartError, TimeoutError, OSError) as err:
            _LOGGER.debug("%s: snapshot request failed: %s", self._config.name, err)
            return self._last_frame
        if frame:
            self._publish(frame)
        return frame or self._last_frame

    # ---------------------------------------------------------------- internals

    async def _async_run(self) -> None:
        """Supervise the upstream connection, reconnecting as needed."""
        while True:
            frames_before = self._frames
            try:
                poll_mode = await self._async_consume()
            except asyncio.CancelledError:
                self._state = HubState.STOPPED
                raise
            except (UpstreamError, MultipartError, TimeoutError, OSError, ValueError) as err:
                await self._async_handle_failure(err)
                continue

            self._backoff = self._config.reconnect_interval
            if poll_mode:
                await asyncio.sleep(self._config.scan_interval)
                continue
            if self._frames == frames_before:
                self._state = HubState.ERROR
                self._error = "the upstream closed the stream before sending a frame"
                _LOGGER.warning("%s: %s", self._config.name, self._error)
            await asyncio.sleep(self._config.reconnect_interval)

    async def _async_consume(self) -> bool:
        """Consume one connection; return ``True`` for single shot (poll) sources."""
        if self._config.source_type == SOURCE_TYPE_IMAGE:
            await self._async_poll_once()
            return True

        headers = {"Accept": _ACCEPT_HEADER}
        timeout = self._client.timeout(total=None, sock_read=self._config.stall_timeout)
        async with self._client.open(headers=headers, timeout=timeout) as response:
            if response.status >= 400:
                raise UpstreamError(
                    f"upstream returned HTTP {response.status} for {redact_url(self._config.url)}"
                )
            content_type = response.headers.get("Content-Type")
            if content_type and "multipart/" in content_type:
                if self._config.source_type not in (
                    SOURCE_TYPE_AUTO,
                    SOURCE_TYPE_MJPEG,
                    SOURCE_TYPE_IMAGE,
                ):
                    raise UpstreamError(
                        f"source type is configured as '{self._config.source_type}' "
                        "but the upstream serves a multipart stream"
                    )
                parser = MultipartFrameParser(parse_boundary(content_type) or DEFAULT_BOUNDARY)
                self._mark_live()
                async for chunk in response.content.iter_chunked(READ_CHUNK):
                    for frame in parser.feed(chunk):
                        self._publish(frame)
                for frame in parser.flush():
                    self._publish(frame)
                return False

            if content_type and content_type.startswith("image/"):
                data = await response.content.read(MAX_SNAPSHOT_BYTES)
                if not data:
                    raise UpstreamError("upstream returned an empty image")
                self._mark_live()
                self._publish(data)
                return True

            raise UpstreamError(
                "expected a multipart or image stream but the upstream sent "
                f"{content_type or 'no content type'}"
            )

    async def _async_poll_once(self) -> None:
        """Fetch a single image from the configured snapshot URL."""
        url = self._config.snapshot_url or self._config.url
        timeout = self._client.timeout(total=float(self._config.upstream_timeout))
        async with self._client.open(url=url, timeout=timeout) as response:
            if response.status >= 400:
                raise UpstreamError(f"upstream returned HTTP {response.status}")
            data = await response.content.read(MAX_SNAPSHOT_BYTES)
        if not data:
            raise UpstreamError("upstream returned an empty image")
        self._mark_live()
        self._publish(data)

    async def _async_fetch_frame(self) -> bytes | None:
        """Fetch one frame without touching the supervisor."""
        if self._config.source_type == SOURCE_TYPE_IMAGE:
            await self._async_poll_once()
            return self._last_frame

        timeout = self._client.timeout(total=float(self._config.upstream_timeout))
        async with self._client.open(
            headers={"Accept": _ACCEPT_HEADER}, timeout=timeout
        ) as response:
            if response.status >= 400:
                raise UpstreamError(f"upstream returned HTTP {response.status}")
            content_type = response.headers.get("Content-Type")
            if content_type and "multipart/" in content_type:
                parser = MultipartFrameParser(parse_boundary(content_type) or DEFAULT_BOUNDARY)
                async for chunk in response.content.iter_chunked(READ_CHUNK):
                    if frames := parser.feed(chunk):
                        return frames[-1]
                if frames := parser.flush():
                    return frames[-1]
                return None
            data = await response.content.read(MAX_SNAPSHOT_BYTES)
            return data or None

    def _publish(self, frame: bytes) -> None:
        """Publish a frame to every subscriber and the cache."""
        if not frame:
            return
        now = time.monotonic()
        self._last_frame = frame
        self._last_frame_at = now
        if self._connected_at is None:
            self._connected_at = now
        self._frames += 1
        self._bytes += len(frame)
        self._frame_times.append(now)
        self._state = HubState.LIVE
        self._error = None
        for queue in tuple(self._subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(frame)

    def _mark_live(self) -> None:
        self._state = HubState.LIVE
        self._error = None
        if self._connected_at is None:
            self._connected_at = time.monotonic()

    async def _async_handle_failure(self, err: Exception) -> None:
        self._state = HubState.ERROR
        message = str(err) or err.__class__.__name__
        if message != self._error:
            _LOGGER.warning("%s: upstream connection failed: %s", self._config.name, message)
        self._error = message
        delay = min(max(self._config.reconnect_interval, self._backoff), self._config.max_backoff)
        self._backoff = min(delay * 2, self._config.max_backoff)
        _LOGGER.debug("%s: reconnecting in %ss", self._config.name, delay)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self._state = HubState.STOPPED
            raise

    def _schedule_idle_stop(self) -> None:
        if self._idle_task is not None and not self._idle_task.done():
            return
        self._idle_task = self._hass.async_create_background_task(
            self._async_idle_stop(), name=f"generic_video_proxy idle {self._config.name}"
        )

    async def _async_idle_stop(self) -> None:
        await asyncio.sleep(IDLE_STOP_DELAY)
        if self._subscribers:
            return
        _LOGGER.debug(
            "%s: no viewers left, closing the upstream connection",
            self._config.name,
        )
        await self.async_stop()

    def _cancel_idle_task(self) -> None:
        task = self._idle_task
        self._idle_task = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
