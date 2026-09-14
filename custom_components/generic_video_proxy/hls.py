"""HLS playlist rewriting.

An HLS playlist is only useful to a browser when *every* URI inside it is
reachable from the browser. When Home Assistant proxies an upstream playlist,
each URI (variant playlists, media playlists, segments, encryption keys,
initialisation maps, preload hints) must be rewritten to a signed URL served by
this integration.

The rewriting itself is pure text manipulation so that it can be unit tested
without a running Home Assistant instance:

* relative URIs are first resolved against the URL the playlist was fetched
  from (:func:`urllib.parse.urljoin`);
* URI attributes inside tags such as ``#EXT-X-KEY`` are replaced in place;
* ``#EXT-X-BYTERANGE`` is passed through untouched, because the byte range is
  expressed with an HTTP ``Range`` request header by the player, which the
  segment proxy forwards upstream;
* every other line, including ``#EXT-X-PROGRAM-DATE-TIME`` and vendor tags, is
  preserved byte for byte.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urljoin

#: Tags that carry a ``URI="..."`` attribute.
_URI_ATTRIBUTE_RE = re.compile(r'(URI\s*=\s*")([^"]*)(")', re.IGNORECASE)
_URI_ATTRIBUTE_UNQUOTED_RE = re.compile(r"(URI\s*=\s*)([^,\s\"]+)", re.IGNORECASE)

#: ``#EXT-X-BYTERANGE:<length>[@<offset>]``
_BYTERANGE_RE = re.compile(r"^#EXT-X-BYTERANGE:\s*(\d+)(?:@(\d+))?\s*$", re.IGNORECASE)

#: Tags whose value line is a URI (already covered by the bare-URI rule); listed
#: for documentation purposes only.
PLAYLIST_HEADER = "#EXTM3U"

#: Signature of the callback used to route an upstream URI through the proxy.
RewriteCallback = Callable[[str, str | None], str]


def parse_byte_range(value: str) -> str | None:
    """Translate an ``#EXT-X-BYTERANGE`` value into a ``Range`` header value.

    Returns ``None`` when the value is not a byte range or when it omits the
    offset, in which case the player itself is responsible for tracking the
    offset and will send its own ``Range`` header.
    """
    match = _BYTERANGE_RE.match(value.strip())
    if match is None:
        return None
    length = int(match.group(1))
    offset = match.group(2)
    if offset is None or length <= 0:
        return None
    start = int(offset)
    return f"bytes={start}-{start + length - 1}"


def is_playlist(text: str) -> bool:
    """Return ``True`` when the payload looks like an HLS playlist."""
    return text.lstrip("\ufeff \t\r\n").startswith(PLAYLIST_HEADER)


def rewrite_playlist(
    text: str,
    playlist_url: str,
    rewrite: RewriteCallback,
) -> str:
    """Return ``text`` with every URI routed through ``rewrite``.

    ``rewrite`` receives an absolute upstream URL (and the byte range declared by
    a preceding ``#EXT-X-BYTERANGE`` tag, when there is one) and must return the
    public URL the browser should request instead.
    """
    out: list[str] = []
    pending_range: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            out.append(raw_line)
            continue
        if line.startswith("#"):
            if byte_range := parse_byte_range(line):
                pending_range = byte_range
            out.append(_rewrite_tag_uris(raw_line, playlist_url, rewrite))
            continue
        out.append(rewrite(urljoin(playlist_url, line), pending_range))
        pending_range = None
    trailing = "\n" if text.endswith(("\n", "\r")) else ""
    return "\n".join(out) + trailing


def _rewrite_tag_uris(
    line: str,
    playlist_url: str,
    rewrite: RewriteCallback,
) -> str:
    def replace_quoted(match: re.Match[str]) -> str:
        uri = match.group(2)
        if not uri:
            return match.group(0)
        return f"{match.group(1)}{rewrite(urljoin(playlist_url, uri), None)}{match.group(3)}"

    def replace_unquoted(match: re.Match[str]) -> str:
        uri = match.group(2)
        if not uri:
            return match.group(0)
        return f"{match.group(1)}{rewrite(urljoin(playlist_url, uri), None)}"

    if _URI_ATTRIBUTE_RE.search(line):
        return _URI_ATTRIBUTE_RE.sub(replace_quoted, line)
    return _URI_ATTRIBUTE_UNQUOTED_RE.sub(replace_unquoted, line)


def iter_uris(text: str, playlist_url: str) -> list[str]:
    """Return every absolute URI referenced by a playlist (mainly for tests)."""
    found: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            for match in _URI_ATTRIBUTE_RE.finditer(line):
                if match.group(2):
                    found.append(urljoin(playlist_url, match.group(2)))
            for match in _URI_ATTRIBUTE_UNQUOTED_RE.finditer(line):
                if match.group(2) and '="' not in match.group(0):
                    found.append(urljoin(playlist_url, match.group(2)))
            continue
        found.append(urljoin(playlist_url, line))
    return found
