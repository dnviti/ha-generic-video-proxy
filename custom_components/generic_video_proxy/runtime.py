"""Runtime state for one configured proxy."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DATA_RUNTIMES,
    MJPEG_SOURCE_TYPES,
    SOURCE_TYPE_AUTO,
)
from .hub import StreamHub
from .models import ProxyConfig
from .security import TokenManager
from .upstream import (
    MAX_SNAPSHOT_BYTES,
    UpstreamClient,
    UpstreamError,
    async_close_session,
    async_detect_source_type,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ProxyRuntime:
    """Everything the HTTP views, the camera entity and diagnostics need."""

    hass: HomeAssistant
    entry: ConfigEntry
    config: ProxyConfig
    client: UpstreamClient
    hub: StreamHub
    tokens: TokenManager
    session: aiohttp.ClientSession

    @property
    def entry_id(self) -> str:
        """Return the config entry id of this proxy."""
        return self.entry.entry_id

    @property
    def is_stream_source(self) -> bool:
        """Return ``True`` when frames are pushed as a multipart MJPEG stream."""
        return self.config.source_type in MJPEG_SOURCE_TYPES or (
            self.config.source_type == SOURCE_TYPE_AUTO
        )

    async def async_detect_source(self) -> tuple[str, str | None]:
        """Probe the upstream and resolve the concrete source type."""
        return await async_detect_source_type(self.client)

    async def async_snapshot(self) -> bytes | None:
        """Return a still image for the camera entity, if one can be obtained."""
        if self.hub.is_frame_fresh():
            return self.hub.last_frame

        if self.config.snapshot_url is not None:
            try:
                return await self._async_fetch_image(self.config.snapshot_url)
            except UpstreamError as err:
                _LOGGER.debug(
                    "%s: snapshot url failed (%s), falling back to the stream",
                    self.config.name,
                    err,
                )

        if self.is_stream_source:
            return await self.hub.async_refresh_frame()

        if self.config.poster_url is not None:
            try:
                return await self._async_fetch_image(self.config.poster_url)
            except UpstreamError as err:
                _LOGGER.debug("%s: poster url failed: %s", self.config.name, err)

        return self.hub.last_frame

    async def _async_fetch_image(self, url: str) -> bytes:
        body, _headers, _status = await self.client.read_bytes(
            url, limit=MAX_SNAPSHOT_BYTES, what="image"
        )
        if not body:
            raise UpstreamError("upstream returned an empty image")
        return body

    async def async_stop(self) -> None:
        """Stop background work owned by this runtime and close its session."""
        await self.hub.async_stop()
        await async_close_session(self.session)

    def status(self) -> dict[str, object]:
        """Return a serialisable status document for the card and diagnostics."""
        stats = self.hub.stats
        return {
            "entry_id": self.entry_id,
            "name": self.config.name,
            "source_type": self.config.source_type,
            "upstream": self.client.safe_url,
            "state": stats.state,
            "error": stats.error,
            "clients": stats.clients,
            "frames": stats.frames,
            "fps": stats.fps,
            "bytes_received": stats.bytes_received,
            "last_frame_bytes": stats.last_frame_bytes,
            "idle": not self.hub.is_running,
        }


def set_runtime(hass: HomeAssistant, runtime: ProxyRuntime) -> None:
    """Register a runtime so the HTTP views can find it."""
    hass.data.setdefault(DATA_RUNTIMES, {})[runtime.entry_id] = runtime


def get_runtime(hass: HomeAssistant, entry_id: str) -> ProxyRuntime | None:
    """Return the runtime for a config entry, if it is loaded."""
    return hass.data.get(DATA_RUNTIMES, {}).get(entry_id)


def remove_runtime(hass: HomeAssistant, entry_id: str) -> None:
    """Forget a runtime."""
    hass.data.get(DATA_RUNTIMES, {}).pop(entry_id, None)


def iter_runtimes(hass: HomeAssistant) -> list[ProxyRuntime]:
    """Return every loaded runtime."""
    return list(hass.data.get(DATA_RUNTIMES, {}).values())
