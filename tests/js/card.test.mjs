/**
 * Behavioural tests for the Generic Video Proxy Lovelace card.
 *
 * The card is a plain browser script: it expects `window`, `document`,
 * `customElements` and friends to exist when it is evaluated, and it talks to
 * Home Assistant through `hass.callWS`. Every test therefore gets a fresh jsdom
 * window (so the module's own state, the custom element registry and
 * `window.customCards` all start clean) plus a hand-driven fake clock, so cache
 * busters and retry delays are deterministic instead of wall-clock dependent.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { afterEach, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM, VirtualConsole } from "jsdom";

const CARD_TAG = "generic-video-proxy-card";
const EDITOR_TAG = "generic-video-proxy-card-editor";
const WS_STREAM_URL = "generic_video_proxy/stream_url";

const CARD_SOURCE = await readFile(
  fileURLToPath(
    new URL("../../custom_components/generic_video_proxy/www/generic-video-proxy-card.js", import.meta.url)
  ),
  "utf8"
);

/** Let the card's promise chains settle. Uses the host realm, not the fake clock. */
const flush = () => new Promise((resolve) => setImmediate(resolve));

/**
 * Copy plain data out of the jsdom realm: objects the card creates there have a
 * different Object.prototype, which assert.deepEqual compares.
 */
const plain = (value) => JSON.parse(JSON.stringify(value));

/** The moments the fake clock starts at, and the URLs the card is asked to play. */
const CLOCK_START = 1_000_000;
const ENTITY = "camera.front_door";
const STREAM_URL = "/api/generic_video_proxy/stream/camera.front_door?token=abc";
const SNAPSHOT_URL = "/api/generic_video_proxy/snapshot/camera.front_door";
const MEDIA_URL = "/api/generic_video_proxy/video/camera.front_door.mp4";

const openEnvironments = [];

afterEach(() => {
  for (const env of openEnvironments.splice(0)) {
    // A jsdom "Not implemented" report means a stub is missing, and an uncaught
    // exception thrown inside a card listener shows up here too.
    assert.deepEqual(
      env.jsdomErrors.map((error) => `${error.type}: ${error.message}`),
      [],
      "jsdom reported no unimplemented API or uncaught exception"
    );
    env.window.close();
  }
});

/**
 * A jsdom window with the browser APIs the card needs that jsdom does not
 * implement, and a fake clock driving `setTimeout`/`setInterval`/`Date.now`.
 */
function createEnvironment() {
  const jsdomErrors = [];
  const virtualConsole = new VirtualConsole();
  // Swallow the module's console.info banner but keep jsdom's own error reports.
  virtualConsole.on("jsdomError", (error) => jsdomErrors.push(error));

  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "http://localhost/",
    runScripts: "outside-only",
    virtualConsole,
  });
  const { window } = dom;

  // Fake clock: the card reads Date.now() for cache busters and schedules its
  // retry/poll timers on window, so replacing both makes every test tick-free.
  const clock = { now: CLOCK_START, nextId: 1, timers: new Map() };
  const schedule = (kind) => (fn, delay) => {
    const id = clock.nextId++;
    const every = Math.max(0, Number(delay) || 0);
    clock.timers.set(id, { at: clock.now + every, every, fn, kind });
    return id;
  };
  window.setTimeout = schedule("timeout");
  window.setInterval = schedule("interval");
  window.clearTimeout = (id) => clock.timers.delete(id);
  window.clearInterval = (id) => clock.timers.delete(id);
  window.Date.now = () => clock.now;
  clock.advance = (ms) => {
    const target = clock.now + ms;
    for (let guard = 0; guard < 10_000; guard += 1) {
      let dueId = null;
      let due = null;
      for (const [id, timer] of clock.timers) {
        if (timer.at <= target && (due === null || timer.at < due.at)) {
          dueId = id;
          due = timer;
        }
      }
      if (due === null) {
        break;
      }
      clock.now = Math.max(clock.now, due.at);
      if (due.kind === "interval") {
        due.at = clock.now + Math.max(1, due.every);
      } else {
        clock.timers.delete(dueId);
      }
      due.fn();
    }
    clock.now = target;
  };

  // jsdom does not implement the media element API: play() returns undefined
  // (not the promise the card chains .catch() onto) and play/pause/load report
  // "Not implemented" through the virtual console.
  const media = window.HTMLMediaElement.prototype;
  media.play = () => Promise.resolve();
  media.pause = () => {};
  media.load = () => {};
  media.canPlayType = () => "";

  // jsdom has no fullscreen API; the card calls it optionally from its toolbar.
  window.Element.prototype.requestFullscreen = () => Promise.resolve();
  // No test may reach the network: the status badge poll always gets a stub.
  window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ state: "live", fps: 5 }) });

  window.eval(CARD_SOURCE);

  const env = { window, document: window.document, clock, jsdomErrors };
  openEnvironments.push(env);
  return env;
}

