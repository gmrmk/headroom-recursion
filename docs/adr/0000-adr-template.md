# ADR-NNNN: Short imperative title

- **Status:** proposed | accepted | superseded by ADR-MMMM | deprecated
- **Date:** YYYY-MM-DD
- **Deciders:** @handle, @handle, persona-role
- **Tags:** stack, security, ux, data, license, etc.

## Context

What forces are in play? What constraints (technical, legal, organizational) and what observations (empirical, market, user-research) brought us here? Two to four paragraphs. Cite primary sources by filename and line, regulation by article, papers by URL.

## Decision

We will do X. State it as an imperative. One paragraph, sometimes a short bulleted list of the load-bearing sub-decisions. The reader should be able to point to a code change and say "this is the decision."

## Alternatives considered

The options we weighed and chose against, with the one-line reason each was rejected. Two to five entries. This section is non-optional even when the alternative looks obvious in hindsight — six months from now the obviousness is gone and a future reader will ask "did they consider …?" Naming the rejected paths short-circuits that question and surfaces the actual tradeoff.

- **Alternative 1:** … *Rejected because:* …
- **Alternative 2:** … *Rejected because:* …
- (Do not list straw-man alternatives. Each entry should be a thing a competent peer would actually have proposed.)

## Consequences

What becomes easier, what becomes harder, and what risks we accept. Three to six bullets, split into Positive / Negative / Neutral when it helps.

- **Positive:** …
- **Negative:** …
- **Neutral:** … (often: future-decision criteria are now set)

## Gates that re-open this

Future-state conditions that should trigger revisiting this decision. Each gate is one sentence: a measurable condition + the action that follows. If we cannot name any gate, the decision is either trivial (don't bother with an ADR) or our reasoning is too shallow to defend later. Two to four gates is typical.

- **Gate 1:** When/if … then revisit … (e.g., "When external distribution intent is declared, revisit the AGPL §13 distribution-trigger posture in this ADR")
- **Gate 2:** When measurement X exceeds threshold Y, revisit the choice between A and B.
- (Gates can be time-based — "revisit at M2 entry" — or condition-based — "revisit if the live corpus passes 10k entities".)

## References

- INTEGRATION-SPEC §N or CONSOLIDATED-ROADMAP §N
- Other ADRs (predecessors / supersessions / sibling decisions)
- Primary regulatory text or paper URL
- Empirical evidence file (e.g. `empirical/01-scrapling-smoke.md`)

---

**Style rules.** Two pages maximum (≈800–1000 words; the two new sections raise the cap slightly). Status changes are appended (Date + new status). Supersession is by writing a new ADR that names this one in its Status field; this file is not deleted. Sections **Context · Decision · Alternatives considered · Consequences · Gates that re-open this · References** are not optional. Aldous proposed the "Alternatives considered" and "Gates that re-open this" additions in phase6 Q4 + Q10 (2026-05-11); ratified by user 2026-05-11.
