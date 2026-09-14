"""Tests for the shared upstream connection hub."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from upstream_server import JPEG_FRAME, UpstreamServer

from custom_components.generic_video_proxy import hub as hub_module
from custom_components.generic_video_proxy.hub import HubState, StreamHub
from custom_components.generic_video_proxy.models import parse_config
from custom_components.generic_video_proxy.upstream import (
    UpstreamClient,
    async_close_session,
    create_session,
)


@dataclass
class HubFactory:
    """Build hubs pointed at the local upstream server."""

    hass: HomeAssistant
    upstream: UpstreamServer
    created: list[StreamHub] = field(default_factory=list)
    sessions: list[aiohttp.ClientSession] = field(default_factory=list)

    def __call__(
        self, path: str = "/mjpeg", *, entry_id: str = "entry-1", **overrides: object
    ) -> StreamHub:
        config = parse_config(
            {
                "name": "cam",
                "url": self.upstream.url(path),
                "source_type": "mjpeg",
                **overrides,
            }
        )
        session = create_session(config.verify_ssl)
        self.sessions.append(session)
        hub = StreamHub(self.hass, entry_id, config, UpstreamClient(config, session))
        self.created.append(hub)
        return hub


@pytest.fixture(name="hubs")
async def hubs_fixture(hass: HomeAssistant, upstream: UpstreamServer) -> AsyncIterator[HubFactory]:
    """Provide a hub factory that always shuts its hubs down."""
    factory = HubFactory(hass=hass, upstream=upstream)
    yield factory
    for hub in factory.created:
        await hub.async_stop()
    for session in factory.sessions:
        await async_close_session(session)


async def take_frames(hub: StreamHub, count: int, timeout: float = 10.0) -> list[bytes]:
    """Collect ``count`` frames from a hub subscription."""
    frames: list[bytes] = []

    async def collect() -> None:
        async with aclosing(hub.async_subscribe()) as subscription:
            async for frame in subscription:
                frames.append(frame)
                if len(frames) >= count:
                    return

    async with asyncio.timeout(timeout):
        await collect()
    return frames


async def wait_until(predicate, timeout: float = 10.0) -> None:
    """Wait until ``predicate`` returns a truthy value."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


class TestStreaming:
    """Frames reach every subscriber."""

    async def test_frames_are_delivered(self, hubs: HubFactory) -> None:
        hub = hubs("/mjpeg?frames=6")

        frames = await take_frames(hub, 3)

        assert len(frames) == 3
        assert all(frame.startswith(b"\xff\xd8") for frame in frames)
        assert hub.last_frame is not None
        assert hub.stats.state == HubState.LIVE.value
        assert hub.stats.frames >= 3
        assert hub.stats.bytes_received > 0

    async def test_auto_source_type_streams_multipart(self, hubs: HubFactory) -> None:
        hub = hubs("/mjpeg?frames=6", source_type="auto")

        frames = await take_frames(hub, 2)

        assert len(frames) == 2
        assert hub.state is HubState.LIVE

    async def test_subscribers_share_one_upstream_connection(
        self, hubs: HubFactory, upstream: UpstreamServer
    ) -> None:
        hub = hubs("/mjpeg-endless")

        first, second = await asyncio.gather(take_frames(hub, 2), take_frames(hub, 2))

        assert len(first) == 2
        assert len(second) == 2
        # The whole point of the hub: one upstream connection, many viewers.
        assert upstream.hits.get("mjpeg-endless") == 1

    async def test_mismatched_boundary_still_parses(self, hubs: HubFactory) -> None:
        # The upstream declares "boundary=--foo" but writes "----foo" in the
        # body, exactly like the reference camera this integration was built for.
        hub = hubs("/mjpeg?frames=4&boundary=--foo")

        frames = await take_frames(hub, 2)

        assert len(frames) == 2

    async def test_lenient_boundary_like_the_reference_camera(self, hubs: HubFactory) -> None:
        # The reference camera declares "boundary=--foo" and writes "--foo".
        hub = hubs("/mjpeg?frames=4&boundary=--foo&body_boundary=--foo")

        frames = await take_frames(hub, 2)

        assert len(frames) == 2

    async def test_fps_is_reported(self, hubs: HubFactory) -> None:
        hub = hubs("/mjpeg-endless")

        await take_frames(hub, 3)

        assert hub.stats.fps > 0


