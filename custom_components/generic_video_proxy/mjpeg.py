"""Streaming parser for ``multipart/x-mixed-replace`` JPEG streams (MJPEG).

The parser is transport agnostic: feed it arbitrary byte chunks as they arrive
from the upstream socket and it returns the complete JPEG frames it recognised.
It is deliberately defensive, because real world MJPEG producers are sloppy:

* the boundary declared in ``Content-Type`` may or may not carry the leading
  ``--`` that the wire format uses (``boundary=--foo`` produces ``----foo``);
* parts may or may not declare ``Content-Length``;
* line endings may be CRLF or bare LF;
* the stream may start with arbitrary preamble bytes before the first boundary.

The parser never buffers an unbounded amount of data: a part that exceeds
``max_part_bytes`` raises :class:`MultipartError` so the caller can recycle the
upstream connection instead of exhausting memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Hard ceiling for a single part. Reference MJPEG cameras stay far below this.
DEFAULT_MAX_PART_BYTES = 8 * 1024 * 1024

#: Cap for the header block that precedes a part body.
_MAX_HEADER_BYTES = 8 * 1024

_BOUNDARY_RE = re.compile(r'boundary\s*=\s*(?:"([^"]+)"|([^;\s]+))', re.IGNORECASE)


class MultipartError(Exception):
    """Raised when the upstream stream cannot be parsed safely."""


@dataclass(frozen=True, slots=True)
class Part:
    """A single parsed multipart part."""

    data: bytes
    content_type: str | None = None
    headers: dict[str, str] | None = None


def parse_boundary(content_type: str | None) -> str | None:
    """Extract the multipart boundary from a ``Content-Type`` header value."""
    if not content_type:
        return None
    match = _BOUNDARY_RE.search(content_type)
    if match is None:
        return None
    return match.group(1) or match.group(2)


def _split_headers(block: bytes) -> tuple[dict[str, str], str | None]:
    headers: dict[str, str] = {}
    content_type: str | None = None
    for raw_line in block.replace(b"\r\n", b"\n").split(b"\n"):
        if b":" not in raw_line:
            continue
        name, _, value = raw_line.partition(b":")
        key = name.decode("latin-1").strip().lower()
        decoded = value.decode("latin-1").strip()
        headers[key] = decoded
        if key == "content-type":
            content_type = decoded
    return headers, content_type


class MultipartFrameParser:
    """Incremental parser producing JPEG frames from a multipart stream."""

    def __init__(
        self,
        boundary: str,
        *,
        max_part_bytes: int = DEFAULT_MAX_PART_BYTES,
        require_jpeg: bool = True,
    ) -> None:
        """Initialise the parser for the given boundary token."""
        if not boundary:
            raise ValueError("boundary must not be empty")
        self.boundary = boundary
        self.delimiter = b"--" + boundary.encode("latin-1")
        self.max_part_bytes = max_part_bytes
        self.require_jpeg = require_jpeg
        self._buffer = bytearray()
        self._state = "preamble"
        self._part_headers: dict[str, str] = {}
        self._part_content_type: str | None = None
        self._part_remaining: int | None = None
        #: Offset from which the next delimiter search may start. Parts that
        #: declare no Content-Length must be buffered in full, so the search is
        #: resumed instead of re-scanning everything already inspected.
        self._body_scan_from = 0
        self.frames_parsed = 0

    # ------------------------------------------------------------------ public

    def feed(self, chunk: bytes) -> list[bytes]:
        """Feed a chunk of upstream bytes and return every complete frame."""
        frames: list[bytes] = []
        self._buffer.extend(chunk)

        while True:
            if self._state == "preamble":
                index = self._buffer.find(self.delimiter)
                if index < 0:
                    self._trim_preamble()
                    break
                del self._buffer[: index + len(self.delimiter)]
                self._state = "headers"
                continue

            if self._state == "headers":
                end = self._find_header_end()
                if end is None:
                    if len(self._buffer) > _MAX_HEADER_BYTES:
                        raise MultipartError("multipart part header block is too large")
                    break
                block = bytes(self._buffer[:end])
                del self._buffer[: end + len(self._header_terminator())]
                self._part_headers, self._part_content_type = _split_headers(block)
                length = self._part_headers.get("content-length")
                if length is not None and length.isdigit() and int(length) <= self.max_part_bytes:
                    self._part_remaining = int(length)
                    self._state = "body_length"
                else:
                    if length is not None and length.isdigit():
                        raise MultipartError(
                            "multipart part declares a Content-Length above the limit"
                        )
                    self._part_remaining = None
                    self._body_scan_from = 0
                    self._state = "body_until_boundary"
                continue

            if self._state == "body_length":
                assert self._part_remaining is not None
                if len(self._buffer) < self._part_remaining:
                    break
                data = bytes(self._buffer[: self._part_remaining])
                del self._buffer[: self._part_remaining]
                self._part_remaining = None
                self._emit(data, frames)
                self._state = "preamble"
                continue

            if self._state == "body_until_boundary":
                index = self._buffer.find(self.delimiter, self._body_scan_from)
                if index < 0:
                    if len(self._buffer) > self.max_part_bytes:
                        raise MultipartError("multipart part exceeded the maximum part size")
                    # A delimiter may still be split across two chunks, so the
                    # search restarts early enough to catch that overlap.
                    self._body_scan_from = max(0, len(self._buffer) - len(self.delimiter) + 1)
                    break
                data = bytes(self._buffer[:index])
                del self._buffer[: index + len(self.delimiter)]
                self._body_scan_from = 0
                self._emit(_rstrip_eol(data), frames)
                self._state = "headers"
                continue

        return frames

    def flush(self) -> list[bytes]:
        """Return any final frame still buffered when the stream ends."""
        frames: list[bytes] = []
        if self._state in ("body_length", "body_until_boundary") and self._buffer:
            self._emit(_rstrip_eol(bytes(self._buffer)), frames)
        self._buffer.clear()
        self._state = "preamble"
        return frames

    # ----------------------------------------------------------------- private

    def _emit(self, data: bytes, frames: list[bytes]) -> None:
        if not data:
            return
        if self.require_jpeg and not _looks_like_jpeg(data):
            return
        self.frames_parsed += 1
        frames.append(data)

    def _header_terminator(self) -> bytes:
        if self._buffer.find(b"\r\n\r\n") == self._buffer.find(b"\n\n") == -1:
            return b"\r\n\r\n"
        if self._buffer.find(b"\r\n\r\n") != -1:
            return b"\r\n\r\n"
        return b"\n\n"

    def _find_header_end(self) -> int | None:
        crlf = self._buffer.find(b"\r\n\r\n")
        lf = self._buffer.find(b"\n\n")
        candidates = [value for value in (crlf, lf) if value >= 0]
        if not candidates:
            return None
        return min(candidates)

    def _trim_preamble(self) -> None:
        # Retain only as much as could still become a delimiter.
        keep = len(self.delimiter) + 8
        if len(self._buffer) > keep:
            del self._buffer[: len(self._buffer) - keep]


def _rstrip_eol(data: bytes) -> bytes:
    if data.endswith(b"\r\n"):
        return data[:-2]
    if data.endswith(b"\n"):
        return data[:-1]
    return data


def _looks_like_jpeg(data: bytes) -> bool:
    return data.startswith(b"\xff\xd8")
