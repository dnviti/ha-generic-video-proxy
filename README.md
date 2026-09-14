# Generic Video Proxy

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/daniele/ha-generic-video-proxy/actions/workflows/validate.yml/badge.svg)](https://github.com/daniele/ha-generic-video-proxy/actions/workflows/validate.yml)
[![Tests](https://github.com/daniele/ha-generic-video-proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/daniele/ha-generic-video-proxy/actions/workflows/tests.yml)

A Home Assistant integration that proxies **any** HTTP(S) video or image stream
through Home Assistant's own web server, and ships with its own Lovelace card to
play it.

Point it at an MJPEG stream, an HLS playlist, an MP4 file, a still image
endpoint, or an RTSP camera. Home Assistant fetches the stream from your LAN and
re-serves it from its own address, so the video plays in the Home Assistant web
UI **over HTTP or HTTPS, at home and from outside** — with no mixed content
errors, no CORS problems, and no credentials exposed to the browser.

```yaml
type: custom:generic-video-proxy-card
entity: camera.front_door
```

## Why proxy the stream at all?

A camera that only speaks `http://192.168.1.10:8080/video` cannot be played by a
browser that loaded Home Assistant over `https://`. The browser blocks it as
mixed content, and from outside your network the address is not even reachable.
Putting the camera URL directly in a dashboard card therefore produces one of
the classic Home Assistant video problems:

| Symptom | Cause |
| --- | --- |
| Nothing plays over HTTPS | Mixed content: an `http://` stream inside an `https://` page |
| Nothing plays remotely | The browser cannot reach a private LAN address |
| Stream plays at home only | The card points at the camera, not at Home Assistant |
| Credentials in the dashboard | Username and password embedded in the stream URL |

This integration moves the connection to the server side. Home Assistant opens
the upstream connection, and the browser only ever talks to Home Assistant at the
same origin and scheme the dashboard was served from.

## Features

- **Generic sources** — MJPEG, HLS, progressive video, polled still images and
  RTSP, with automatic detection of the source type.
- **One upstream connection per stream** — the backend holds a single connection
  to the camera and fans it out to every viewer (phone, wall tablet, desktop),
  then closes it when the last viewer leaves.
- **Automatic reconnection** with exponential backoff, and a stall timeout that
  recycles a silently dead connection.
- **Works everywhere Home Assistant works** — plain HTTP, HTTPS behind a reverse
  proxy, Nabu Casa remote access, companion apps.
- **A real `camera` entity per stream**, so dashboards, automations, snapshots
  and third party cards work too.
- **Dedicated Lovelace card** with a visual editor, live status badge, automatic
  reconnection, fullscreen and snapshot buttons, and a bundled hls.js player for
  browsers without native HLS support.
- **Basic and Digest HTTP authentication** for the upstream device (many IP
  cameras only speak Digest).
- **Signed, expiring media URLs** — the browser never needs an `Authorization`
  header it cannot send, and the upstream address cannot be swapped for another
  one (no open proxy).
- **Byte range support** for progressive media and HLS segments, so seeking works
  and players only fetch what they need.
- **Root cause visible** — a status endpoint, a status badge and diagnostics with
  credentials redacted.

## Supported stream types

| Source type | What it is | Example |
| --- | --- | --- |
| `mjpeg` | `multipart/x-mixed-replace` image stream | `http://192.168.1.10:8080/video` |
| `hls` | HLS playlist (`m3u8`) | `https://host/live/index.m3u8` |
| `progressive` | A single video file | `https://host/clip.mp4` |
| `image` | A still image endpoint, polled and re-published as MJPEG | `http://cam/snapshot.jpg` |
| `rtsp` | Delegated to Home Assistant's own stream component | `rtsp://user:pass@cam/stream` |
| `auto` | Detected from the upstream `Content-Type` when you save | — |

## Installation

### HACS (recommended)

1. Open HACS → **Integrations** → the three dot menu → **Custom repositories**.
2. Add `https://github.com/daniele/ha-generic-video-proxy` with category
   **Integration**.
3. Search for **Generic Video Proxy** in HACS and install it.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → Add integration → Generic Video
   Proxy**.

### Manual

Copy the `custom_components/generic_video_proxy` directory into your Home
Assistant `config/custom_components` directory and restart Home Assistant.

## Configuration

Each config entry proxies one stream.

| Field | Default | Description |
| --- | --- | --- |
| Name | — | Name of the stream; also the camera entity name |
| Stream URL | — | The upstream HTTP(S) (or RTSP) address |
| Source type | Detect automatically | Identifies the stream from the upstream response when you save |
| Authentication | None | HTTP authentication used by the device: `Basic` or `Digest` |
| Username / Password | — | Credentials for that authentication |
| Verify TLS certificate | on | Turn off only for devices with a self-signed certificate |
| Snapshot URL | — | Optional JPEG endpoint used for the camera image; needed for HLS or MP4 sources that have no still image |
| Poster image URL | — | Optional image shown before the video starts |
| Extra request headers | — | One `Name: value` per line, for example `X-Api-Key: my-token` |
| Snapshot interval | 5 s | How often a still image source is polled |
| Reconnect interval | 5 s | Delay before reconnecting after a failure |
| Connect timeout | 10 s | Timeout for connecting to the source |
| Stall timeout | 30 s | Close the connection when no data arrives for this long |
| Frame buffer | 5 | How many frames are buffered per viewer |
| Test the connection before saving | on | Validates the URL and detects the source type |

All values can be changed later through **Configure** on the integration entry.
An empty password field keeps the stored password.

## The Lovelace card

The card is served by the integration itself and registered as a frontend
module, so installing the integration is enough — no manual Lovelace resource is
required. After a Home Assistant restart, `custom:generic-video-proxy-card` is
available; add it from the dashboard UI and configure it visually, or in YAML:

```yaml
type: custom:generic-video-proxy-card
entity: camera.front_door
title: Front door
aspect_ratio: 16:9
fit: contain
muted: true
show_status: true
```

### Card options

| Option | Default | Description |
| --- | --- | --- |
| `entity` | — | A camera entity created by this integration |
| `entry_id` | — | Alternatively, the config entry id of a proxy |
| `title` | Entity name | Overlay title; set to `""` for none |
| `aspect_ratio` | `16:9` | Any `W:H` ratio; set to `""` to let the card fill its container |
| `fit` | `contain` | `contain` keeps the whole frame visible, `cover` fills the card |
| `muted` | `true` | Start muted (browsers only autoplay muted video) |
| `controls` | `false` | Show the browser's own video controls |
| `show_controls` | `true` | Show the card's overlay buttons (play, mute, snapshot, fullscreen) |
| `show_status` | `true` | Show the live status badge with the current frame rate |
| `show_snapshot_button` | `true` | Show the button that opens the current frame |
| `background` | `false` | Transparent background, for use inside another card |
| `image_refresh_interval` | `10` | Polling interval in seconds for still image sources |
| `player` | `auto` | Force a player: `mjpeg`, `hls`, `video`, `image` or `native` |
| `hls_js_url` | bundled | Load hls.js from another URL (for example a CDN) |
| `force_hls_js` | `false` | Use hls.js even where the browser supports HLS natively |
| `columns` | `12` | Grid columns used in the sections view |

The card reconnects by itself: if the upstream drops, it retries with an
exponential backoff and shows the reason in the card instead of going blank.

## Camera entity and native Home Assistant playback

Every proxy also creates a `camera` entity, named after the config entry. That
gives you:

- still images through Home Assistant's own camera proxy, including
  `/api/camera_proxy/<entity_id>?token=…` (the rotating token is provided in the
  entity attributes as `entity_picture`);
- a live MJPEG stream through `/api/camera_proxy_stream/<entity_id>`, served from
  the same shared upstream connection as the card;
- for HLS and RTSP sources, a `stream_source` so Home Assistant's own stream
  component (ffmpeg) can serve HLS to the frontend and to WebRTC-capable cards.

## How it works

```
browser ──HTTPS──> Home Assistant  ──HTTP(S)──> camera
   <img>/<video>/hls.js   │  signed URLs      │  Basic/Digest, custom headers
                          │  MJPEG hub        │  one connection, fanned out
                          │  HLS rewriter     │  playlists, segments, keys
                          │  range proxy      │  MP4 seeking, byte ranges
```

- Media endpoints are authorised with **signed, expiring tokens in the URL path**
  (six hours by default, refreshed by the card well before they expire). Tokens
  are path segments rather than query parameters on purpose: HLS players append
  query parameters of their own, and a signature that covers the query string
  would break.
- The token also carries the upstream URL, so a client cannot point the proxy at
  an arbitrary address.
- HLS playlists are fetched, rewritten and returned with every URI — variant
  playlists, media playlists, segments, encryption keys, initialisation sections
  and `#EXT-X-BYTERANGE` offsets — routed back through Home Assistant.
- `#EXT-X-BYTERANGE` is translated into an upstream `Range` request, so byte
  range segments behave exactly like the upstream intends.

### HTTP endpoints

Everything the card uses is also usable by scripts and other cards. All of these
are relative to your Home Assistant URL and require a signed token:

| Endpoint | Purpose |
| --- | --- |
| `/api/generic_video_proxy/<entry_id>/stream.mjpeg/<token>` | Multipart MJPEG stream |
| `/api/generic_video_proxy/<entry_id>/stream.m3u8/<token>` | Rewritten HLS playlist |
| `/api/generic_video_proxy/<entry_id>/segment/<token>` | HLS segment, key or init section |
| `/api/generic_video_proxy/<entry_id>/media/<token>` | Progressive media, with byte ranges |
| `/api/generic_video_proxy/<entry_id>/snapshot.jpg/<token>` | Current still frame |
| `/api/generic_video_proxy/<entry_id>/poster.jpg/<token>` | Configured poster image |
| `/api/generic_video_proxy/<entry_id>/status/<token>` | JSON stream health |

Two websocket commands mint those URLs: `generic_video_proxy/list` and
`generic_video_proxy/stream_url` (with `entity_id` or `entry_id`).

## Security notes

- The integration is a proxy by design: the upstream URL lives inside a signed
  token, so only URLs that this integration rewrote can be fetched. Signed URLs
  are not an open proxy.
- Tokens are signed with a random secret generated once per Home Assistant
  instance and stored in `.storage/generic_video_proxy.tokens`.
- Credentials are stored in the config entry like any other integration's
  secrets, and are redacted from diagnostics and logs. Upstream URLs are logged
  without credentials and without their query string.
- Disabling **Verify TLS certificate** accepts a self-signed certificate for that
  one proxy only.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| "Could not read the stream" when saving | The URL, the credentials, and that Home Assistant itself can reach the device |
| Card shows "Waiting for the stream…" | Open the status endpoint, or **Settings → Devices & services → Generic Video Proxy → Download diagnostics** |
| Camera has no still image | HLS and MP4 sources cannot be turned into a frame without a decoder: set **Snapshot URL** |
| Video stays black in Chrome/Safari | Autoplay rules: keep the card muted, or tap the card once |
| Several MJPEG cards on one dashboard stop loading | Over HTTP/1.1 a browser opens at most six connections per host; use HTTP/2 (any HTTPS reverse proxy) or fewer simultaneous live views |
| HLS never starts in Chrome | The bundled hls.js is loaded from Home Assistant; check that `/generic_video_proxy/hls.light.min.js` is reachable, or set `hls_js_url` |
| RTSP source does not play in the card | RTSP is played through Home Assistant's stream component, which needs ffmpeg (included in Home Assistant OS and Supervised) |
| Upstream is only reachable intermittently | Tune **Reconnect interval** and **Stall timeout**; the backend reconnects on its own |

Debug logging:

```yaml
logger:
  logs:
    custom_components.generic_video_proxy: debug
```

## Development

Prerequisites: Python 3.12 and Node.js for the card tests.

```shell
# Python tests and lint
python -m venv .venv
.venv/Scripts/activate            # or: source .venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff check custom_components tests scripts
ruff format --check custom_components tests scripts

# Browser-side card tests (jsdom)
npm ci
npm test

# Regenerate the brand images
python scripts/make_brand_assets.py
```

The Python suite includes end-to-end tests that set up a real config entry,
serve a real upstream MJPEG/HLS/MP4 server on `127.0.0.1` and read the proxied
bytes back through Home Assistant's own aiohttp server, so the wire format is
verified rather than mocked. `hassfest` and the HACS validation run in CI
(`.github/workflows/validate.yml`).

## Publishing this repository to HACS

This repository already follows the HACS requirements: one integration under
`custom_components/`, a `manifest.json` with all required keys, local brand
images under `custom_components/generic_video_proxy/brand/`, an `hacs.json`, a
README, tests, and workflows running hassfest and the HACS action. Before
publishing, replace the placeholder owner in these places:

- `custom_components/generic_video_proxy/manifest.json`: `codeowners`,
  `documentation`, `issue_tracker`;
- `custom_components/generic_video_proxy/www/generic-video-proxy-card.js`: the
  `documentationURL` of the card;
- `README.md` and `CHANGELOG.md`: repository links and badges.

Then add a repository description and the topics `home-assistant`, `hacs`,
`integration`, `mjpeg`, `hls` on GitHub, create a release whose tag matches the
`version` in `manifest.json` (`v0.1.0`), and optionally open a pull request
against [hacs/default](https://github.com/hacs/default) to be included in the
HACS store.

## License

MIT — see [LICENSE](LICENSE). The bundled hls.js build is Apache-2.0; see
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
