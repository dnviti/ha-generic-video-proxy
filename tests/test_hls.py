"""Tests for HLS playlist rewriting."""

from __future__ import annotations

from custom_components.generic_video_proxy.hls import (
    is_playlist,
    iter_uris,
    parse_byte_range,
    rewrite_playlist,
)

MASTER = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="main",URI="audio/index.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,AUDIO="audio"
video/low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
https://cdn.example.com/video/high.m3u8
"""

MEDIA = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:7
#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x1234
#EXT-X-MAP:URI="init.mp4"
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T10:00:00.000Z
#EXTINF:4.00000,
segment0.m4s
#EXT-X-BYTERANGE:1024@2048
segment1.m4s
#EXTINF:4.00000,
../shared/segment2.m4s?token=abc
"""


def proxy_url(uri: str, byte_range: str | None = None) -> str:
    """Stand-in for the token minting callback."""
    suffix = f"#range={byte_range}" if byte_range else ""
    return f"/PROXY?u={uri}{suffix}"


class TestIsPlaylist:
    """Playlist detection."""

    def test_master(self) -> None:
        assert is_playlist(MASTER) is True

    def test_not_a_playlist(self) -> None:
        assert is_playlist("<html>hello</html>") is False


class TestParseByteRange:
    """#EXT-X-BYTERANGE translation."""

    def test_with_offset(self) -> None:
        assert parse_byte_range("#EXT-X-BYTERANGE:1024@2048") == "bytes=2048-3071"

    def test_without_offset_is_left_to_the_player(self) -> None:
        assert parse_byte_range("#EXT-X-BYTERANGE:1024") is None

    def test_not_a_byte_range(self) -> None:
        assert parse_byte_range("#EXTINF:4.0,") is None
        assert parse_byte_range("#EXT-X-BYTERANGE:0@0") is None


class TestRewriteMasterPlaylist:
    """Variant playlists and URI attributes."""

    def test_every_uri_is_rewritten(self) -> None:
        result = rewrite_playlist(MASTER, "https://cam.local/hls/master.m3u8", proxy_url)

        assert "/PROXY?u=https://cam.local/hls/video/low.m3u8" in result
        assert "/PROXY?u=https://cam.local/hls/audio/index.m3u8" in result
        # An absolute upstream URI is handed to the proxy as is.
        assert "/PROXY?u=https://cdn.example.com/video/high.m3u8" in result
        # No bare relative URI is left behind for the browser to resolve.
        assert "\nvideo/low.m3u8\n" not in result

    def test_tags_are_preserved(self) -> None:
        result = rewrite_playlist(MASTER, "https://cam.local/hls/master.m3u8", proxy_url)

        assert "#EXTM3U" in result
        assert "#EXT-X-VERSION:6" in result
        assert "#EXT-X-INDEPENDENT-SEGMENTS" in result
        assert '#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,AUDIO="audio"' in result

    def test_trailing_newline_is_kept(self) -> None:
        result = rewrite_playlist(MASTER, "https://cam.local/hls/master.m3u8", proxy_url)
        assert result.endswith("\n")

    def test_playlist_without_trailing_newline(self) -> None:
        result = rewrite_playlist("#EXTM3U\n#EXT-X-ENDLIST", "https://x/y.m3u8", proxy_url)
        assert result == "#EXTM3U\n#EXT-X-ENDLIST"


class TestRewriteMediaPlaylist:
    """Segments, keys, init sections and byte ranges."""

    def test_relative_uris_are_resolved(self) -> None:
        result = rewrite_playlist(MEDIA, "https://cam.local/hls/video/low.m3u8", proxy_url)

        assert "/PROXY?u=https://cam.local/hls/video/segment0.m4s" in result
        assert "/PROXY?u=https://cam.local/hls/video/key.bin" in result
        assert "/PROXY?u=https://cam.local/hls/video/init.mp4" in result
        # ".." is resolved against the playlist directory, query string intact.
        assert "/PROXY?u=https://cam.local/hls/shared/segment2.m4s?token=abc" in result

    def test_byte_range_is_forwarded_to_the_proxy_callback(self) -> None:
        result = rewrite_playlist(MEDIA, "https://cam.local/hls/video/low.m3u8", proxy_url)

        assert "#range=bytes=2048-3071" in result
        # The tag itself is untouched, only its meaning is forwarded.
        assert "#EXT-X-BYTERANGE:1024@2048" in result

    def test_caller_receives_none_when_there_is_no_byte_range(self) -> None:
        seen: list[tuple[str, str | None]] = []

        def recorder(uri: str, byte_range: str | None) -> str:
            seen.append((uri, byte_range))
            return uri

        rewrite_playlist(MEDIA, "https://cam.local/hls/video/low.m3u8", recorder)

        byters = [item for item in seen if item[1] is not None]
        assert byters == [("https://cam.local/hls/video/segment1.m4s", "bytes=2048-3071")]

    def test_program_date_time_is_untouched(self) -> None:
        result = rewrite_playlist(MEDIA, "https://cam.local/hls/video/low.m3u8", proxy_url)
        assert "#EXT-X-PROGRAM-DATE-TIME:2026-01-01T10:00:00.000Z" in result


class TestIterUris:
    """The helper used to inspect playlists in tests and diagnostics."""

    def test_collects_every_uri(self) -> None:
        uris = iter_uris(MASTER, "https://cam.local/hls/master.m3u8")
        assert uris == [
            "https://cam.local/hls/audio/index.m3u8",
            "https://cam.local/hls/video/low.m3u8",
            "https://cdn.example.com/video/high.m3u8",
        ]
