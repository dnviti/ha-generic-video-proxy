"""A local upstream media server used by the proxy tests.

Real sockets, real multipart framing, real HLS playlists and real HTTP byte
ranges: proxying is byte level work, so the tests exercise the wire format
rather than mocks.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

#: A minimal but structurally valid JPEG frame (SOI ... EOI).
JPEG_FRAME = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

MASTER_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="main",URI="audio/index.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,AUDIO="audio"
video/low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
https://cdn.example.com/video/high.m3u8
"""

MEDIA_PLAYLIST = """#EXTM3U
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

SEGMENT_PAYLOAD = b"segment-payload" * 8


@dataclass
class UpstreamServer:
    """Handle to the local upstream test server."""

    session_url: str
    server: web.Server
    media_bytes: bytes
    hits: dict[str, int] = field(default_factory=dict)

    def url(self, path: str) -> str:
        """Return an absolute URL for a path on the test server."""
        return f"{self.session_url}{path}"

    @property
    def port(self) -> int:
        """Return the port the test server listens on."""
        return urlsplit(self.session_url).port or 0


async def _handle_mjpeg(request: web.Request) -> web.StreamResponse:
    """Serve a multipart MJPEG stream that ends after a few frames."""
    frames = int(request.query.get("frames", "5"))
    declared_boundary = request.query.get("boundary", "--foo")
    #: Reproduces sloppy producers whose body boundary differs from the header.
    body_boundary = request.query.get("body_boundary", declared_boundary)
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": f"multipart/x-mixed-replace; boundary={declared_boundary}",
            "Cache-Control": "no-cache",
        },
    )
    await response.prepare(request)
    request.app["hits"]["mjpeg"] = request.app["hits"].get("mjpeg", 0) + 1
    for index in range(frames):
        frame = JPEG_FRAME + bytes([index]) * (index + 1)
        await response.write(
            b"--"
            + body_boundary.encode()
            + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(frame)).encode()
            + b"\r\n\r\n"
            + frame
            + b"\r\n"
        )
        await asyncio.sleep(0.01)
    return response


async def _handle_endless_mjpeg(request: web.Request) -> web.StreamResponse:
    """Serve MJPEG until the client goes away."""
    response = web.StreamResponse(
        status=200,
        headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"},
    )
    await response.prepare(request)
    request.app["hits"]["mjpeg-endless"] = request.app["hits"].get("mjpeg-endless", 0) + 1
    try:
        while True:
            await response.write(
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(JPEG_FRAME)).encode()
                + b"\r\n\r\n"
                + JPEG_FRAME
                + b"\r\n"
            )
            await asyncio.sleep(0.02)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return response


async def _handle_snapshot(request: web.Request) -> web.Response:
    """Serve a single JPEG image."""
    request.app["hits"]["snapshot"] = request.app["hits"].get("snapshot", 0) + 1
    return web.Response(body=JPEG_FRAME, content_type="image/jpeg")


async def _handle_master(request: web.Request) -> web.Response:
    """Serve an HLS master playlist."""
    return web.Response(text=MASTER_PLAYLIST, content_type="application/vnd.apple.mpegurl")


async def _handle_media_playlist(request: web.Request) -> web.Response:
    """Serve an HLS media playlist."""
    return web.Response(text=MEDIA_PLAYLIST, content_type="application/vnd.apple.mpegurl")


async def _handle_bytes(request: web.Request) -> web.Response:
    """Serve small binary payloads (segments, keys, init sections)."""
    return web.Response(body=SEGMENT_PAYLOAD, content_type="video/mp4")


async def _handle_media_file(request: web.Request) -> web.StreamResponse:
    """Serve a range-capable media file."""
    return web.FileResponse(Path(request.app["media_file"]))


async def _handle_basic(request: web.Request) -> web.Response:
    """Require HTTP basic authentication."""
    if request.headers.get("Authorization") != "Basic dXNlcjpzZWNyZXQ=":
        return web.Response(
            status=401, headers={"WWW-Authenticate": 'Basic realm="upstream"'}, text="nope"
        )
    return web.Response(body=JPEG_FRAME, content_type="image/jpeg")


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


async def _handle_digest(request: web.Request) -> web.Response:
    """Require HTTP digest authentication (verified independently here)."""
    challenge = (
        'Digest realm="cam", qop="auth", nonce="abc123nonce", opaque="opaque-value", algorithm=MD5'
    )
    header = request.headers.get("Authorization", "")
    if not header.startswith("Digest "):
        return web.Response(status=401, headers={"WWW-Authenticate": challenge}, text="auth")

    params: dict[str, str] = {}
    for part in header[len("Digest ") :].split(","):
        key, _, value = part.partition("=")
        params[key.strip()] = value.strip().strip('"')

    ha1 = _md5("user:cam:secret")
    ha2 = _md5(f"GET:{params.get('uri', '')}")
    expected = _md5(
        f"{ha1}:abc123nonce:{params.get('nc', '')}:{params.get('cnonce', '')}:auth:{ha2}"
    )
    if params.get("response") != expected:
        return web.Response(status=401, headers={"WWW-Authenticate": challenge}, text="bad digest")
    return web.Response(body=JPEG_FRAME, content_type="image/jpeg")


async def _handle_redirect(request: web.Request) -> web.Response:
    """Redirect to the MJPEG endpoint."""
    raise web.HTTPFound("/mjpeg")


async def _handle_error(request: web.Request) -> web.Response:
    """Always fail, to exercise error handling."""
    return web.Response(status=500, text="upstream exploded")


async def _handle_teapot(request: web.Request) -> web.Response:
    """Return an unidentifiable content type."""
    return web.Response(text="not media at all", content_type="application/json")


async def _handle_slow_headers(request: web.Request) -> web.StreamResponse:
    """Send headers and then nothing, to exercise stall handling."""
    response = web.StreamResponse(status=200)
    response.content_type = "multipart/x-mixed-replace; boundary=frame"
    await response.prepare(request)
    await asyncio.sleep(30)
    return response


def build_app(hits: dict[str, int], media_path: str) -> web.Application:
    """Build the upstream application."""
    app = web.Application()
    app["hits"] = hits
    app["media_file"] = media_path
    app.add_routes(
        [
            web.get("/mjpeg", _handle_mjpeg),
            web.get("/mjpeg-endless", _handle_endless_mjpeg),
            web.get("/snapshot.jpg", _handle_snapshot),
            web.get("/hls/master.m3u8", _handle_master),
            web.get("/hls/video/low.m3u8", _handle_media_playlist),
            web.get("/hls/video/segment0.m4s", _handle_bytes),
            web.get("/hls/video/key.bin", _handle_bytes),
            web.get("/hls/video/init.mp4", _handle_bytes),
            web.get("/media.mp4", _handle_media_file),
            web.get("/auth/basic", _handle_basic),
            web.get("/auth/digest", _handle_digest),
            web.get("/redirect", _handle_redirect),
            web.get("/error", _handle_error),
            web.get("/teapot", _handle_teapot),
            web.get("/slow", _handle_slow_headers),
        ]
    )
    return app


def create_media_file() -> tuple[int, str]:
    """Create a temporary file with known, range-friendly contents."""
    file_descriptor, media_path = tempfile.mkstemp(suffix=".mp4")
    os.write(file_descriptor, bytes(range(256)) * 24)
    os.close(file_descriptor)
    return file_descriptor, media_path