class TestReconnect:
    """The hub survives upstream failures."""

    async def test_reconnects_when_the_stream_ends(
        self, hubs: HubFactory, upstream: UpstreamServer
    ) -> None:
        hub = hubs("/mjpeg?frames=2", reconnect_interval=1)

        await take_frames(hub, 1)
        await wait_until(lambda: upstream.hits.get("mjpeg", 0) >= 2, timeout=5)

        assert upstream.hits["mjpeg"] >= 2

    async def test_error_state_after_a_failure(self, hubs: HubFactory) -> None:
        hub = hubs("/error", reconnect_interval=1)

        await hub.async_start()
        await wait_until(lambda: hub.state is HubState.ERROR, timeout=5)

        assert "500" in (hub.stats.error or "")

    async def test_stall_timeout_detects_a_silent_upstream(self, hubs: HubFactory) -> None:
        hub = hubs("/slow", stall_timeout=1, upstream_timeout=1, reconnect_interval=1)

        await hub.async_start()
        await wait_until(lambda: hub.state is HubState.ERROR, timeout=10)

        assert hub.stats.error

    async def test_wrong_source_type_is_reported(self, hubs: HubFactory) -> None:
        # An HLS playlist served by a source configured as MJPEG.
        hub = hubs("/hls/master.m3u8", reconnect_interval=1)

        await hub.async_start()
        await wait_until(lambda: hub.state is HubState.ERROR, timeout=5)

        assert "multipart or image" in (hub.stats.error or "")


class TestPolledSources:
    """Still image sources are polled and re-published."""

    async def test_refresh_frame_fetches_one_image(self, hubs: HubFactory) -> None:
        hub = hubs("/snapshot.jpg", source_type="image")

        frame = await hub.async_refresh_frame()

        assert frame == JPEG_FRAME
        assert hub.last_frame == JPEG_FRAME

    async def test_supervisor_polls_at_the_scan_interval(
        self, hubs: HubFactory, upstream: UpstreamServer
    ) -> None:
        hub = hubs("/snapshot.jpg", source_type="image", scan_interval=1)

        frames = await take_frames(hub, 2, timeout=10)

        assert len(frames) == 2
        assert upstream.hits.get("snapshot", 0) >= 2

    async def test_cached_frame_is_reused_for_snapshots(self, hubs: HubFactory) -> None:
        hub = hubs("/mjpeg-endless")

        await take_frames(hub, 1)
        frame = await hub.async_refresh_frame()

        assert frame is not None
        assert hub.is_frame_fresh() is True


class TestLifecycle:
    """Starting, idling and stopping."""

    async def test_idle_shutdown_closes_the_upstream(
        self, hubs: HubFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hub_module, "IDLE_STOP_DELAY", 0.05)
        hub = hubs("/mjpeg-endless")

        await take_frames(hub, 1)
        assert hub.is_running is True

        await wait_until(lambda: hub.state is HubState.STOPPED, timeout=5)
        assert hub.is_running is False
        assert hub.subscriber_count == 0

    async def test_start_again_after_idling(self, hubs: HubFactory) -> None:
        hub = hubs("/mjpeg-endless")

        await take_frames(hub, 1)
        await hub.async_stop()
        frames = await take_frames(hub, 1)

        assert len(frames) == 1
        assert hub.is_running is True

    async def test_stop_is_idempotent(self, hubs: HubFactory) -> None:
        hub = hubs("/mjpeg-endless")

        await hub.async_start()
        await hub.async_stop()
        await hub.async_stop()

        assert hub.is_running is False

    async def test_subscribers_are_released(self, hubs: HubFactory) -> None:
        hub = hubs("/mjpeg-endless")

        await take_frames(hub, 1)

        assert hub.subscriber_count == 0