/** A realistic `generic_video_proxy/stream_url` response. */
function mediaPayload(overrides = {}) {
  return {
    name: "Front Door",
    player: "mjpeg",
    stream_url: STREAM_URL,
    snapshot_url: SNAPSHOT_URL,
    media_url: MEDIA_URL,
    status_url: "/api/generic_video_proxy/status/camera.front_door",
    issued_at: 1,
    expires_in: 3600,
    ...overrides,
  };
}

/** A `hass` stub recording every websocket call. */
function hassStub(calls, { result, error } = {}) {
  return {
    states: {},
    callWS(message) {
      calls.push(message);
      if (error !== undefined) {
        return Promise.reject(error);
      }
      return Promise.resolve(typeof result === "function" ? result(message) : result);
    },
  };
}

/** Mount a configured card and wait for its first websocket round trip. */
async function mountCard(config, { payload = mediaPayload(), error } = {}) {
  const env = createEnvironment();
  const card = env.document.createElement(CARD_TAG);
  env.document.body.appendChild(card);
  card.setConfig(config);
  const calls = [];
  const hass = hassStub(calls, { result: payload, error });
  card.hass = hass;
  await flush();
  return { ...env, card, calls, hass };
}

const shadowImage = (card) => card.shadowRoot.querySelector("img");

