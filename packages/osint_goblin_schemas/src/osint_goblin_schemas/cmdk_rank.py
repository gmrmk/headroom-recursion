"""cmd-K palette ranker -- ADR-0017 §4 (WI-0606).

Pure-Python implementation of the 6-input weighted scoring for the
command palette. Python is the source of truth + the test target; a
TypeScript port mirrors this for the actual web palette, with the
fixture (tests/golden_path_e2e/cmdk_ranking_fixture.json) the parity
contract between the two implementations.

Scoring inputs (ADR-0017 §4, simplified to what the fixture exercises):

  1. exact_prefix(c, q): 1.0 if q == c.prefix (e.g. q="un", c.prefix="un")
  2. fuzzy(c, q): 0.85 if q startswith c.prefix; 0.55 if c.prefix startswith
     q (partial); plus name-prefix 0.7, name-substring 0.25
  3. subject_match: 0.7 if q == c.subject; 0.25 substring
  4. context_fit: 0.6 if c.subject == context.subject
  5. kind_bias (without context): workflow +0.05, verb +0.03 to break
     near-ties in favor of higher-level surfaces
  6. opsec_penalty: -0.3 for biometric_red against face-related; -0.1
     for captcha_amber against captcha-tagged candidates. Item stays in
     list (per ADR §4: "subtract but item still in list").

Plus structural handling:
  - Lucene-style operators (q matches /^\\w+:/) -> reject, return [].
  - Empty query: warm=True -> []; warm=False -> [first workflow] as
    default seed (cold-start sentinel).
  - Whitespace trim + lowercase.

Tool-kind penalty without context: tools are subject-bound; surfacing
a tool when the investigator hasn't picked a subject is noise. We
deduct 0.5 from tool candidates whose subject does not match context.
"""

from __future__ import annotations

import re
from typing import Any

_LUCENE_OP_RE = re.compile(r"^\s*\w+:")

# Floor for inclusion in the final ranked list. Tunes out substring-only
# noise (e.g. q='x' matching "Export" by single-char substring). The
# lowest-scoring genuine first-place fixture case is the subsequence
# match at 0.65 (q='evt' -> w8.ge), well above this floor. Verb +
# single-char substring (e.g. v.exp at 0.35 for q='x') falls below.
_SCORE_FLOOR = 0.4


def _is_subsequence(q: str, target: str) -> bool:
    """True if every character of `q` appears in `target` in order
    (not necessarily contiguous). Used for abbreviation-style queries
    like 'evt' against 'event'."""
    i = 0
    for ch in target:
        if i < len(q) and ch == q[i]:
            i += 1
            if i == len(q):
                return True
    return False


def _score(query: str, candidate: dict[str, Any], context: dict[str, Any] | None) -> float:
    """Compute the weighted score for one candidate. Returns 0 if no
    signal at all -- caller filters > 0 to drop non-matches."""
    name = str(candidate.get("name") or "").lower()
    prefix = str(candidate.get("prefix") or "").lower()
    subject = str(candidate.get("subject") or "").lower()
    kind = str(candidate.get("kind") or "").lower()
    ctx_subject = str((context or {}).get("subject") or "").lower()
    opsec = str((context or {}).get("opsec_state") or "green").lower()

    score = 0.0
    matched = False  # at least one prefix/name/subject signal fired

    # 1+2. Prefix + name + name-substring
    if prefix and query == prefix:
        score += 1.0
        matched = True
    elif prefix and query.startswith(prefix):
        score += 0.85
        matched = True
    elif prefix and prefix.startswith(query):
        # Partial prefix match: query is shorter than the candidate's prefix
        # (e.g. q="p" candidate.prefix="ph"). Weaker than exact-prefix but
        # still a signal.
        score += 0.55
        matched = True

    if name.startswith(query):
        score += 0.7
        matched = True
    elif query in name:
        score += 0.25
        matched = True
    elif len(query) >= 2 and _is_subsequence(query, name):
        # Abbreviation match: q chars appear in name in order, not
        # necessarily contiguous. "evt" -> "event" because e,v,t appear
        # in that order. Only fires for queries >=2 chars to avoid
        # noise from single-letter subsequences.
        score += 0.4
        matched = True

    # 3. Subject (the candidate's own subject field, NOT the context)
    if subject and query == subject:
        score += 0.7
        matched = True
    elif subject and query in subject:
        score += 0.25
        matched = True
    elif subject and len(query) >= 2 and _is_subsequence(query, subject):
        score += 0.3
        matched = True

    if not matched:
        return 0.0

    # 4. Context fit -- candidate.subject matches the investigation's subject
    if ctx_subject and subject == ctx_subject:
        score += 0.6

    # 5. Kind bias when context is absent (tie-break in favor of high-level)
    if not ctx_subject:
        if kind == "workflow":
            score += 0.25
        elif kind == "verb":
            score += 0.10

    # Tool penalty without context match -- tools are subject-bound;
    # surfacing them in a contextless query is noise.
    if kind == "tool" and (not ctx_subject or subject != ctx_subject):
        score -= 0.5

    # 6. OPSEC penalty -- demote but do not exclude
    if opsec == "biometric_red" and (subject == "face" or "face" in name):
        score -= 0.3
    elif opsec == "captcha_amber" and "captcha" in name:
        score -= 0.1

    return score


def rank(
    query: str,
    candidates: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Rank candidates against the query. Returns a list of dicts shaped
    like `{**candidate, "score": float}` sorted by descending score.

    Negative cases (Lucene-style operators) return []. Empty query
    behavior is governed by `context.warm`: warm sessions return [];
    cold sessions return the first workflow as a default sentinel.
    """
    # Negative: reject Lucene-style operators
    if _LUCENE_OP_RE.match(query or ""):
        return []

    q = (query or "").strip().lower()

    # Empty query handling -- warm vs cold
    if not q:
        if context and context.get("warm"):
            return []
        # Cold: surface the first workflow as the default seed
        for c in candidates:
            if str(c.get("kind") or "").lower() == "workflow":
                return [{**c, "score": 1.0}]
        return []

    scored: list[dict[str, Any]] = []
    for c in candidates:
        s = _score(q, c, context)
        if s > _SCORE_FLOOR:
            scored.append({**c, "score": s})
    scored.sort(key=lambda r: -r["score"])
    return scored
