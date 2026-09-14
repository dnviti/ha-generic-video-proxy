"""Tests for signed media tokens."""

from __future__ import annotations

import time

import pytest

from custom_components.generic_video_proxy.security import (
    InvalidToken,
    MediaToken,
    TokenManager,
)
from custom_components.generic_video_proxy.signer import (
    InvalidSignature,
    PayloadSigner,
    is_safe_upstream_url,
    new_secret,
)

ENTRY = "entry-1"
UPSTREAM = "https://cam.local/hls/segment0.m4s?token=abc"


@pytest.fixture(name="tokens")
def tokens_fixture() -> TokenManager:
    """Return a token manager with a throwaway secret."""
    return TokenManager(PayloadSigner("unit-test-secret"), ttl=60)


class TestPayloadSigner:
    """The raw signing primitive."""

    def test_round_trip(self) -> None:
        signer = PayloadSigner("s3cret")
        token = signer.sign("hello world")
        assert signer.unsign(token) == "hello world"

    def test_signature_depends_on_the_secret(self) -> None:
        token = PayloadSigner("a").sign("payload")
        with pytest.raises(InvalidSignature):
            PayloadSigner("b").unsign(token)

    def test_tampered_payload_is_rejected(self) -> None:
        signer = PayloadSigner("s3cret")
        token = signer.sign("payload")
        payload, _, signature = token.partition(".")
        tampered = f"{'A' + payload[1:]}.{signature}"
        with pytest.raises(InvalidSignature):
            signer.unsign(tampered)

    @pytest.mark.parametrize("token", ["", ".", "no-dot", "abc.", ".abc"])
    def test_malformed_tokens(self, token: str) -> None:
        with pytest.raises(InvalidSignature):
            PayloadSigner("s").unsign(token)

    def test_empty_secret_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            PayloadSigner("")

    def test_secrets_are_random(self) -> None:
        assert new_secret() != new_secret()
        assert len(new_secret()) == 64


class TestSafeUpstreamUrl:
    """Only http(s) targets may be proxied."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://cam.local/video",
            "https://cam.local/video",
            "https://cam.local:8443/a/b?c=d",
        ],
    )
    def test_allowed(self, url: str) -> None:
        assert is_safe_upstream_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "file:///etc/passwd",
            "ftp://cam.local/x",
            "//cam.local/x",
            "/relative/path",
            "javascript:alert(1)",
            "http://",
        ],
    )
    def test_rejected(self, url: str) -> None:
        assert is_safe_upstream_url(url) is False


class TestTokenManager:
    """Issue and verify media tokens."""

    def test_round_trip_without_upstream(self, tokens: TokenManager) -> None:
        token = tokens.async_issue(entry_id=ENTRY, resource="stream.mjpeg")
        media = tokens.async_verify(token, entry_id=ENTRY)

        assert isinstance(media, MediaToken)
        assert media.entry_id == ENTRY
        assert media.resource == "stream.mjpeg"
        assert media.upstream is None
        assert media.expires > time.time()

    def test_round_trip_with_upstream(self, tokens: TokenManager) -> None:
        token = tokens.async_issue(
            entry_id=ENTRY, resource="segment", upstream=UPSTREAM, byte_range="bytes=0-10"
        )
        media = tokens.async_verify(token, entry_id=ENTRY)

        assert media.upstream == UPSTREAM
        assert media.byte_range == "bytes=0-10"

    def test_url_shape(self, tokens: TokenManager) -> None:
        url = tokens.async_url(entry_id=ENTRY, resource="stream.mjpeg")
        assert url.startswith(f"/api/generic_video_proxy/{ENTRY}/stream.mjpeg/")
        token = url.rsplit("/", 1)[1]
        assert tokens.async_verify(token, entry_id=ENTRY).resource == "stream.mjpeg"

    def test_token_is_bound_to_the_entry(self, tokens: TokenManager) -> None:
        token = tokens.async_issue(entry_id=ENTRY, resource="stream.mjpeg")
        with pytest.raises(InvalidToken):
            tokens.async_verify(token, entry_id="another-entry")

    def test_expired_token_is_rejected(self, tokens: TokenManager) -> None:
        token = tokens.async_issue(entry_id=ENTRY, resource="stream.mjpeg", ttl=-1)
        with pytest.raises(InvalidToken):
            tokens.async_verify(token, entry_id=ENTRY)

    def test_tampered_token_is_rejected(self, tokens: TokenManager) -> None:
        token = tokens.async_issue(entry_id=ENTRY, resource="stream.mjpeg")
        with pytest.raises(InvalidToken):
            tokens.async_verify(token[:-2] + "aa", entry_id=ENTRY)

    @pytest.mark.parametrize("token", [None, "", "nonsense", "a.b"])
    def test_invalid_tokens(self, tokens: TokenManager, token: str | None) -> None:
        with pytest.raises(InvalidToken):
            tokens.async_verify(token, entry_id=ENTRY)

    def test_unsafe_upstream_is_never_signed(self, tokens: TokenManager) -> None:
        with pytest.raises(InvalidToken):
            tokens.async_issue(entry_id=ENTRY, resource="segment", upstream="file:///etc/passwd")

    def test_unsafe_upstream_inside_a_payload_is_rejected(self, tokens: TokenManager) -> None:
        # A payload crafted outside of async_issue must still be refused.
        signer = tokens._signer
        forged = signer.sign(
            '{"e":"entry-1","r":"segment","u":"file:///etc/passwd","x":99999999999}'
        )
        with pytest.raises(InvalidToken):
            tokens.async_verify(forged, entry_id=ENTRY)
