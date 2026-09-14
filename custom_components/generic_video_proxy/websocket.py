"""Websocket API consumed by the bundled Lovelace card.

The card never guesses URLs: it asks for them over the authenticated websocket
API and receives freshly signed, relative media URLs. Signed URLs are why the
browser can load media with a plain ``<img>``/``<video>`` element, which cannot
send an ``Authorization`` header.
"""

from __future__ import annotations

import time

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    HLS_JS_URL_PATH,
    RESOURCE_MEDIA,
    RESOURCE_PLAYLIST,
    RESOURCE_POSTER,
    RESOURCE_SNAPSHOT,
    RESOURCE_STATUS,
    RESOURCE_STREAM,
    SIGNED_URL_TTL,
    SOURCE_TYPE_HLS,
    SOURCE_TYPE_IMAGE,
    SOURCE_TYPE_MJPEG,
    SOURCE_TYPE_PROGRESSIVE,
    SOURCE_TYPE_RTSP,
    WS_LIST,
    WS_STREAM_URL,
)
from .runtime import ProxyRuntime, get_runtime, iter_runtimes

#: How the card should render this stream.
PLAYER_MJPEG = "mjpeg"
PLAYER_HLS = "hls"
PLAYER_VIDEO = "video"
PLAYER_NATIVE = "native"

PLAYER_BY_SOURCE_TYPE = {
    SOURCE_TYPE_MJPEG: PLAYER_MJPEG,
    SOURCE_TYPE_IMAGE: PLAYER_MJPEG,
    SOURCE_TYPE_HLS: PLAYER_HLS,
    SOURCE_TYPE_PROGRESSIVE: PLAYER_VIDEO,
    SOURCE_TYPE_RTSP: PLAYER_NATIVE,
}


def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register the websocket commands used by the card."""
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_stream_url)


def describe(runtime: ProxyRuntime) -> dict[str, object]:
    """Return everything the card needs to play a proxied stream."""
    entry_id = runtime.entry_id
    config = runtime.config
    tokens = runtime.tokens
    player = PLAYER_BY_SOURCE_TYPE.get(config.source_type, PLAYER_MJPEG)

    def url(resource: str) -> str:
        return tokens.async_url(entry_id=entry_id, resource=resource)

    return {
        "entry_id": entry_id,
        "name": config.name,
        "source_type": config.source_type,
        "player": player,
        "entity_id": _camera_entity_id(runtime),
        "stream_url": url(RESOURCE_STREAM) if player == PLAYER_MJPEG else None,
        "playlist_url": url(RESOURCE_PLAYLIST) if player == PLAYER_HLS else None,
        "media_url": url(RESOURCE_MEDIA) if player == PLAYER_VIDEO else None,
        "snapshot_url": url(RESOURCE_SNAPSHOT),
        "status_url": url(RESOURCE_STATUS),
        "poster_url": url(RESOURCE_POSTER) if config.poster_url else None,
        "hls_js_url": HLS_JS_URL_PATH,
        "expires_in": SIGNED_URL_TTL,
        "issued_at": int(time.time()),
    }


def _camera_entity_id(runtime: ProxyRuntime) -> str | None:
    """Return the camera entity id belonging to this proxy, if it exists."""
    registry = er.async_get(runtime.hass)
    return registry.async_get_entity_id("camera", DOMAIN, f"{runtime.entry_id}_camera")


@websocket_api.websocket_command({vol.Required("type"): WS_LIST})
@websocket_api.async_response
async def ws_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return every configured proxy."""
    connection.send_result(
        msg["id"],
        {
            "entries": [
                {
                    "entry_id": runtime.entry_id,
                    "name": runtime.config.name,
                    "source_type": runtime.config.source_type,
                    "entity_id": _camera_entity_id(runtime),
                }
                for runtime in iter_runtimes(hass)
            ]
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_STREAM_URL,
        vol.Optional("entry_id"): cv.string,
        vol.Optional("entity_id"): cv.entity_id,
    }
)
@websocket_api.async_response
async def ws_stream_url(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return signed media URLs for one proxy."""
    entry_id = msg.get("entry_id")
    if not entry_id and (entity_id := msg.get("entity_id")):
        registry = er.async_get(hass)
        entity = registry.async_get(entity_id)
        entry_id = entity.config_entry_id if entity else None

    if not entry_id or (runtime := get_runtime(hass, entry_id)) is None:
        connection.send_error(
            msg["id"], "not_found", "No generic video proxy is configured for this target"
        )
        return

    connection.send_result(msg["id"], describe(runtime))
