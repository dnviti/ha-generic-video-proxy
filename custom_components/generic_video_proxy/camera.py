"""Camera entity exposing every proxy to Home Assistant itself.

The dedicated card talks to the integration's own endpoints, but a real camera
entity is what makes a proxied stream usable everywhere else: dashboards,
automations, snapshots, ``camera.turn_on``-style services and third party cards.
It also means Home Assistant's built-in camera proxy (``/api/camera_proxy`` and
``/api/camera_proxy_stream``) serves the stream through Home Assistant, with the
same HTTPS/remote benefits.
"""

from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

try:  # Home Assistant 2025.2 and newer
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
except ImportError:  # pragma: no cover - Home Assistant 2025.1
    from homeassistant.helpers.entity_platform import (
        AddEntitiesCallback as AddConfigEntryEntitiesCallback,
    )

from .const import DOMAIN, MANUFACTURER, MODEL, SOURCE_TYPE_HLS, SOURCE_TYPE_RTSP
from .hub import HubState
from .runtime import ProxyRuntime

_LOGGER = logging.getLogger(__name__)

#: Source types that Home Assistant's own stream component (ffmpeg) can handle.
NATIVE_STREAM_SOURCE_TYPES = (SOURCE_TYPE_HLS, SOURCE_TYPE_RTSP)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the camera entity for a proxy."""
    async_add_entities([GenericVideoProxyCamera(entry.runtime_data)])


class GenericVideoProxyCamera(Camera):
    """A camera entity serving frames proxied by this integration."""

    _attr_brand = MANUFACTURER
    _attr_model = MODEL
    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(self, runtime: ProxyRuntime) -> None:
        """Initialise the entity from its runtime."""
        super().__init__()
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry_id}_camera"
        self._attr_name = runtime.config.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry_id)},
            name=runtime.config.name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=runtime.client.safe_url,
        )
        if runtime.config.source_type in NATIVE_STREAM_SOURCE_TYPES:
            self._attr_supported_features = CameraEntityFeature.STREAM

    @property
    def available(self) -> bool:
        """Return ``False`` when the upstream is known to be unreachable."""
        if not super().available:
            return False
        hub = self._runtime.hub
        return not (hub.state is HubState.ERROR and hub.last_frame is None)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the proxied stream."""
        image = await self._runtime.async_snapshot()
        if image is None:
            _LOGGER.debug(
                "%s: no still image available (configure a snapshot URL for this source)",
                self._runtime.config.name,
            )
        return image

    async def handle_async_mjpeg_stream(self, request: web.Request) -> web.StreamResponse | None:
        """Serve Home Assistant's MJPEG proxy from the shared upstream stream."""
        if self._runtime.is_stream_source:
            # Imported lazily to keep the camera platform importable on its own.
            from .views import async_proxy_mjpeg_stream

            return await async_proxy_mjpeg_stream(request, self._runtime)
        return await super().handle_async_mjpeg_stream(request)

    async def stream_source(self) -> str | None:
        """Return a stream URL for Home Assistant's own stream component.

        Only HLS and RTSP sources are handed over: Home Assistant runs ffmpeg on
        its own host, which can reach the upstream directly. Everything else is
        served by this integration's own proxy views.
        """
        if self._runtime.config.source_type in NATIVE_STREAM_SOURCE_TYPES:
            return self._runtime.config.url
        return None
