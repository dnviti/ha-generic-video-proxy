"""End to end tests of the proxied HTTP endpoints and the camera entity.

These tests go through Home Assistant's real aiohttp server: the config entry is
set up, media URLs are signed exactly like the card would request them, and the
bytes are read back over a socket.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from http import HTTPStatus

import pytest
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from upstream_server import SEGMENT_PAYLOAD, UpstreamServer

from custom_components.generic_video_proxy.const import (
    DOMAIN,
    RESOURCE_MEDIA,
    RESOURCE_PLAYLIST,
    RESOURCE_SEGMENT,
    RESOURCE_SNAPSHOT,
    RESOURCE_STATUS,
    RESOURCE_STREAM,
)
from custom_components.generic_video_proxy.runtime import ProxyRuntime

PROXY_PATH = re.compile(r"/api/generic_video_proxy/[^\s\"']+")


@dataclass
class ProxySetup:
    """Create config entries on demand."""

    hass: HomeAssistant
    upstream: UpstreamServer
    entries: list[MockConfigEntry]

    async def __call__(self, path: str = "/mjpeg", **overrides: object) -> MockConfigEntry:
        data = {
            "name": "Test stream",
            "url": self.upstream.url(path),
            "source_type": "mjpeg",
            "verify_ssl": True,
            **overrides,
        }
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=str(data["name"]),
            data=data,
            unique_id=f"{data['name']}:{data['url']}",
        )
        entry.add_to_hass(self.hass)
        assert await self.hass.config_entries.async_setup(entry.entry_id)
        await self.hass.async_block_till_done()
        self.entries.append(entry)
        return entry

    @property
    def runtime(self) -> ProxyRuntime:
        """Return the runtime of the most recently created entry."""
        return self.entries[-1].runtime_data


@pytest.fixture(name="proxy")
async def proxy_fixture(hass: HomeAssistant, upstream: UpstreamServer) -> AsyncIterator[ProxySetup]:
    """Set up proxies and always tear them down again."""
    setup = ProxySetup(hass=hass, upstream=upstream, entries=[])
    yield setup
    for entry in setup.entries:
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def media_url(runtime: ProxyRuntime, resource: str, **kwargs: object) -> str:
    """Return a signed URL exactly like the card receives it."""
    return runtime.tokens.async_url(entry_id=runtime.entry_id, resource=resource, **kwargs)


def camera_entity_id(hass: HomeAssistant) -> str:
    """Return the entity id of the proxy camera."""
    return next(iter(hass.states.async_entity_ids("camera")))


async def wait_for(predicate, timeout: float = 5.0) -> None:
    """Wait until ``predicate`` returns a truthy value."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


