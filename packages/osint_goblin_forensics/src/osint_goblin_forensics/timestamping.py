"""RFC3161 trusted-timestamp client.

freetsa.org default per Camille phase3/05 sec.5. Three-TSA fan-out shape
(also-supported: digicert.com TSA, sectigo.com TSA) lives in WI-0202 worker
config -- this module is the primitive for ONE TSA call.

Uses urllib (stdlib) so verify.py can timestamp-verify offline-ish without
adding requests/httpx as a dep.

Per Camille phase5/spikes/rfc3161_smoke.py: pass = HTTP 200 + content-type
application/timestamp-reply + leading 0x30 DER tag.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass

FREETSA_URL: str = "https://freetsa.org/tsr"
DIGICERT_URL: str = "http://timestamp.digicert.com"
SECTIGO_URL: str = "http://timestamp.sectigo.com"

DEFAULT_TSA_CHAIN: tuple[str, ...] = (FREETSA_URL, DIGICERT_URL, SECTIGO_URL)


class RFC3161Error(RuntimeError):
    """Timestamp request failed."""


@dataclass(frozen=True, slots=True)
class TimestampedResult:
    """Result from a successful TSA call."""

    tsa_url: str
    tsr_der: bytes  # raw DER-encoded TimeStampResp
    content_type: str
    nonce: int  # echoed back by TSA in the response


def _build_tsq(sha256_digest: bytes, nonce: int | None = None) -> bytes:
    """Build a minimal RFC3161 TimeStampReq (DER-encoded ASN.1).

    Hand-encoded so we don't pull in pyasn1 for this single primitive.
    Structure (RFC3161 sec.2.4.1):

      TimeStampReq ::= SEQUENCE {
        version              INTEGER  { v1(1) },
        messageImprint       MessageImprint,
        nonce                INTEGER OPTIONAL,
        certReq              BOOLEAN DEFAULT FALSE
      }
      MessageImprint ::= SEQUENCE {
        hashAlgorithm AlgorithmIdentifier,  -- SHA-256 OID: 2.16.840.1.101.3.4.2.1
        hashedMessage OCTET STRING
      }
    """
    if len(sha256_digest) != 32:
        raise RFC3161Error(f"expected 32-byte SHA-256 digest, got {len(sha256_digest)}")
    # SHA-256 OID DER: 06 09 60 86 48 01 65 03 04 02 01
    sha256_oid = bytes([0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01])
    # AlgorithmIdentifier SEQUENCE { OID, NULL }
    algo_id_inner = sha256_oid + bytes([0x05, 0x00])
    algo_id = bytes([0x30, len(algo_id_inner)]) + algo_id_inner
    # OCTET STRING wrapping digest
    digest_oct = bytes([0x04, 0x20]) + sha256_digest
    # MessageImprint SEQUENCE { algo_id, digest_oct }
    msg_imprint_inner = algo_id + digest_oct
    msg_imprint = bytes([0x30, len(msg_imprint_inner)]) + msg_imprint_inner
    # version INTEGER 1
    version = bytes([0x02, 0x01, 0x01])
    # Optional nonce
    nonce_bytes = b""
    if nonce is not None:
        nb = nonce.to_bytes((nonce.bit_length() + 7) // 8 or 1, "big", signed=False)
        # Add leading zero if high bit set (DER for positive INTEGER)
        if nb[0] & 0x80:
            nb = b"\x00" + nb
        nonce_bytes = bytes([0x02, len(nb)]) + nb
    # Outer SEQUENCE
    inner = version + msg_imprint + nonce_bytes
    return bytes([0x30, len(inner)]) + inner


def timestamp(
    sha256_digest: bytes,
    *,
    tsa_url: str = FREETSA_URL,
    nonce: int | None = None,
    timeout_s: float = 10.0,
) -> TimestampedResult:
    """POST a SHA-256 digest to a TSA and return the TimeStampResp.

    Raises RFC3161Error on any failure (network, non-200, wrong content-type,
    malformed DER).
    """
    if nonce is None:
        nonce = int.from_bytes(os.urandom(8), "big")
    tsq = _build_tsq(sha256_digest, nonce=nonce)
    req = urllib.request.Request(
        tsa_url,
        data=tsq,
        headers={"Content-Type": "application/timestamp-query"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read()
    except urllib.error.URLError as e:
        raise RFC3161Error(f"network error from {tsa_url}: {e}") from e
    except OSError as e:
        raise RFC3161Error(f"OS error contacting {tsa_url}: {e}") from e
    if status != 200:
        raise RFC3161Error(f"TSA {tsa_url} returned HTTP {status}")
    if "timestamp-reply" not in content_type:
        raise RFC3161Error(f"TSA {tsa_url} returned wrong content-type: {content_type!r}")
    if not body or body[0] != 0x30:  # leading DER SEQUENCE tag
        raise RFC3161Error(f"TSA {tsa_url} body not DER (leading byte 0x{body[0]:02x} if any)")
    return TimestampedResult(tsa_url=tsa_url, tsr_der=body, content_type=content_type, nonce=nonce)
