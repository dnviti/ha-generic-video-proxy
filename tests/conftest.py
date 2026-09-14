"""Shared fixtures for the Generic Video Proxy test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import aiohttp
import pytest
import pytest_socket
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from upstream_server import (
    JPEG_FRAME,
    MASTER_PLAYLIST,
    MEDIA_PLAYLIST,
    SEGMENT_PAYLOAD,
    UpstreamServer,
    build_app,
    create_media_file,
)

from custom_components.generic_video_proxy.const import DOMAIN

__all__ = [
    "JPEG_FRAME",
    "MASTER_PLAYLIST",
    "MEDIA_PLAYLIST",
    "SEGMENT_PAYLOAD",
    "UpstreamServer",
]

pytest_plugins = ["pytest_homeassistant_custom_component"]

# aiohttp picks aiodns as its resolver whenever it is installed. aiodns refuses
# to run on the Windows proactor event loop used by the Home Assistant test
# plugin ("aiodns needs a SelectorEventLoop on Windows"), and its c-ares channels
# start a background thread on destruction that outlives the test and trips the
# plugin's thread leak check. The threaded resolver has neither problem and is
# only used by the test suite; production keeps the aiohttp default.
_aiohttp_connector_init = aiohttp.TCPConnector.__init__


def _threaded_resolver_connector_init(self, *args: object, **kwargs: object) -> None:
    kwargs.setdefault("resolver", aiohttp.ThreadedResolver())
    _aiohttp_connector_init(self, *args, **kwargs)


aiohttp.TCPConnector.__init__ = _threaded_resolver_connector_init


@pytest.hookimpl(tryfirst=True)
def pytest_fixture_setup() -> None:
    """Lift the socket block the Home Assistant plugin installs for every test.

    ``pytest_homeassistant_custom_component`` disables ``socket.socket`` for each
    test and only allows AF_UNIX sockets. This integration is a byte level proxy,
    so its tests talk to a real upstream HTTP server on 127.0.0.1 over TCP - and
    on Windows asyncio needs an AF_INET ``socketpair`` just to build an event
    loop, which is what the pytest-asyncio ``event_loop`` fixture does. Enabling
    sockets right before fixture creation is therefore the earliest reliable
    point; the plugin's own hook has already run by then.
    """
    pytest_socket.enable_socket()


@pytest.fixture(name="upstream")
async def upstream_fixture(aiohttp_server) -> AsyncIterator[UpstreamServer]:
    """Run the local upstream server used by the proxy tests."""
    hits: dict[str, int] = {}
    _descriptor, media_path = create_media_file()
    app = build_app(hits, media_path)
    server = await aiohttp_server(app)
    media_bytes = Path(media_path).read_bytes()

    try:
        yield UpstreamServer(
            session_url=f"http://127.0.0.1:{server.port}",
            server=server,
            media_bytes=media_bytes,
            hits=hits,
        )
    finally:
        Path(media_path).unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Iterator[None]:
    """Make the custom integration discoverable by Home Assistant."""
    yield


@pytest.fixture(name="config_entry")
async def config_entry_fixture(hass: HomeAssistant, upstream: UpstreamServer) -> MockConfigEntry:
    """Return an unloaded config entry pointing at the test upstream."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test stream",
        data={
            "name": "Test stream",
            "url": upstream.url("/mjpeg"),
            "source_type": "mjpeg",
            "verify_ssl": True,
        },
        unique_id="test-stream",
    )
    entry.add_to_hass(hass)
    return entry