class TestMjpegStream:
    """Multipart streams are re-served by Home Assistant."""

    async def test_stream_is_served_through_home_assistant(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/mjpeg?frames=6")
        client = await hass_client()

        async with client.get(media_url(proxy.runtime, RESOURCE_STREAM)) as response:
            assert response.status == HTTPStatus.OK
            assert response.headers["Content-Type"].startswith(
                "multipart/x-mixed-replace; boundary="
            )
            chunk = await asyncio.wait_for(response.content.read(4096), timeout=10)

        assert b"--frameboundary" in chunk
        assert b"\xff\xd8" in chunk
        # Chrome needs the first frame twice, otherwise it shows nothing.
        assert chunk.count(b"--frameboundary") >= 2

    async def test_stream_uses_the_signed_token(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client_no_auth
    ) -> None:
        await proxy()
        client = await hass_client_no_auth()
        entry_id = proxy.runtime.entry_id

        async with client.get(
            f"/api/generic_video_proxy/{entry_id}/stream.mjpeg/not-a-valid-token"
        ) as response:
            assert response.status == HTTPStatus.FORBIDDEN

        async with client.get(f"/api/generic_video_proxy/{entry_id}/stream.mjpeg") as response:
            assert response.status == HTTPStatus.NOT_FOUND

    async def test_authenticated_request_without_token_is_rejected(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy()
        client = await hass_client()
        entry_id = proxy.runtime.entry_id

        async with client.get(
            f"/api/generic_video_proxy/{entry_id}/snapshot.jpg/bogus"
        ) as response:
            # 401 (not 403) when credentials were offered, so that a stale <img>
            # URL is not counted as a failed login by the IP ban middleware.
            assert response.status == HTTPStatus.UNAUTHORIZED

    async def test_unknown_entry_is_not_found(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy()
        client = await hass_client()
        url = proxy.runtime.tokens.async_url(entry_id="does-not-exist", resource=RESOURCE_STREAM)

        async with client.get(url) as response:
            assert response.status == HTTPStatus.NOT_FOUND

    async def test_snapshot_endpoint_returns_the_current_frame(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/mjpeg?frames=6")
        client = await hass_client()

        async with client.get(media_url(proxy.runtime, RESOURCE_STREAM)) as stream:
            await asyncio.wait_for(stream.content.read(4096), timeout=10)

        async with client.get(media_url(proxy.runtime, RESOURCE_SNAPSHOT)) as response:
            assert response.status == HTTPStatus.OK
            assert response.headers["Content-Type"] == "image/jpeg"
            body = await response.read()

        assert body.startswith(b"\xff\xd8")

    async def test_status_endpoint_reports_health(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/mjpeg?frames=6")
        client = await hass_client()

        async with client.get(media_url(proxy.runtime, RESOURCE_STREAM)) as stream:
            await asyncio.wait_for(stream.content.read(4096), timeout=10)

        async with client.get(media_url(proxy.runtime, RESOURCE_STATUS)) as response:
            assert response.status == HTTPStatus.OK
            payload = await response.json()

        assert payload["name"] == "Test stream"
        assert payload["state"] == "live"
        assert payload["frames"] >= 1
        # The upstream URL is reported without credentials or query string.
        assert payload["upstream"].endswith("/mjpeg")

    async def test_viewer_disconnects_release_the_hub(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/mjpeg-endless")
        client = await hass_client()

        async with client.get(media_url(proxy.runtime, RESOURCE_STREAM)) as response:
            await asyncio.wait_for(response.content.read(4096), timeout=10)

        await wait_for(lambda: proxy.runtime.hub.subscriber_count == 0)
        assert proxy.runtime.hub.subscriber_count == 0


class TestHls:
    """HLS playlists and segments are proxied and rewritten."""

    async def test_playlist_and_segments_are_proxied(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/hls/master.m3u8", source_type="hls")
        client = await hass_client()

        async with client.get(media_url(proxy.runtime, RESOURCE_PLAYLIST)) as response:
            assert response.status == HTTPStatus.OK
            assert response.headers["Content-Type"].startswith("application/vnd.apple.mpegurl")
            master = await response.text()

        assert master.startswith("#EXTM3U")
        assert "#EXT-X-STREAM-INF" in master
        # Every URI now points back at this integration.
        assert "video/low.m3u8\n" not in master
        assert len(PROXY_PATH.findall(master)) == 3

        media_playlist_url = PROXY_PATH.findall(master)[1]
        async with client.get(media_playlist_url) as response:
            assert response.status == HTTPStatus.OK
            media = await response.text()

        assert "#EXT-X-PROGRAM-DATE-TIME:2026-01-01T10:00:00.000Z" in media
        assert "#EXT-X-BYTERANGE:1024@2048" in media
        segment_urls = PROXY_PATH.findall(media)
        assert segment_urls
        # The byterange following "segment1.m4s" travels inside the token.
        assert any("bytes=2048-3071" in url for url in segment_urls) is False

        async with client.get(segment_urls[0]) as response:
            assert response.status == HTTPStatus.OK
            assert await response.read() == SEGMENT_PAYLOAD

    async def test_byte_range_from_the_playlist_is_applied(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/hls/master.m3u8", source_type="hls")
        client = await hass_client()
        runtime = proxy.runtime

        # A segment whose token carries a byte range: the proxy must forward it
        # upstream even when the player does not send a Range header itself.
        url = media_url(
            runtime,
            RESOURCE_SEGMENT,
            upstream=proxy.upstream.url("/media.mp4"),
            byte_range="bytes=10-19",
        )
        async with client.get(url) as response:
            assert response.status == HTTPStatus.PARTIAL_CONTENT
            body = await response.read()

        assert body == proxy.upstream.media_bytes[10:20]

    async def test_non_playlist_response_is_reported(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/teapot", source_type="hls")
        client = await hass_client()

        async with client.get(media_url(proxy.runtime, RESOURCE_PLAYLIST)) as response:
            assert response.status == HTTPStatus.BAD_GATEWAY
            payload = await response.json()

        assert "HLS playlist" in payload["error"]


class TestProgressiveMedia:
    """Progressive video is proxied with byte range support."""

    async def test_full_download(self, hass: HomeAssistant, proxy: ProxySetup, hass_client) -> None:
        await proxy("/media.mp4", source_type="progressive")
        client = await hass_client()

        async with client.get(media_url(proxy.runtime, RESOURCE_MEDIA)) as response:
            assert response.status == HTTPStatus.OK
            body = await response.read()

        assert body == proxy.upstream.media_bytes

    async def test_range_request_returns_partial_content(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/media.mp4", source_type="progressive")
        client = await hass_client()

        async with client.get(
            media_url(proxy.runtime, RESOURCE_MEDIA), headers={"Range": "bytes=0-99"}
        ) as response:
            assert response.status == HTTPStatus.PARTIAL_CONTENT
            assert (
                response.headers["Content-Range"] == f"bytes 0-99/{len(proxy.upstream.media_bytes)}"
            )
            assert response.headers["Accept-Ranges"] == "bytes"
            body = await response.read()

        assert body == proxy.upstream.media_bytes[:100]

    async def test_upstream_error_becomes_bad_gateway(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/error", source_type="progressive")
        client = await hass_client()

        async with client.get(media_url(proxy.runtime, RESOURCE_MEDIA)) as response:
            assert response.status == HTTPStatus.BAD_GATEWAY


class TestCameraEntity:
    """The camera entity makes the proxy usable by Home Assistant itself."""

    async def test_entity_and_still_image(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/mjpeg?frames=6")
        entity_id = camera_entity_id(hass)

        assert hass.states.get(entity_id) is not None

        client = await hass_client()
        async with client.get(f"/api/camera_proxy/{entity_id}") as response:
            assert response.status == HTTPStatus.OK
            assert response.headers["Content-Type"] == "image/jpeg"
            body = await response.read()

        assert body.startswith(b"\xff\xd8")

    async def test_camera_proxy_accepts_the_rotating_token(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client_no_auth
    ) -> None:
        await proxy("/mjpeg?frames=6")
        entity_id = camera_entity_id(hass)
        token = hass.states.get(entity_id).attributes["access_token"]

        client = await hass_client_no_auth()
        async with client.get(f"/api/camera_proxy/{entity_id}?token={token}") as response:
            assert response.status == HTTPStatus.OK

    async def test_mjpeg_camera_proxy_stream(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        await proxy("/mjpeg-endless")
        entity_id = camera_entity_id(hass)
        token = hass.states.get(entity_id).attributes["access_token"]

        client = await hass_client()
        async with client.get(f"/api/camera_proxy_stream/{entity_id}?token={token}") as response:
            assert response.status == HTTPStatus.OK
            assert response.headers["Content-Type"].startswith("multipart/x-mixed-replace")
            chunk = await asyncio.wait_for(response.content.read(4096), timeout=10)

        assert b"\xff\xd8" in chunk

    async def test_hls_source_declares_the_stream_feature(
        self, hass: HomeAssistant, proxy: ProxySetup
    ) -> None:
        await proxy("/hls/master.m3u8", source_type="hls")
        state = hass.states.get(camera_entity_id(hass))

        assert state.attributes["supported_features"] & CameraEntityFeature.STREAM


class TestBundledFrontend:
    """The integration serves its own card and video player."""

    async def test_card_module_is_served(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client_no_auth
    ) -> None:
        await proxy()
        client = await hass_client_no_auth()

        async with client.get("/generic_video_proxy/generic-video-proxy-card.js") as response:
            assert response.status == HTTPStatus.OK
            body = await response.text()

        assert "customElements.define(CARD_TAG" in body
        assert "generic_video_proxy/stream_url" in body
        assert "generic-video-proxy-card-editor" in body

    async def test_bundled_hls_player_is_served(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client_no_auth
    ) -> None:
        await proxy()
        client = await hass_client_no_auth()

        async with client.get("/generic_video_proxy/hls.light.min.js") as response:
            assert response.status == HTTPStatus.OK
            assert int(response.headers["Content-Length"]) > 100_000
            head = await response.content.read(64)

        assert b"function" in head


class TestLifecycleAndDiagnostics:
    """Setup, teardown and diagnostics."""

    async def test_unload_stops_the_hub_and_the_views(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_client
    ) -> None:
        entry = await proxy("/mjpeg-endless")
        runtime = entry.runtime_data
        url = media_url(runtime, RESOURCE_STREAM)

        client = await hass_client()
        async with client.get(url) as response:
            await asyncio.wait_for(response.content.read(4096), timeout=10)

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert runtime.hub.is_running is False

        async with client.get(url) as response:
            assert response.status == HTTPStatus.NOT_FOUND

    async def test_websocket_reports_signed_urls(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_ws_client
    ) -> None:
        entry = await proxy("/mjpeg?frames=6")
        client = await hass_ws_client(hass)

        await client.send_json({"id": 5, "type": "generic_video_proxy/list"})
        listed = await client.receive_json()
        assert listed["success"] is True
        assert listed["result"]["entries"][0]["entry_id"] == entry.entry_id

        entity_id = listed["result"]["entries"][0]["entity_id"]
        await client.send_json(
            {
                "id": 6,
                "type": "generic_video_proxy/stream_url",
                "entity_id": entity_id,
            }
        )
        payload = await client.receive_json()

        assert payload["success"] is True
        result = payload["result"]
        assert result["player"] == "mjpeg"
        assert result["stream_url"].startswith(f"/api/generic_video_proxy/{entry.entry_id}/")
        assert result["snapshot_url"] is not None
        assert result["playlist_url"] is None
        assert result["hls_js_url"].endswith("hls.light.min.js")

    async def test_websocket_rejects_unknown_targets(
        self, hass: HomeAssistant, proxy: ProxySetup, hass_ws_client
    ) -> None:
        await proxy()
        client = await hass_ws_client(hass)

        await client.send_json(
            {"id": 7, "type": "generic_video_proxy/stream_url", "entry_id": "nope"}
        )
        payload = await client.receive_json()

        assert payload["success"] is False
        assert payload["error"]["code"] == "not_found"

    async def test_diagnostics_redact_secrets(self, hass: HomeAssistant, proxy: ProxySetup) -> None:
        from custom_components.generic_video_proxy.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = await proxy(
            "/auth/basic",
            auth_type="basic",
            username="camera-user",
            password="s3cret-password",
            headers="X-Api-Key: token-value",
        )

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        dumped = str(diagnostics)

        assert "s3cret-password" not in dumped
        assert "token-value" not in dumped
        assert "camera-user" not in dumped
        assert diagnostics["config"]["name"] == "Test stream"
        assert diagnostics["stream"]["state"] in {
            "idle",
            "connecting",
            "live",
            "error",
            "stopped",
        }
        assert diagnostics["upstream"] == proxy.upstream.url("/auth/basic")