describe("Generic Video Proxy card", () => {
  it("registers the card and its editor and advertises the card to Lovelace", () => {
    const { window } = createEnvironment();

    assert.equal(typeof window.customElements.get(CARD_TAG), "function");
    assert.equal(typeof window.customElements.get(EDITOR_TAG), "function");

    const card = window.document.createElement(CARD_TAG);
    assert.ok(card instanceof window.HTMLElement);
    assert.ok(card.shadowRoot, "the card attaches an open shadow root");
    assert.ok(
      window.document.createElement(EDITOR_TAG) instanceof window.HTMLElement,
      "the editor element can be instantiated"
    );

    const advertised = window.customCards.find((entry) => entry.type === CARD_TAG);
    assert.ok(advertised, "the card pushes itself into window.customCards");
    assert.deepEqual(plain(advertised), {
      type: CARD_TAG,
      name: "Generic Video Proxy",
      description: "Play a video or image stream proxied through Home Assistant.",
      preview: false,
      documentationURL: "https://github.com/dnviti/ha-generic-video-proxy",
    });
  });

  it("rejects configs with neither entity nor entry_id and accepts either", () => {
    const { document } = createEnvironment();
    const card = document.createElement(CARD_TAG);

    assert.throws(() => card.setConfig(), /entity/);
    assert.throws(() => card.setConfig({}), /entry_id/);
    assert.throws(() => card.setConfig({ title: "Front door" }), /entry_id/);

    assert.doesNotThrow(() => card.setConfig({ entity: ENTITY }));
    assert.doesNotThrow(() => card.setConfig({ entry_id: "entry-1" }));
  });

  it("asks the websocket for a stream URL by entity_id", async () => {
    const { card, calls, hass } = await mountCard({ entity: ENTITY });

    assert.deepEqual(plain(calls), [{ type: WS_STREAM_URL, entity_id: ENTITY }]);
    assert.equal(card.hass, hass, "the hass property returns what Home Assistant set");
  });

  it("asks the websocket for a stream URL by entry_id when configured that way", async () => {
    const { calls } = await mountCard({ entry_id: "entry-1" });

    assert.deepEqual(plain(calls), [{ type: WS_STREAM_URL, entry_id: "entry-1" }]);
  });

  it("renders an MJPEG <img> pointing at the signed stream URL", async () => {
    const env = await mountCard({ entity: ENTITY });
    const image = shadowImage(env.card);

    assert.ok(image, "an <img> is mounted in the shadow root");
    assert.equal(image.getAttribute("src"), `${STREAM_URL}&_gvp=${env.clock.now}`);
    assert.equal(image.alt, "Front Door", "the payload name is used as alt text");
    assert.equal(env.card.shadowRoot.querySelector("video"), null);
  });

  it("renders a <video> honouring muted, autoplay and controls from the config", async () => {
    const unmuted = await mountCard({ entity: ENTITY, player: "video", muted: false, controls: true }, {
      payload: mediaPayload({ player: "video" }),
    });
    const video = unmuted.card.shadowRoot.querySelector("video");

    assert.ok(video, "a <video> is mounted instead of the MJPEG <img>");
    assert.equal(video.muted, false, "muted: false leaves the video unmuted");
    assert.equal(video.autoplay, true);
    assert.equal(video.controls, true);
    assert.equal(video.getAttribute("src"), MEDIA_URL);

    const defaulted = await mountCard({ entity: ENTITY, player: "video" }, {
      payload: mediaPayload({ player: "video" }),
    });
    const mutedVideo = defaulted.card.shadowRoot.querySelector("video");
    assert.equal(mutedVideo.muted, true, "videos start muted unless muted: false is set");
    assert.equal(mutedVideo.controls, false);
  });

  it("re-requests the still image on the configured image_refresh_interval", async () => {
    const env = await mountCard(
      { entity: ENTITY, image_refresh_interval: 5 },
      { payload: mediaPayload({ player: "image" }) }
    );
    const image = shadowImage(env.card);

    assert.equal(image.getAttribute("src"), `${SNAPSHOT_URL}?_gvp=${env.clock.now}`);

    env.clock.advance(4_999);
    assert.equal(image.getAttribute("src"), `${SNAPSHOT_URL}?_gvp=${CLOCK_START}`, "not refreshed early");

    env.clock.advance(1);
    assert.equal(shadowImage(env.card), image, "the same <img> is refreshed, not re-mounted");
    assert.equal(image.getAttribute("src"), `${SNAPSHOT_URL}?_gvp=${CLOCK_START + 5_000}`);
  });

  it("surfaces a websocket rejection in the message element instead of throwing", async () => {
    const env = await mountCard({ entity: ENTITY }, { error: { code: "not_found" } });

    assert.equal(shadowImage(env.card), null, "no player is mounted after a failed resolve");
    const message = env.card.shadowRoot.querySelector(".message");
    assert.notEqual(message.style.display, "none", "the message element is shown");
    assert.ok(message.classList.contains("error"), "the message is flagged as an error");
  });

  it("shows the websocket error text when the rejection carries a message", async () => {
    const error = Object.assign(new Error("not_found"), { code: "not_found" });
    const env = await mountCard({ entity: ENTITY }, { error });

    const message = env.card.shadowRoot.querySelector(".message");
    assert.equal(message.style.display, "");
    assert.equal(message.textContent, "not_found");
  });

  it("re-mounts the MJPEG player with a fresh cache buster after an image error", async () => {
    const env = await mountCard({ entity: ENTITY });
    const first = shadowImage(env.card);

    first.dispatchEvent(new env.window.Event("error"));
    assert.match(env.card.shadowRoot.querySelector(".message").textContent, /Waiting for the stream/);

    env.clock.advance(1_000);

    const second = shadowImage(env.card);
    assert.ok(second, "a new <img> is mounted after the retry delay");
    assert.notEqual(second, first, "the failed element is replaced");
    assert.equal(second.getAttribute("src"), `${STREAM_URL}&_gvp=${CLOCK_START + 1_000}`);
  });

  it("renders ha-form in the editor with entry options from the list websocket", async () => {
    const env = createEnvironment();
    const editor = env.document.createElement(EDITOR_TAG);
    env.document.body.appendChild(editor);

    const calls = [];
    const entries = [{ entry_id: "entry-1", name: "Front Door", entity_id: ENTITY }];
    const hass = hassStub(calls, { result: { entries } });
    const config = { entity: ENTITY, title: "Front door" };

    editor.setConfig(config);
    editor.hass = hass;
    await flush();

    assert.deepEqual(plain(calls), [{ type: "generic_video_proxy/list" }]);

    const form = editor.shadowRoot.querySelector("ha-form");
    assert.ok(form, "ha-form is rendered in the editor shadow root");
    assert.equal(form.hass, hass);
    assert.deepEqual(plain(form.data), config);
    assert.deepEqual(
      plain(form.schema).map((field) => field.name),
      ["entity", "entry_id", "title", "", "", "", "image_refresh_interval", "hls_js_url"]
    );

    const entryField = form.schema.find((field) => field.name === "entry_id");
    assert.deepEqual(plain(entryField.selector.select.options), [
      { value: "entry-1", label: `Front Door (${ENTITY})` },
    ]);
    assert.equal(form.computeLabel({ name: "muted" }), "Start muted");

    const emitted = [];
    editor.addEventListener("config-changed", (event) => emitted.push(event.detail.config));
    form.dispatchEvent(
      new env.window.CustomEvent("value-changed", { detail: { value: { title: "Driveway" } } })
    );

    assert.deepEqual(plain(emitted), [{ ...config, title: "Driveway" }]);
  });
});
