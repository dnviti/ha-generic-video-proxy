"""Tests for HTTP digest authentication, against the published RFC vectors."""

from __future__ import annotations

import hashlib

import pytest

from custom_components.generic_video_proxy.digest import (
    DigestChallenge,
    DigestError,
    build_authorization,
)


class TestChallengeParsing:
    """WWW-Authenticate parsing."""

    def test_full_challenge(self) -> None:
        challenge = DigestChallenge.parse(
            'Digest realm="testrealm@host.com", qop="auth,auth-int", '
            'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", opaque="5ccc069c403ebaf9f0171e9517f40e41"'
        )

        assert challenge.realm == "testrealm@host.com"
        assert challenge.nonce == "dcd98b7102dd2f0e8b11d0f600bfb0c093"
        assert challenge.qop == "auth,auth-int"
        assert challenge.opaque == "5ccc069c403ebaf9f0171e9517f40e41"
        assert challenge.algorithm == "MD5"
        assert challenge.uses_qop_auth is True
        assert challenge.stale is False

    def test_quoted_value_containing_a_comma(self) -> None:
        challenge = DigestChallenge.parse('Digest realm="a,b", nonce="n"')
        assert challenge.realm == "a,b"
        assert challenge.qop is None
        assert challenge.uses_qop_auth is False

    def test_sess_and_stale(self) -> None:
        challenge = DigestChallenge.parse(
            'Digest realm="r", nonce="n", algorithm=MD5-sess, stale=true'
        )
        assert challenge.algorithm == "MD5-SESS"
        assert challenge.sess is True
        assert challenge.stale is True

    def test_sha256(self) -> None:
        challenge = DigestChallenge.parse('Digest realm="r", nonce="n", algorithm=SHA-256')
        assert challenge.algorithm == "SHA-256"

    def test_rejects_non_digest(self) -> None:
        with pytest.raises(DigestError):
            DigestChallenge.parse("Basic realm=whatever")

    def test_rejects_missing_nonce(self) -> None:
        with pytest.raises(DigestError):
            DigestChallenge.parse('Digest realm="r"')

    def test_rejects_unknown_algorithm(self) -> None:
        with pytest.raises(DigestError):
            DigestChallenge.parse('Digest realm="r", nonce="n", algorithm=SHA-512-256')


class TestReferenceVectors:
    """RFC 2617 section 3.5 and RFC 7616 section 3.9.1 examples."""

    def test_rfc2617_md5(self) -> None:
        challenge = DigestChallenge.parse(
            'Digest realm="testrealm@host.com", qop="auth,auth-int", '
            'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", '
            'opaque="5ccc069c403ebaf9f0171e9517f40e41"'
        )

        header = build_authorization(
            "GET",
            "/dir/index.html",
            "Mufasa",
            "Circle Of Life",
            challenge,
            nonce_count=1,
            cnonce="0a4f113b",
        )

        assert 'response="6629fae49393a05397450978507c4ef1"' in header
        assert "qop=auth" in header
        assert "nc=00000001" in header
        assert 'uri="/dir/index.html"' in header
        assert 'opaque="5ccc069c403ebaf9f0171e9517f40e41"' in header

    def test_rfc7616_sha256(self) -> None:
        challenge = DigestChallenge.parse(
            'Digest realm="http-auth@example.org", qop="auth", algorithm=SHA-256, '
            'nonce="7ypf/xlj9XXwfDPEoM4URrv/xwf94BcCAzFZH4GiTo0v", '
            'opaque="FQhe/qaU925kfnzjCev0ciny7QMkPqMAFRtzCUYo5tdS"'
        )

        header = build_authorization(
            "GET",
            "/dir/index.html",
            "Mufasa",
            "Circle of Life",
            challenge,
            nonce_count=1,
            cnonce="f2/wE4q74E6zIJEtWaHKaf5wv/H5QzzpXusqGemxURZJ",
        )

        assert (
            'response="753927fa0e85d155564e2e272a28d1802ca10daf4496794697cf8db5856cb6c1"' in header
        )
        assert "algorithm=SHA-256" in header


class TestDetails:
    """Behaviours that are easy to get subtly wrong."""

    def test_uri_keeps_the_query_string(self) -> None:
        challenge = DigestChallenge.parse('Digest realm="r", nonce="n", qop="auth"')
        header = build_authorization(
            "GET", "http://cam.local/video?x=1", "u", "p", challenge, cnonce="c"
        )
        assert 'uri="/video?x=1"' in header

    def test_qop_less_variant_omits_nc_and_cnonce(self) -> None:
        challenge = DigestChallenge.parse('Digest realm="r", nonce="n"')

        def md5(value: str) -> str:
            return hashlib.md5(value.encode()).hexdigest()

        ha1 = md5("u:r:p")
        ha2 = md5("GET:/x")
        header = build_authorization("GET", "/x", "u", "p", challenge, nonce_count=1, cnonce="c")

        assert f'response="{md5(f"{ha1}:n:{ha2}")}"' in header
        assert "nc=" not in header
        assert "cnonce=" not in header

    def test_sess_algorithm_hashes_the_session_key(self) -> None:
        challenge = DigestChallenge.parse('Digest realm="r", nonce="n", algorithm=MD5-sess')

        def md5(value: str) -> str:
            return hashlib.md5(value.encode()).hexdigest()

        cnonce = "cnonce-value"
        ha1 = md5(f"{md5('u:r:p')}:n:{cnonce}")
        ha2 = md5("GET:/x")
        header = build_authorization("GET", "/x", "u", "p", challenge, nonce_count=1, cnonce=cnonce)

        assert f'response="{md5(f"{ha1}:n:{ha2}")}"' in header

    def test_missing_username_is_rejected(self) -> None:
        challenge = DigestChallenge.parse('Digest realm="r", nonce="n"')
        with pytest.raises(DigestError):
            build_authorization("GET", "/x", "", "p", challenge)

    def test_nonce_count_is_padded(self) -> None:
        challenge = DigestChallenge.parse('Digest realm="r", nonce="n", qop="auth"')
        header = build_authorization("GET", "/x", "u", "p", challenge, nonce_count=26, cnonce="c")
        assert "nc=0000001a" in header
