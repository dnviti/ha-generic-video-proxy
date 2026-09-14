"""Tests for the multipart MJPEG frame parser."""

from __future__ import annotations

import pytest

from custom_components.generic_video_proxy.mjpeg import (
    MultipartError,
    MultipartFrameParser,
    parse_boundary,
)

FRAME_A = b"\xff\xd8frame-a\xff\xd9"
FRAME_B = b"\xff\xd8frame-b\xff\xd9"
FRAME_C = b"\xff\xd8frame-c\xff\xd9"


def part(boundary: bytes, payload: bytes, *, content_length: bool = True) -> bytes:
    """Build one multipart part exactly like a camera would send it."""
    headers = (
        b"--"
        + boundary
        + b"\r\nContent-Type: image/jpeg\r\n"
        + (f"Content-Length: {len(payload)}\r\n".encode() if content_length else b"")
        + b"\r\n"
    )
    return headers + payload + b"\r\n"


class TestParseBoundary:
    """Boundary extraction from a Content-Type header."""

    def test_plain(self) -> None:
        assert parse_boundary("multipart/x-mixed-replace; boundary=frame") == "frame"

    def test_dashes_are_kept(self) -> None:
        # Cameras that report "boundary=--foo" send "----foo" on the wire.
        assert parse_boundary("multipart/x-mixed-replace; boundary=--foo") == "--foo"

    def test_quoted(self) -> None:
        assert parse_boundary('multipart/x-mixed-replace;boundary="my;boundary"') == "my;boundary"

    def test_missing(self) -> None:
        assert parse_boundary("multipart/x-mixed-replace") is None
        assert parse_boundary(None) is None


class TestMultipartFrameParser:
    """Incremental parsing of MJPEG streams."""

    def test_two_parts_one_chunk(self) -> None:
        parser = MultipartFrameParser("--foo")
        stream = part(b"----foo", FRAME_A) + part(b"----foo", FRAME_B)
        assert parser.feed(stream) == [FRAME_A, FRAME_B]
        assert parser.frames_parsed == 2

    def test_split_across_chunks(self) -> None:
        parser = MultipartFrameParser("frame")
        stream = part(b"frame", FRAME_A) + part(b"frame", FRAME_B)
        frames = []
        for index in range(len(stream)):
            frames.extend(parser.feed(stream[index : index + 1]))
        assert frames == [FRAME_A, FRAME_B]

    def test_without_content_length(self) -> None:
        parser = MultipartFrameParser("frame")
        stream = (
            part(b"frame", FRAME_A, content_length=False)
            + part(b"frame", FRAME_B, content_length=False)
            + b"--frame--\r\n"
        )
        assert parser.feed(stream) == [FRAME_A, FRAME_B]

    def test_last_part_without_a_closing_boundary_needs_flush(self) -> None:
        parser = MultipartFrameParser("frame")
        stream = part(b"frame", FRAME_A, content_length=False)
        assert parser.feed(stream) == []
        assert parser.flush() == [FRAME_A]

    def test_lf_only_line_endings(self) -> None:
        parser = MultipartFrameParser("frame")
        stream = (
            b"--frame\nContent-Type: image/jpeg\n\n"
            + FRAME_A
            + b"\n--frame\nContent-Type: image/jpeg\n\n"
            + FRAME_B
            + b"\n"
        )
        assert parser.feed(stream) == [FRAME_A]
        assert parser.flush() == [FRAME_B]

    def test_preamble_is_ignored(self) -> None:
        parser = MultipartFrameParser("frame")
        stream = b"garbage that a camera sends before the first boundary\r\n" + part(
            b"frame", FRAME_A
        )
        assert parser.feed(stream) == [FRAME_A]

    def test_trailing_crlf_is_not_part_of_the_frame(self) -> None:
        parser = MultipartFrameParser("frame")
        (frame,) = parser.feed(part(b"frame", FRAME_A))
        assert frame == FRAME_A
        assert not frame.endswith(b"\r\n")

    def test_non_jpeg_parts_are_skipped_by_default(self) -> None:
        parser = MultipartFrameParser("frame")
        stream = part(b"frame", b"<html>error</html>") + part(b"frame", FRAME_A)
        assert parser.feed(stream) == [FRAME_A]

    def test_non_jpeg_parts_can_be_kept(self) -> None:
        parser = MultipartFrameParser("frame", require_jpeg=False)
        stream = part(b"frame", b"whatever")
        assert parser.feed(stream) == [b"whatever"]

    def test_plain_image_body_is_treated_as_a_single_frame(self) -> None:
        # A camera that answers with a bare JPEG, wrapped in a multipart header.
        parser = MultipartFrameParser("frame")
        stream = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + FRAME_C + b"\r\n--frame--\r\n"
        assert parser.feed(stream) == [FRAME_C]

    def test_flush_returns_the_last_frame(self) -> None:
        parser = MultipartFrameParser("frame", require_jpeg=False)
        assert parser.feed(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n") == []
        assert parser.feed(b"tail-without-boundary") == []
        assert parser.flush() == [b"tail-without-boundary"]

    def test_oversized_part_raises(self) -> None:
        parser = MultipartFrameParser("frame", max_part_bytes=64)
        with pytest.raises(MultipartError):
            parser.feed(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + b"x" * 4096)

    def test_lying_content_length_raises(self) -> None:
        parser = MultipartFrameParser("frame", max_part_bytes=64)
        with pytest.raises(MultipartError):
            parser.feed(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 999999\r\n\r\n")

    def test_empty_boundary_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            MultipartFrameParser("")

    def test_boundary_spanning_chunks(self) -> None:
        parser = MultipartFrameParser("frame")
        stream = part(b"frame", FRAME_A) + part(b"frame", FRAME_B)
        # Feed in chunks that deliberately cut the delimiter in half.
        frames = []
        for index in range(0, len(stream), 7):
            frames.extend(parser.feed(stream[index : index + 7]))
        assert frames == [FRAME_A, FRAME_B]

    def test_large_frame_without_content_length_survives_chunking(self) -> None:
        payload = b"\xff\xd8" + bytes(range(256)) * 800 + b"\xff\xd9"
        parser = MultipartFrameParser("frame")
        stream = part(b"frame", payload, content_length=False) + part(b"frame", FRAME_B)
        frames = []
        for index in range(0, len(stream), 1000):
            frames.extend(parser.feed(stream[index : index + 1000]))
        assert frames == [payload, FRAME_B]
