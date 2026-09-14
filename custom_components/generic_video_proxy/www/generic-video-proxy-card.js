/**
 * Generic Video Proxy card
 *
 * A dedicated player for streams proxied by the Generic Video Proxy
 * integration. The card never talks to the camera: it asks Home Assistant's
 * websocket API for freshly signed, relative media URLs and plays them. Because
 * every URL it uses is served by Home Assistant itself, the same card works on
 * plain HTTP, behind an HTTPS reverse proxy and from outside the home.
 *
 * Supported players:
 *   mjpeg    multipart MJPEG stream rendered in an <img> (polled still image
 *            sources are re-published as MJPEG by the backend, so they use the
 *            same player)
 *   hls      HLS playlist, played natively where supported, else via hls.js
 *   video    progressive MP4/WebM in a <video> element
 *   native   RTSP sources handed to Home Assistant's own stream component
 *
 * Licensed under the MIT license, like the rest of the integration.
 */

const CARD_VERSION = "0.1.0";
const CARD_TAG = "generic-video-proxy-card";
const EDITOR_TAG = "generic-video-proxy-card-editor";
const PROJECT_URL = "https://github.com/dnviti/ha-generic-video-proxy";

const WS_STREAM_URL = "generic_video_proxy/stream_url";
const WS_LIST = "generic_video_proxy/list";

const STATUS_POLL_MS = 15000;
const MIN_RECONNECT_MS = 1000;
const MAX_RECONNECT_MS = 20000;
/** Refresh signed URLs when this many seconds of their lifetime remain. */
const REFRESH_MARGIN_S = 600;

const LABELS = {
  entity: "Camera entity",
  entry_id: "Proxy (config entry)",
  title: "Title",
  aspect_ratio: "Aspect ratio",
  fit: "Fit",
  muted: "Start muted",
  controls: "Show native video controls",
  show_status: "Show status badge",
  background: "Transparent background",
  image_refresh_interval: "Image refresh interval (s)",
  hls_js_url: "Custom hls.js URL",
};

const STYLES = `
  :host {
    display: block;
  }
  .container {
    position: relative;
    display: block;
    overflow: hidden;
    width: 100%;
    background: var(--generic-video-proxy-background, #000);
    border-radius: var(--ha-card-border-radius, 12px);
    aspect-ratio: var(--gvp-aspect-ratio, 16 / 9);
  }
  .container.no-aspect {
    aspect-ratio: auto;
    min-height: 120px;
  }
  .container.background {
    background: none;
  }
  .media, .media img, .media video {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: var(--gvp-fit, contain);
  }
  .media video {
    background: #000;
  }
  .title {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    padding: 10px 12px;
    box-sizing: border-box;
    color: #fff;
    font-size: 0.95rem;
    font-weight: 500;
    text-shadow: 0 1px 3px rgba(0, 0, 0, 0.8);
    background: linear-gradient(to bottom, rgba(0, 0, 0, 0.55), rgba(0, 0, 0, 0));
    pointer-events: none;
  }
  .badges {
    position: absolute;
    top: 10px;
    right: 10px;
    display: flex;
    align-items: center;
    gap: 6px;
    pointer-events: none;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 8px;
    border-radius: 10px;
    color: #fff;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(4px);
  }
  .badge .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--error-color, #f44336);
  }
  .badge.live .dot {
    background: #4caf50;
    animation: pulse 1.8s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }
  .message {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 6px;
    padding: 16px;
    box-sizing: border-box;
    color: var(--secondary-text-color, #ccc);
    font-size: 0.85rem;
    text-align: center;
    background: rgba(0, 0, 0, 0.45);
  }
  .message.error {
    color: #fff;
  }
  .controls {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 2px;
    padding: 6px 8px;
    background: linear-gradient(to top, rgba(0, 0, 0, 0.6), rgba(0, 0, 0, 0));
    opacity: 0;
    transition: opacity 150ms ease-in-out;
  }
  .container:hover .controls,
  .container:focus-within .controls {
    opacity: 1;
  }
  .controls button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    padding: 0;
    border: none;
    border-radius: 50%;
    color: #fff;
    background: rgba(255, 255, 255, 0.12);
    cursor: pointer;
    transition: background 120ms ease-in-out;
  }
  .controls button:hover {
    background: rgba(255, 255, 255, 0.3);
  }
  .controls button:focus-visible {
    outline: 2px solid var(--primary-color, #03a9f4);
    outline-offset: 1px;
  }
  .controls svg {
    width: 18px;
    height: 18px;
    fill: currentColor;
  }
`;

