"""Tests for configuration parsing and validation."""

from __future__ import annotations

import pytest

from custom_components.generic_video_proxy.models import (
    ConfigError,
    ProxyConfig,
    parse_config,
    parse_headers,
    validate_url,
)

MINIMAL = {"name": "Front door", "url": "http://cam.local:8080/video"}


class TestDefaults:
    """A minimal configuration must be usable as is."""

    def test_minimal(self) -> None:
        config = parse_config(MINIMAL)

        assert config.name == "Front door"
        assert config.url == "http://cam.local:8080/video"
        assert config.source_type == "auto"
        assert config.auth_type == "none"
        assert config.verify_ssl is True
        assert config.headers == {}
        assert config.host == "cam.local"
        assert config.uses_credentials is False
        assert config.scan_interval == 5
        assert config.frame_buffer == 5

    def test_url_is_trimmed(self) -> None:
        config = parse_config({"name": "x", "url": "  http://cam.local/video  "})
        assert config.url == "http://cam.local/video"

    def test_verify_ssl_from_string(self) -> None:
        assert parse_config({**MINIMAL, "verify_ssl": "false"}).verify_ssl is False
        assert parse_config({**MINIMAL, "verify_ssl": "true"}).verify_ssl is True


class TestValidationErrors:
    """Invalid input must be rejected with a helpful error."""

    @pytest.mark.parametrize(
        "data",
        [
            {"url": "http://cam.local/video"},
            {"name": "", "url": "http://cam.local/video"},
            {"name": "x"},
            {"name": "x", "url": ""},
            {"name": "x", "url": "not-a-url"},
            {"name": "x", "url": "ftp://cam.local/video"},
            {"name": "x", "url": "file:///etc/passwd"},
        ],
    )
    def test_invalid(self, data: dict) -> None:
        with pytest.raises(ConfigError):
            parse_config(data)

    def test_unknown_source_type(self) -> None:
        with pytest.raises(ConfigError):
            parse_config({**MINIMAL, "source_type": "webrtc"})

    def test_unknown_auth_type(self) -> None:
        with pytest.raises(ConfigError):
            parse_config({**MINIMAL, "auth_type": "bearer"})

    def test_basic_auth_requires_username(self) -> None:
        with pytest.raises(ConfigError):
            parse_config({**MINIMAL, "auth_type": "basic"})

    def test_numeric_range_is_enforced(self) -> None:
        with pytest.raises(ConfigError):
            parse_config({**MINIMAL, "scan_interval": 0})
        with pytest.raises(ConfigError):
            parse_config({**MINIMAL, "scan_interval": 99999})
        with pytest.raises(ConfigError):
            parse_config({**MINIMAL, "frame_buffer": "many"})

    def test_snapshot_url_is_validated(self) -> None:
        with pytest.raises(ConfigError):
            parse_config({**MINIMAL, "snapshot_url": "javscript:alert(1)"})


class TestSourceTypes:
    """RTSP and image sources have their own rules."""

    def test_rtsp_url(self) -> None:
        config = parse_config(
            {"name": "cam", "url": "rtsp://cam.local/stream", "source_type": "rtsp"}
        )
        assert config.source_type == "rtsp"
        assert config.is_rtsp is True

    def test_rtsp_scheme_needs_rtsp_source_type(self) -> None:
        with pytest.raises(ConfigError):
            parse_config({"name": "cam", "url": "rtsp://cam.local/stream"})

    def test_image_source(self) -> None:
        config = parse_config({**MINIMAL, "source_type": "image"})
        assert config.source_type == "image"
        assert config.is_rtsp is False


class TestHeaders:
    """Custom headers are parsed from a text block or a mapping."""

    def test_text_block(self) -> None:
        headers = parse_headers("X-Api-Key: secret\nReferer: http://x/\n")
        assert headers == {"X-Api-Key": "secret", "Referer": "http://x/"}

    def test_comments_and_blank_lines(self) -> None:
        headers = parse_headers("# comment\n\nA: 1\n")
        assert headers == {"A": "1"}

    def test_mapping(self) -> None:
        assert parse_headers({"A": 1}) == {"A": "1"}

    def test_empty(self) -> None:
        assert parse_headers(None) == {}
        assert parse_headers("") == {}

    def test_invalid_line(self) -> None:
        with pytest.raises(ConfigError):
            parse_headers("not a header line")

    def test_round_trip_through_config(self) -> None:
        config = parse_config({**MINIMAL, "headers": "A: 1\nB: 2"})
        assert config.headers == {"A": "1", "B": "2"}
        assert config.as_dict()["headers"] == {"A": "1", "B": "2"}


class TestSecrets:
    """Credentials are kept, but never leaked by default."""

    def test_password_is_kept_for_the_runtime(self) -> None:
        config = parse_config({**MINIMAL, "auth_type": "digest", "username": "u", "password": "p"})
        assert config.uses_credentials is True
        assert config.as_dict(include_secrets=True)["password"] == "p"

    def test_password_is_absent_from_a_plain_dump(self) -> None:
        config = parse_config({**MINIMAL, "auth_type": "basic", "username": "u", "password": "p"})
        dumped = config.as_dict()
        assert "password" not in dumped
        assert "username" not in dumped

    def test_existing_password_is_kept_when_editing(self) -> None:
        existing = parse_config(
            {**MINIMAL, "auth_type": "basic", "username": "u", "password": "stored"}
        )
        updated = parse_config(
            {**MINIMAL, "auth_type": "basic", "username": "u", "password": ""},
            existing=existing,
        )
        assert updated.password == "stored"

    def test_password_can_be_replaced_when_editing(self) -> None:
        existing = parse_config(
            {**MINIMAL, "auth_type": "basic", "username": "u", "password": "stored"}
        )
        updated = parse_config(
            {**MINIMAL, "auth_type": "basic", "username": "u", "password": "new"},
            existing=existing,
        )
        assert updated.password == "new"


class TestOverrides:
    """The options flow merges changes onto an existing configuration."""

    def test_with_overrides(self) -> None:
        config = parse_config({**MINIMAL, "auth_type": "basic", "username": "u", "password": "p"})
        updated = config.with_overrides({"scan_interval": 30})

        assert updated.scan_interval == 30
        assert updated.password == "p"
        assert isinstance(updated, ProxyConfig)


class TestValidateUrl:
    """URL validation helper."""

    def test_allows_rtsp_when_asked(self) -> None:
        assert validate_url("rtsp://x/y", schemes=("rtsp", "rtsps")) == "rtsp://x/y"

    def test_rejects_missing_scheme(self) -> None:
        with pytest.raises(ConfigError):
            validate_url("//host/path")
