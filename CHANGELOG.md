# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-14

Initial release.

### Added

- Home Assistant integration that proxies generic HTTP(S) video and image
  streams through Home Assistant's own HTTP server, so a stream plays in the web
  UI over plain HTTP, behind an HTTPS reverse proxy and from outside the home.
- Source types: MJPEG (`multipart/x-mixed-replace`), HLS (`m3u8`), progressive
  video (MP4/WebM), polled still images and RTSP (handed to Home Assistant's own
  stream component). `auto` detects the type from the upstream response.
- MJPEG parsing accepts both delimiter forms real cameras produce when the
  advertised boundary already carries the leading dashes (`boundary=--foo` is
  written as either `----foo` or `--foo`), verified against real hardware.
- A shared upstream connection per proxy: one connection is opened when the
  first viewer arrives, fanned out to every viewer, and closed again after the
  last viewer leaves. Reconnects use exponential backoff.
- Media endpoints for MJPEG streams, rewritten HLS playlists, HLS segments and
  progressive media with HTTP byte range support, still snapshots, posters and a
  JSON status document.
- Signed, expiring media URLs: the browser never needs a header it cannot send,
  and the proxied upstream URL cannot be swapped for an arbitrary target.
- HTTP authentication support for upstream devices: Basic and Digest.
- A `camera` entity per proxy, so proxied streams also work in dashboards,
  automations and third party cards. Home Assistant's own camera proxy serves the
  still image and the MJPEG stream.
- A dedicated Lovelace card (`custom:generic-video-proxy-card`) with a visual
  editor, automatic reconnection, live status badge, fullscreen and snapshot
  buttons, and a bundled hls.js player for browsers without native HLS.
- Config and options flow with upstream validation and automatic source type
  detection, plus diagnostics with credentials redacted.
- Test suite: 167 Python tests (including end-to-end tests through Home
  Assistant's own aiohttp server and a real upstream HTTP server) and 11
  browser-side card tests.

[0.1.0]: https://github.com/dnviti/ha-generic-video-proxy/releases/tag/v0.1.0
