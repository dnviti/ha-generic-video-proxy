"""Tests for the config and options flows."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from upstream_server import UpstreamServer

from custom_components.generic_video_proxy.config_flow import CONF_VALIDATE
from custom_components.generic_video_proxy.const import DOMAIN, SOURCE_TYPE_MJPEG


async def start_user_flow(hass: HomeAssistant):
    """Open the user step of the config flow."""
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


def user_input(source: UpstreamServer | str, path: str = "/mjpeg", **overrides: object) -> dict:
    """Build a filled in configuration form.

    ``source`` is either the local upstream server (with a path on it) or a raw
    URL to be validated by the flow.
    """
    data = {
        "name": "Test stream",
        "url": source.url(path) if isinstance(source, UpstreamServer) else source,
        "source_type": "auto",
        "auth_type": "none",
        "username": "",
        "password": "",
        "verify_ssl": True,
        "snapshot_url": "",
        "poster_url": "",
        "headers": "",
        "scan_interval": 5,
        "reconnect_interval": 5,
        "upstream_timeout": 10,
        "stall_timeout": 30,
        "frame_buffer": 5,
        CONF_VALIDATE: True,
    }
    data.update(overrides)
    return data


class TestUserFlow:
    """Creating a proxy from the UI."""

    async def test_form_is_shown(self, hass: HomeAssistant) -> None:
        result = await start_user_flow(hass)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["data_schema"] is not None

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/mjpeg", "mjpeg"),
            ("/snapshot.jpg", "image"),
            ("/hls/master.m3u8", "hls"),
            ("/media.mp4", "progressive"),
        ],
    )
    async def test_auto_detection_resolves_the_source_type(
        self, hass: HomeAssistant, upstream: UpstreamServer, path: str, expected: str
    ) -> None:
        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input(upstream, path)
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["source_type"] == expected
        assert result["title"] == "Test stream"

    async def test_explicit_source_type_is_kept(
        self, hass: HomeAssistant, upstream: UpstreamServer
    ) -> None:
        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input(upstream, "/mjpeg", source_type=SOURCE_TYPE_MJPEG),
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["source_type"] == SOURCE_TYPE_MJPEG

    async def test_invalid_url_is_rejected(self, hass: HomeAssistant) -> None:
        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input("not-a-url")
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_config"}

    async def test_unreachable_upstream_is_reported(
        self, hass: HomeAssistant, upstream: UpstreamServer
    ) -> None:
        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input(upstream, "/error")
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_undetectable_upstream_is_reported(
        self, hass: HomeAssistant, upstream: UpstreamServer
    ) -> None:
        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input(upstream, "/teapot")
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_validation_can_be_skipped(
        self, hass: HomeAssistant, upstream: UpstreamServer
    ) -> None:
        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input(upstream, "/error", **{CONF_VALIDATE: False}),
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY

    async def test_rtsp_sources_are_not_probed(self, hass: HomeAssistant) -> None:
        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input("rtsp://10.0.0.5/stream", name="Camera", source_type="rtsp"),
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["source_type"] == "rtsp"

    async def test_duplicate_is_aborted(
        self, hass: HomeAssistant, upstream: UpstreamServer
    ) -> None:
        first = await start_user_flow(hass)
        created = await hass.config_entries.flow.async_configure(
            first["flow_id"], user_input(upstream)
        )
        assert created["type"] is FlowResultType.CREATE_ENTRY

        second = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            second["flow_id"], user_input(upstream)
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"

    async def test_credentials_are_stored(
        self, hass: HomeAssistant, upstream: UpstreamServer
    ) -> None:
        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input(
                upstream,
                "/auth/basic",
                auth_type="basic",
                username="user",
                password="secret",
            ),
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["username"] == "user"
        assert result["data"]["password"] == "secret"


class TestOptionsFlow:
    """Editing an existing proxy."""

    async def test_options_can_be_updated(
        self, hass: HomeAssistant, upstream: UpstreamServer
    ) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Test stream",
            data=user_input(upstream),
            unique_id="test-stream",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

        updated = dict(user_input(upstream))
        updated["scan_interval"] = 30
        updated["name"] = "Renamed stream"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input=updated
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options["scan_interval"] == 30
        assert entry.options["name"] == "Renamed stream"

    async def test_password_is_kept_when_left_empty(
        self, hass: HomeAssistant, upstream: UpstreamServer
    ) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Test stream",
            data=user_input(
                upstream,
                "/auth/basic",
                auth_type="basic",
                username="user",
                password="stored-secret",
            ),
            unique_id="test-stream",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        updated = user_input(
            upstream,
            "/auth/basic",
            auth_type="basic",
            username="user",
            password="",
            **{CONF_VALIDATE: False},
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input=updated
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options["password"] == "stored-secret"

    async def test_probe_uses_the_stored_password(
        self, hass: HomeAssistant, upstream: UpstreamServer
    ) -> None:
        """An empty password field must still validate against the upstream."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Test stream",
            data=user_input(
                upstream,
                "/auth/basic",
                auth_type="basic",
                username="user",
                password="secret",
            ),
            unique_id="test-stream",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        updated = user_input(
            upstream, "/auth/basic", auth_type="basic", username="user", password=""
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input=updated
        )

        # The upstream only accepts "user:secret"; a 401 would surface here as
        # "cannot_connect" instead of a created entry.
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options["password"] == "secret"

    async def test_invalid_options_are_rejected(
        self, hass: HomeAssistant, upstream: UpstreamServer
    ) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Test stream",
            data=user_input(upstream),
            unique_id="test-stream",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input=user_input("http://", name="x")
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_config"}
