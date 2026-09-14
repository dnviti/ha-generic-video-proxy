# Third party components

This repository vendors one third party browser library so that the bundled
Lovelace card works on an installation without internet access.

## hls.js

- **File:** `custom_components/generic_video_proxy/www/hls.light.min.js`
- **Version:** 1.7.3 (light build, minified)
- **Source:** <https://cdn.jsdelivr.net/npm/hls.js@1.7.3/dist/hls.light.min.js>
- **Upstream project:** <https://github.com/video-dev/hls.js>
- **License:** Apache License 2.0 (<https://www.apache.org/licenses/LICENSE-2.0>)

The file is included unmodified. It is loaded by the card only when an HLS
source is played in a browser without native HLS support (Chrome, Firefox,
Edge); Safari plays HLS natively and never downloads it. Set the card option
`hls_js_url` to load hls.js from another location instead.

To update it:

```shell
curl -L -o custom_components/generic_video_proxy/www/hls.light.min.js \
  https://cdn.jsdelivr.net/npm/hls.js@<version>/dist/hls.light.min.js
node --check custom_components/generic_video_proxy/www/hls.light.min.js
```

then update the version in this file.
