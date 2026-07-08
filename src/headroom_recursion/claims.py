"""Claim auditing — the anti-fabrication layer for research-mode runs.

Live-run data: the moment a graded rubric paid for progress, the cheapest tier
fabricated citations in 4/4 steps, and a mid tier mislabeled known results as
novel. The judge caught both — but as pattern-recognition, not verification.
This module makes the two failure modes *mechanical*:

* **Citation firewall.** Every ``[KNOWN]`` claim's citations are looked up in the
  retrieval corpus. Unresolvable citations demote the claim to ``[UNSOURCED]``
  and the judge is told — a misattributed paper stops being a vibe and becomes
  a failed lookup.
* **Novelty triage.** Every ``[NEW]`` claim triggers a prior-art retrieval pass;
  hits are reported to the judge as candidate prior art. Honest limitation,
  stated everywhere it matters: NO retrieval hit is NOT proof of novelty —
  a surviving [NEW] means only "novel relative to this corpus".

Both checks are rung-4 (trust = the corpus's), which is why they inform the
judge rather than overrule it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_CLAIM_RE = re.compile(r"\[(KNOWN|NEW|FORMAL)\]", re.IGNORECASE)
# "Karp, R., Lipton, R. (1980)" / "Williams (2010)" / "Aaronson–Wigderson, 2008"
_CITATION_RE = re.compile(r"([A-Z][A-Za-zÀ-ɏ'’\-]+(?:[,\s]+[A-Z]\.)?(?:[\s,]+(?:and|&|–|-)?\s*[A-Z][A-Za-zÀ-ɏ'’\-]+)*)[\s,(]+\(?(\d{4})\)?")


@dataclass
class Claim:
    label: str                 # KNOWN | NEW | UNSOURCED
    statement: str             # first line of the claim text
    body: str                  # full claim text (until the next claim marker)
    citations: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)  # citations with no corpus hit
    prior_art: list[str] = field(default_factory=list)   # corpus hits against a NEW claim


def parse_claims(answer: str) -> list[Claim]:
    """Extract [KNOWN]/[NEW]-labeled claims from a research-mode answer."""

    marks = list(_CLAIM_RE.finditer(answer))
    claims: list[Claim] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else min(len(answer), m.end() + 4000)
        body = answer[m.end():end].strip()
        statement = body.splitlines()[0].strip(" *:.-") if body else ""
        citations = [f"{who.strip()} ({year})" for who, year in _CITATION_RE.findall(body)]
        claims.append(
            Claim(label=m.group(1).upper(), statement=statement[:200], body=body, citations=citations)
        )
    return claims


def audit_claims(
    claims: list[Claim],
    retriever,
    *,
    k: int = 3,
    max_snippet_chars: int = 240,
) -> list[Claim]:
    """Rung-4 pass: resolve citations for [KNOWN], hunt prior art for [NEW]."""

    if retriever is None:
        return claims
    for claim in claims:
        if claim.label == "KNOWN":
            for cite in claim.citations[:5]:
                if not _lookup(retriever, cite, k=k):
                    claim.unresolved.append(cite)
            if claim.citations and claim.unresolved:
                claim.label = "UNSOURCED"
        elif claim.label == "NEW":
            hits = _lookup(retriever, claim.statement, k=k)
            claim.prior_art = [h[:max_snippet_chars] for h in hits[:k]]
        # [FORMAL] claims are deliberately not audited here: their authority is
        # the Lean gate (rung 1), not the corpus (rung 4). A [FORMAL] label on a
        # block that doesn't compile is the GATE's mechanical rejection to make,
        # and the rubric prices false [FORMAL] labels at the fabrication cap.
    return claims


def judge_addendum(claims: list[Claim]) -> str:
    """One block for the judge: mechanical findings it must weigh, not re-derive."""

    if not claims:
        return ""
    lines = ["[CLAIM AUDIT — mechanical, rung 4]"]
    for i, c in enumerate(claims):
        if c.label == "UNSOURCED":
            lines.append(
                f"- claim {i} was [KNOWN] but these citations did NOT resolve in the corpus: "
                f"{'; '.join(c.unresolved)} — treat as unsourced, score accordingly"
            )
        elif c.label == "NEW" and c.prior_art:
            lines.append(
                f"- claim {i} is labeled [NEW] but the corpus contains candidate prior art: "
                f"{' | '.join(c.prior_art)} — verify the novelty label before crediting"
            )
        elif c.label == "NEW":
            lines.append(
                f"- claim {i} [NEW]: no corpus hit. NOTE: absence of a hit is NOT proof of "
                "novelty — credit at most 'novel relative to corpus'"
            )
    if len(lines) == 1:
        lines.append("- all [KNOWN] citations resolved; no [NEW] claims flagged")
    return "\n".join(lines)


def _lookup(retriever, query: str, *, k: int) -> list[str]:
    try:
        hits = retriever.retrieve(query[:300], k=k)
    except Exception:
        return []
    return [h for h in (hits or []) if h and h.strip()]