const ICONS = {
  play: "M8,5.14V19.14L19,12.14L8,5.14Z",
  pause: "M14,19H18V5H14M6,19H10V5H6V19Z",
  volume:
    "M14,3.23V5.29C16.89,6.15 19,8.83 19,12C19,15.17 16.89,17.84 14,18.7V20.77C18,19.86 21,16.28 21,12C21,7.72 18,4.14 14,3.23M16.5,12C16.5,10.23 15.5,8.71 14,7.97V16C15.5,15.29 16.5,13.76 16.5,12M3,9V15H7L12,20V4L7,9H3Z",
  mute: "M12,4L9.91,6.09L12,8.18M4.27,3L3,4.27L7.73,9H3V15H7L12,20V13.27L16.25,17.53C15.58,18.04 14.83,18.46 14,18.7V20.77C15.38,20.45 16.63,19.82 17.68,18.96L19.73,21L21,19.73L12,10.73M19,12C19,12.94 18.8,13.82 18.46,14.64L19.97,16.15C20.62,14.91 21,13.5 21,12C21,7.72 18,4.14 14,3.23V5.29C16.89,6.15 19,8.83 19,12M16.5,12C16.5,10.23 15.5,8.71 14,7.97V10.18L16.45,12.63C16.5,12.43 16.5,12.21 16.5,12Z",
  fullscreen:
    "M14,14H19V16H16V19H14V14M5,14H10V19H8V16H5V14M8,5H10V10H5V8H8V5M19,8V10H14V5H16V8H19Z",
  snapshot:
    "M4,4H7L9,2H15L17,4H20A2,2 0 0,1 22,6V18A2,2 0 0,1 20,20H4A2,2 0 0,1 2,18V6A2,2 0 0,1 4,4M12,7A5,5 0 0,0 7,12A5,5 0 0,0 12,17A5,5 0 0,0 17,12A5,5 0 0,0 12,7M12,9A3,3 0 0,1 15,12A3,3 0 0,1 12,15A3,3 0 0,1 9,12A3,3 0 0,1 12,9Z",
};

let hlsJsPromise = null;

/** Load hls.js once, from the integration itself or from a configured URL. */
function loadHlsJs(url) {
  if (window.Hls) {
    return Promise.resolve(window.Hls);
  }
  if (hlsJsPromise) {
    return hlsJsPromise;
  }
  hlsJsPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url;
    script.async = true;
    script.onload = () => {
      if (window.Hls) {
        resolve(window.Hls);
      } else {
        reject(new Error("hls.js loaded but window.Hls is missing"));
      }
    };
    script.onerror = () => reject(new Error(`Could not load hls.js from ${url}`));
    document.head.appendChild(script);
  }).catch((err) => {
    // Allow a later retry to try again instead of caching the failure.
    hlsJsPromise = null;
    throw err;
  });
  return hlsJsPromise;
}

/** Create an element with an optional class name and text. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

/** Append a cache buster, keeping any existing query string intact. */
function withCacheBuster(url, stamp) {
  if (!url || !stamp) {
    return url;
  }
  return `${url}${url.includes("?") ? "&" : "?"}_gvp=${stamp}`;
}

class GenericVideoProxyCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
    this._media = null;
    this._resolvedKey = null;
    this._resolving = false;
    this._status = null;
    this._statusTimer = null;
    this._error = null;
    this._playerKind = null;
    this._playerKey = null;
    this._hls = null;
    this._video = null;
    this._imageTimer = null;
    this._retryTimer = null;
    this._retryDelay = MIN_RECONNECT_MS;
    this._built = false;
  }

  static getStubConfig(_hass, entities) {
    const camera = (entities || []).find((entityId) => entityId.startsWith("camera."));
    return camera ? { entity: camera } : {};
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  get hass() {
    return this._hass;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first || this._needsRefresh()) {
      this._resolve();
    }
    if (first) {
      this._render();
      this._startStatusPoll();
    }
  }

  setConfig(config) {
    if (!config || (!config.entity && !config.entry_id)) {
      throw new Error(
        "generic-video-proxy-card: define 'entity' (a camera entity created by this integration) or 'entry_id'"
      );
    }
    const next = { ...config };
    if (JSON.stringify(next) === JSON.stringify(this._config)) {
      return;
    }
    this._config = next;
    this._media = null;
    this._resolvedKey = null;
    this._teardownPlayer();
    if (this._built) {
      this._render();
    }
  }

  getCardSize() {
    return 5;
  }

  getGridOptions() {
    return { columns: this._config?.columns || 12, min_columns: 3, rows: "auto" };
  }

  connectedCallback() {
    this._startStatusPoll();
    if (this._built) {
      this._render();
    }
  }

  disconnectedCallback() {
    this._stopStatusPoll();
    this._teardownPlayer();
  }

  // -------------------------------------------------------------- data flow

  _needsRefresh() {
    if (!this._media) {
      return true;
    }
    const expiresAt = (this._media.issued_at || 0) + (this._media.expires_in || 0);
    return expiresAt - REFRESH_MARGIN_S <= Date.now() / 1000;
  }

  async _resolve() {
    if (!this._hass || !this._config || this._resolving) {
      return;
    }
    const key = this._config.entity || this._config.entry_id;
    if (this._resolvedKey === key && !this._needsRefresh()) {
      return;
    }
    const message = { type: WS_STREAM_URL };
    if (this._config.entity) {
      message.entity_id = this._config.entity;
    } else {
      message.entry_id = this._config.entry_id;
    }

    this._resolving = true;
    try {
      this._media = await this._hass.callWS(message);
      this._resolvedKey = key;
      this._error = null;
    } catch (err) {
      this._media = null;
      this._resolvedKey = null;
      this._error = err?.message || String(err);
    } finally {
      this._resolving = false;
    }
    if (this._built) {
      this._render();
    }
  }

  async _pollStatus() {
    if (!this._hass || !this._media?.status_url || this._config?.show_status === false) {
      return;
    }
    try {
      const response = await fetch(this._media.status_url, { cache: "no-store" });
      this._status = response.ok ? await response.json() : null;
    } catch (err) {
      this._status = null;
    }
    this._updateBadges();
  }

  _startStatusPoll() {
    if (this._statusTimer || this._config?.show_status === false) {
      return;
    }
    this._statusTimer = window.setInterval(() => this._pollStatus(), STATUS_POLL_MS);
    this._pollStatus();
  }

  _stopStatusPoll() {
    if (this._statusTimer) {
      window.clearInterval(this._statusTimer);
      this._statusTimer = null;
    }
  }

  // ---------------------------------------------------------------- rendering

  _buildSkeleton() {
    this.shadowRoot.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = STYLES;
    this._container = el("div", "container");
    this._mediaHost = el("div", "media");
    this._titleEl = el("div", "title");
    this._badgesEl = el("div", "badges");
    this._messageEl = el("div", "message");
    this._controlsEl = el("div", "controls");
    this._container.append(
      this._mediaHost,
      this._titleEl,
      this._badgesEl,
      this._messageEl,
      this._controlsEl
    );
    this.shadowRoot.append(style, this._container);
    this._built = true;
  }

  _render() {
    if (!this.shadowRoot) {
      return;
    }
    if (!this._built) {
      this._buildSkeleton();
    }

    const config = this._config || {};
    const aspect = config.aspect_ratio === undefined ? "16:9" : config.aspect_ratio;
    if (aspect) {
      const parts = String(aspect).split(":");
      const ratio = parts.length === 2 ? `${Number(parts[0])} / ${Number(parts[1])}` : String(aspect);
      this._container.style.setProperty("--gvp-aspect-ratio", ratio);
      this._container.classList.remove("no-aspect");
    } else {
      this._container.classList.add("no-aspect");
    }
    this._container.style.setProperty("--gvp-fit", config.fit === "cover" ? "cover" : "contain");
    this._container.classList.toggle("background", config.background === true);

    this._titleEl.textContent = config.title ?? this._media?.name ?? "";
    this._titleEl.style.display = this._titleEl.textContent ? "" : "none";

    this._messageEl.classList.remove("error");
    this._messageEl.style.display = "none";
    this._messageEl.style.cursor = "";
    this._messageEl.onclick = null;

    if (this._error) {
      this._teardownPlayer();
      this._messageEl.style.display = "";
      this._messageEl.classList.add("error");
      this._messageEl.textContent = this._error;
      return;
    }

    if (!this._media) {
      this._messageEl.style.display = "";
      this._messageEl.textContent = this._hass ? "Loading…" : "Waiting for Home Assistant…";
      return;
    }

    this._mountPlayer();
    this._updateBadges();
  }

  _updateBadges() {
    if (!this._badgesEl) {
      return;
    }
    this._badgesEl.innerHTML = "";
    if (this._config?.show_status === false) {
      return;
    }
    const state = this._status?.state;
    const live = state === "live";
    const badge = el("div", `badge${live ? " live" : ""}`);
    badge.appendChild(el("span", "dot"));
    let label = live ? "Live" : state || "Idle";
    if (live && this._status?.fps) {
      label += ` ${this._status.fps} fps`;
    }
    badge.appendChild(el("span", null, label));
    this._badgesEl.appendChild(badge);
  }

  _showMessage(text) {
    if (!this._messageEl) {
      return;
    }
    this._messageEl.style.display = "";
    this._messageEl.classList.remove("error");
    this._messageEl.textContent = text;
  }

  // ------------------------------------------------------------------ players

  _playerKindFor(media) {
    const requested = this._config?.player;
    return requested && requested !== "auto" ? requested : media.player;
  }

  _mountPlayer() {
    const media = this._media;
    const kind = this._playerKindFor(media);
    const key = `${this._resolvedKey}:${kind}:${media.issued_at}`;
    if (this._playerKind === kind && this._playerKey === key) {
      return;
    }
    this._teardownPlayer();
    this._playerKind = kind;
    this._playerKey = key;

    switch (kind) {
      case "mjpeg":
        this._mountImage(() => withCacheBuster(media.stream_url, Date.now()), {
          retryable: true,
        });
        break;
      case "image":
        this._mountImage(() => withCacheBuster(media.snapshot_url, Date.now()), {
          poll: true,
        });
        break;
      case "video":
        this._mountVideo(media.media_url, false);
        break;
      case "hls":
        this._mountVideo(media.playlist_url, true);
        break;
      case "native":
        this._mountNative();
        break;
      default:
        this._showMessage(`Unsupported player type: ${kind}`);
    }
    this._buildControls();
  }

  _mountImage(getUrl, { retryable = false, poll = false } = {}) {
    const url = getUrl();
    if (!url) {
      this._showMessage("This source has no image endpoint.");
      return;
    }
    const image = document.createElement("img");
    image.alt = this._config?.title ?? this._media?.name ?? "Proxied stream";
    image.decoding = "async";
    image.addEventListener("load", () => {
      this._retryDelay = MIN_RECONNECT_MS;
      this._messageEl.style.display = "none";
    });
    image.addEventListener("error", () => {
      if (retryable) {
        this._scheduleRetry(() => this._remount(), "Waiting for the stream…");
      } else {
        this._showMessage("The image could not be loaded.");
      }
    });
    image.src = url;
    this._mediaHost.replaceChildren(image);

    if (poll) {
      const interval = Math.max(1, Number(this._config?.image_refresh_interval ?? 10)) * 1000;
      this._imageTimer = window.setInterval(() => {
        const next = getUrl();
        if (next) {
          image.src = next;
        }
      }, interval);
    }
  }

  async _mountVideo(url, useHls) {
    if (!url) {
      this._showMessage("This source has no video endpoint.");
      return;
    }
    const video = document.createElement("video");
    video.muted = this._config?.muted !== false;
    video.autoplay = true;
    video.playsInline = true;
    video.controls = this._config?.controls === true;
    video.setAttribute("playsinline", "");
    video.preload = "auto";
    video.addEventListener("playing", () => {
      this._retryDelay = MIN_RECONNECT_MS;
      this._messageEl.style.display = "none";
      this._buildControls();
    });
    video.addEventListener("error", () => {
      if (!this._hls) {
        this._scheduleRetry(() => this._remount(), "Waiting for the stream…");
      }
    });
    this._video = video;
    this._mediaHost.replaceChildren(video);

    const nativeHls =
      !!video.canPlayType("application/vnd.apple.mpegurl") &&
      this._config?.force_hls_js !== true;

    if (!useHls || nativeHls) {
      video.src = url;
      this._attemptPlay(video);
      return;
    }

    this._showMessage("Loading the player…");
    let Hls;
    try {
      Hls = await loadHlsJs(this._config?.hls_js_url || this._media?.hls_js_url);
    } catch (err) {
      this._showMessage(`Could not load the HLS player: ${err.message}`);
      return;
    }
    if (!Hls.isSupported()) {
      video.src = url;
      this._attemptPlay(video);
      return;
    }

    const hls = new Hls({
      // Live camera streams: stability over latency, and no playlist reload
      // parameters appended by the player.
      lowLatencyMode: false,
      liveDurationInfinity: true,
      backBufferLength: 30,
      manifestLoadingTimeOut: 15000,
      fragLoadingTimeOut: 30000,
    });
    this._hls = hls;
    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (!data?.fatal) {
        return;
      }
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        hls.startLoad();
        return;
      }
      if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        hls.recoverMediaError();
        return;
      }
      this._scheduleRetry(() => this._remount(), "Waiting for the stream…");
    });
    hls.loadSource(url);
    hls.attachMedia(video);
    this._attemptPlay(video);
  }

  async _mountNative() {
    const entityId = this._config?.entity || this._media?.entity_id;
    if (!entityId) {
      this._showMessage("This source needs a camera entity to stream through Home Assistant.");
      return;
    }
    this._showMessage("Starting the Home Assistant stream…");
    try {
      const { url } = await this._hass.callWS({
        type: "camera/stream",
        entity_id: entityId,
        format: "hls",
      });
      this._mountVideo(url, true);
    } catch (err) {
      // The stream component (ffmpeg) may be unavailable: fall back to stills.
      const picture = this._hass?.states?.[entityId]?.attributes?.entity_picture;
      if (picture) {
        this._mountImage(
          () => this._hass?.states?.[entityId]?.attributes?.entity_picture,
          { poll: true }
        );
      } else {
        this._showMessage(`Home Assistant could not stream this camera: ${err?.message ?? err}`);
      }
    }
  }

  /** Tear down and rebuild the current player (used by retries). */
  _remount() {
    this._teardownPlayer();
    if (this._media) {
      this._mountPlayer();
    }
  }

  _attemptPlay(video) {
    const attempt = video.play();
    if (attempt?.catch) {
      attempt.catch(() => {
        // Autoplay was blocked: show a tap target instead.
        this._showMessage("Tap to play");
        this._messageEl.style.cursor = "pointer";
        this._messageEl.onclick = () => {
          video.muted = true;
          video.play().catch(() => undefined);
          this._messageEl.style.display = "none";
          this._messageEl.onclick = null;
        };
      });
    }
  }

  _scheduleRetry(action, message) {
    if (this._retryTimer) {
      return;
    }
    this._showMessage(message);
    const delay = this._retryDelay;
    this._retryDelay = Math.min(this._retryDelay * 2, MAX_RECONNECT_MS);
    this._retryTimer = window.setTimeout(() => {
      this._retryTimer = null;
      action();
    }, delay);
  }

  _buildControls() {
    if (!this._controlsEl) {
      return;
    }
    this._controlsEl.innerHTML = "";
    const config = this._config || {};
    if (config.show_controls === false || config.background === true) {
      return;
    }

    const addButton = (icon, label, handler) => {
      const button = el("button");
      button.type = "button";
      button.title = label;
      button.setAttribute("aria-label", label);
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 24 24");
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", icon);
      svg.appendChild(path);
      button.appendChild(svg);
      button.addEventListener("click", handler);
      this._controlsEl.appendChild(button);
      return button;
    };

    const video = this._video;
    if (video) {
      const playButton = addButton(video.paused ? ICONS.play : ICONS.pause, "Play", () => {
        if (video.paused) {
          video.play().catch(() => undefined);
          playButton.querySelector("path").setAttribute("d", ICONS.pause);
        } else {
          video.pause();
          playButton.querySelector("path").setAttribute("d", ICONS.play);
        }
      });
      const muteButton = addButton(video.muted ? ICONS.mute : ICONS.volume, "Mute", () => {
        video.muted = !video.muted;
        muteButton
          .querySelector("path")
          .setAttribute("d", video.muted ? ICONS.mute : ICONS.volume);
      });
    }

    if (config.show_snapshot_button !== false && this._media?.snapshot_url) {
      addButton(ICONS.snapshot, "Open snapshot", () => {
        window.open(this._media.snapshot_url, "_blank", "noopener");
      });
    }

    addButton(ICONS.fullscreen, "Fullscreen", () => {
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => undefined);
      } else {
        this._container.requestFullscreen?.().catch(() => undefined);
      }
    });
  }

  _teardownPlayer() {
    if (this._retryTimer) {
      window.clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }
    if (this._imageTimer) {
      window.clearInterval(this._imageTimer);
      this._imageTimer = null;
    }
    if (this._hls) {
      this._hls.destroy();
      this._hls = null;
    }
    if (this._video) {
      this._video.pause?.();
      this._video.removeAttribute("src");
      this._video.load?.();
      this._video = null;
    }
    if (this._mediaHost) {
      this._mediaHost.replaceChildren();
    }
    this._playerKind = null;
    this._playerKey = null;
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, GenericVideoProxyCard);
}

