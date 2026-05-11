"""Unit tests for osint_goblin_forensics.timestamping (RFC3161).

The real-network call to freetsa.org is marked @pytest.mark.real_network
(skipped by default; run via `pytest -m real_network` weekly per Yuki spec).
"""

from __future__ import annotations

import hashlib

import pytest
from osint_goblin_forensics.timestamping import (
    DEFAULT_TSA_CHAIN,
    FREETSA_URL,
    RFC3161Error,
    _build_tsq,
    timestamp,
)


def test_build_tsq_starts_with_der_sequence_tag():
    digest = hashlib.sha256(b"hello").digest()
    tsq = _build_tsq(digest)
    assert tsq[0] == 0x30  # DER SEQUENCE
    assert digest in tsq  # digest is present in the encoded request


def test_build_tsq_rejects_wrong_digest_length():
    with pytest.raises(RFC3161Error):
        _build_tsq(b"\x00" * 16)  # not 32 bytes


def test_build_tsq_with_nonce_embeds_value():
    digest = hashlib.sha256(b"hello").digest()
    tsq = _build_tsq(digest, nonce=0x12345678)
    # nonce bytes 0x12 0x34 0x56 0x78 should appear contiguously
    assert b"\x12\x34\x56\x78" in tsq


def test_default_tsa_chain_has_three():
    """Camille's three-TSA fan-out (phase5/spike-results/rfc3161/) requires 3 endpoints."""
    assert len(DEFAULT_TSA_CHAIN) == 3
    assert FREETSA_URL in DEFAULT_TSA_CHAIN


@pytest.mark.real_network
def test_freetsa_live_roundtrip():
    """Live test against freetsa.org. Marked real_network -- skipped by default."""
    digest = hashlib.sha256(b"osint goblin sprint 1 day 7 test").digest()
    result = timestamp(digest, tsa_url=FREETSA_URL)
    assert result.tsr_der[0] == 0x30
    assert "timestamp-reply" in result.content_type
    assert result.tsa_url == FREETSA_URL
