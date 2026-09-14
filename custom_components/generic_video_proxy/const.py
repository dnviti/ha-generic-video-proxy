"""Constants for the Generic Video Proxy integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "generic_video_proxy"
NAME: Final = "Generic Video Proxy"
MANUFACTURER: Final = "Generic Video Proxy"
MODEL: Final = "Proxied video stream"
USER_AGENT: Final = "ha-generic-video-proxy"

#: API namespace served by this integration.
API_BASE: Final = "/api/generic_video_proxy"

#: Lovelace card served by this integration.
CARD_FILENAME: Final = "generic-video-proxy-card.js"
CARD_URL_PATH: Final = f"/{DOMAIN}/{CARD_FILENAME}"
HLS_JS_FILENAME: Final = "hls.light.min.js"
HLS_JS_URL_PATH: Final = f"/{DOMAIN}/{HLS_JS_FILENAME}"
WWW_PATH: Final = "www"

PLATFORMS: Final = ["camera"]

# ---------------------------------------------------------------- config keys
CONF_NAME: Final = "name"
CONF_URL: Final = "url"
CONF_SOURCE_TYPE: Final = "source_type"
CONF_SNAPSHOT_URL: Final = "snapshot_url"
CONF_POSTER_URL: Final = "poster_url"
CONF_AUTH_TYPE: Final = "auth_type"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_HEADERS: Final = "headers"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_RECONNECT_INTERVAL: Final = "reconnect_interval"
CONF_UPSTREAM_TIMEOUT: Final = "upstream_timeout"
CONF_FRAME_BUFFER: Final = "frame_buffer"
CONF_STALL_TIMEOUT: Final = "stall_timeout"

# ------------------------------------------------------------- source types
SOURCE_TYPE_AUTO: Final = "auto"
SOURCE_TYPE_MJPEG: Final = "mjpeg"
SOURCE_TYPE_HLS: Final = "hls"
SOURCE_TYPE_PROGRESSIVE: Final = "progressive"
SOURCE_TYPE_IMAGE: Final = "image"
SOURCE_TYPE_RTSP: Final = "rtsp"

SOURCE_TYPES: Final = (
    SOURCE_TYPE_AUTO,
    SOURCE_TYPE_MJPEG,
    SOURCE_TYPE_HLS,
    SOURCE_TYPE_PROGRESSIVE,
    SOURCE_TYPE_IMAGE,
    SOURCE_TYPE_RTSP,
)

#: Source types which are proxied as a multipart MJPEG stream.
MJPEG_SOURCE_TYPES: Final = (SOURCE_TYPE_MJPEG, SOURCE_TYPE_IMAGE)

# --------------------------------------------------------------- auth types
AUTH_TYPE_NONE: Final = "none"
AUTH_TYPE_BASIC: Final = "basic"
AUTH_TYPE_DIGEST: Final = "digest"
AUTH_TYPES: Final = (AUTH_TYPE_NONE, AUTH_TYPE_BASIC, AUTH_TYPE_DIGEST)

# ------------------------------------------------------------------ defaults
DEFAULT_SOURCE_TYPE: Final = SOURCE_TYPE_AUTO
DEFAULT_AUTH_TYPE: Final = AUTH_TYPE_NONE
DEFAULT_VERIFY_SSL: Final = True
DEFAULT_SCAN_INTERVAL: Final = 5
DEFAULT_RECONNECT_INTERVAL: Final = 5
DEFAULT_UPSTREAM_TIMEOUT: Final = 10
DEFAULT_STALL_TIMEOUT: Final = 30
DEFAULT_FRAME_BUFFER: Final = 5

MIN_SCAN_INTERVAL: Final = 1
MAX_SCAN_INTERVAL: Final = 3600
MIN_RECONNECT_INTERVAL: Final = 1
MAX_RECONNECT_INTERVAL: Final = 120
MAX_RECONNECT_BACKOFF: Final = 60
MIN_UPSTREAM_TIMEOUT: Final = 1
MAX_UPSTREAM_TIMEOUT: Final = 300
MIN_FRAME_BUFFER: Final = 1
MAX_FRAME_BUFFER: Final = 50

#: Lifetime of URLs handed to the browser. Kept short lived on purpose: the card
#: refreshes them well before they expire.
SIGNED_URL_TTL: Final = 6 * 60 * 60

# ------------------------------------------------------------------ resource
RESOURCE_STREAM: Final = "stream.mjpeg"
RESOURCE_PLAYLIST: Final = "stream.m3u8"
RESOURCE_SEGMENT: Final = "segment"
RESOURCE_MEDIA: Final = "media"
RESOURCE_SNAPSHOT: Final = "snapshot.jpg"
RESOURCE_POSTER: Final = "poster.jpg"
RESOURCE_STATUS: Final = "status"

#: Query parameter carrying the signed upstream URL.
QUERY_UPSTREAM: Final = "u"

# ------------------------------------------------------------------ websocket
WS_PREFIX: Final = DOMAIN
WS_LIST: Final = f"{WS_PREFIX}/list"
WS_STREAM_URL: Final = f"{WS_PREFIX}/stream_url"

# ---------------------------------------------------------------- hass.data
DATA_RUNTIMES: Final = f"{DOMAIN}_runtimes"
DATA_CARD_REGISTERED: Final = f"{DOMAIN}_card_registered"
DATA_VIEWS_REGISTERED: Final = f"{DOMAIN}_views_registered"