/** Minimal card editor built on Home Assistant's own form element. */
class GenericVideoProxyCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._entries = [];
    this._rendered = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._entries.length) {
      this._loadEntries();
    }
    this._render();
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  async _loadEntries() {
    try {
      const result = await this._hass.callWS({ type: WS_LIST });
      this._entries = result?.entries || [];
    } catch (err) {
      this._entries = [];
    }
    this._render();
  }

  _schema() {
    const entryOptions = this._entries.map((entry) => ({
      value: entry.entry_id,
      label: entry.entity_id ? `${entry.name} (${entry.entity_id})` : entry.name,
    }));
    return [
      { name: "entity", selector: { entity: { domain: "camera" } } },
      ...(entryOptions.length
        ? [
            {
              name: "entry_id",
              selector: { select: { options: entryOptions, mode: "dropdown", sort: false } },
            },
          ]
        : []),
      { name: "title", selector: { text: {} } },
      {
        name: "",
        type: "grid",
        schema: [
          { name: "aspect_ratio", selector: { text: {} } },
          { name: "fit", selector: { select: { options: ["contain", "cover"], mode: "dropdown" } } },
        ],
      },
      {
        name: "",
        type: "grid",
        schema: [
          { name: "muted", selector: { boolean: {} } },
          { name: "controls", selector: { boolean: {} } },
        ],
      },
      {
        name: "",
        type: "grid",
        schema: [
          { name: "show_status", selector: { boolean: {} } },
          { name: "background", selector: { boolean: {} } },
        ],
      },
      {
        name: "image_refresh_interval",
        selector: { number: { min: 1, max: 3600, mode: "box", unit_of_measurement: "s" } },
      },
      { name: "hls_js_url", selector: { text: {} } },
    ];
  }

  _render() {
    if (!this._hass || !this.shadowRoot) {
      return;
    }
    if (!this._rendered) {
      this.shadowRoot.innerHTML = "";
      this._form = document.createElement("ha-form");
      this._form.addEventListener("value-changed", (event) => {
        event.stopPropagation();
        this._config = { ...this._config, ...event.detail.value };
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: this._config },
            bubbles: true,
            composed: true,
          })
        );
      });
      this.shadowRoot.appendChild(this._form);
      this._rendered = true;
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = this._schema();
    this._form.computeLabel = (schema) => LABELS[schema.name] || schema.name;
  }
}

if (!customElements.get(EDITOR_TAG)) {
  customElements.define(EDITOR_TAG, GenericVideoProxyCardEditor);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TAG,
  name: "Generic Video Proxy",
  description: "Play a video or image stream proxied through Home Assistant.",
  preview: false,
  documentationURL: PROJECT_URL,
});

console.info(
  `%c GENERIC-VIDEO-PROXY-CARD %c ${CARD_VERSION} `,
  "color: white; background: #03a9f4; font-weight: 700;",
  "color: #03a9f4; background: white; font-weight: 700;"
);
