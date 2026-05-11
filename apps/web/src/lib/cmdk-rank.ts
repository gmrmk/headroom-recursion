// cmd-K palette ranker -- ADR-0017 §4 (WI-0606 TS port).
//
// 1:1 mirror of packages/osint_goblin_schemas/.../cmdk_rank.py. The
// shared parity contract is tests/golden_path_e2e/cmdk_ranking_fixture.json;
// any change to scoring weights, thresholds, or structural rules MUST
// land in both files together or the Python test suite will diverge
// from the TS palette behavior.
//
// Scoring (calibrated against the fixture):
//   1. Prefix:       exact +1.0 / truncated +0.85 / partial +0.55
//   2. Name:         prefix +0.7 / substring +0.25 / subsequence +0.4
//   3. Subject:      exact +0.7 / substring +0.25 / subsequence +0.3
//   4. Context fit:  c.subject == ctx.subject -> +0.6
//   5. Kind bias:    workflow +0.25 / verb +0.10 (only when ctx absent)
//   6. Tool penalty: tools without context match -> -0.5
//   7. OPSEC:        biometric_red on face -0.3, captcha_amber -0.1
//
// Structural rules:
//   - Lucene-style operators (`^\\w+:`) -> []
//   - Empty query: warm=true -> []; warm=false -> [first workflow]
//   - Score floor 0.4
//   - Trim + lowercase

export interface RankCandidate {
  readonly id: string;
  readonly kind: string; // "workflow" | "verb" | "tool" | other
  readonly name: string;
  readonly prefix?: string;
  readonly subject?: string;
}

export interface RankContext {
  readonly subject?: string | null;
  readonly warm?: boolean;
  readonly opsec_state?: string;
}

export interface RankedCandidate extends RankCandidate {
  readonly score: number;
}

const LUCENE_OP_RE = /^\s*\w+:/;
const SCORE_FLOOR = 0.4;

function isSubsequence(q: string, target: string): boolean {
  let i = 0;
  for (const ch of target) {
    if (i < q.length && ch === q[i]) {
      i += 1;
      if (i === q.length) {
        return true;
      }
    }
  }
  return false;
}

function score(
  query: string,
  candidate: RankCandidate,
  context: RankContext | null,
): number {
  const name = (candidate.name ?? "").toLowerCase();
  const prefix = (candidate.prefix ?? "").toLowerCase();
  const subject = (candidate.subject ?? "").toLowerCase();
  const kind = (candidate.kind ?? "").toLowerCase();
  const ctxSubject = ((context?.subject ?? "") || "").toLowerCase();
  const opsec = ((context?.opsec_state ?? "green") || "green").toLowerCase();

  let s = 0.0;
  let matched = false;

  // 1+2. Prefix
  if (prefix && query === prefix) {
    s += 1.0;
    matched = true;
  } else if (prefix && query.startsWith(prefix)) {
    s += 0.85;
    matched = true;
  } else if (prefix && prefix.startsWith(query)) {
    s += 0.55;
    matched = true;
  }

  // Name
  if (name.startsWith(query)) {
    s += 0.7;
    matched = true;
  } else if (name.includes(query)) {
    s += 0.25;
    matched = true;
  } else if (query.length >= 2 && isSubsequence(query, name)) {
    s += 0.4;
    matched = true;
  }

  // 3. Subject
  if (subject && query === subject) {
    s += 0.7;
    matched = true;
  } else if (subject && subject.includes(query)) {
    s += 0.25;
    matched = true;
  } else if (subject && query.length >= 2 && isSubsequence(query, subject)) {
    s += 0.3;
    matched = true;
  }

  if (!matched) {
    return 0.0;
  }

  // 4. Context fit
  if (ctxSubject && subject === ctxSubject) {
    s += 0.6;
  }

  // 5. Kind bias (no context)
  if (!ctxSubject) {
    if (kind === "workflow") {
      s += 0.25;
    } else if (kind === "verb") {
      s += 0.10;
    }
  }

  // Tool penalty without context match
  if (kind === "tool" && (!ctxSubject || subject !== ctxSubject)) {
    s -= 0.5;
  }

  // 6. OPSEC penalty
  if (opsec === "biometric_red" && (subject === "face" || name.includes("face"))) {
    s -= 0.3;
  } else if (opsec === "captcha_amber" && name.includes("captcha")) {
    s -= 0.1;
  }

  return s;
}

export function rank(
  query: string,
  candidates: ReadonlyArray<RankCandidate>,
  context: RankContext | null = null,
): ReadonlyArray<RankedCandidate> {
  if (LUCENE_OP_RE.test(query ?? "")) {
    return [];
  }

  const q = (query ?? "").trim().toLowerCase();

  if (!q) {
    if (context?.warm) {
      return [];
    }
    for (const c of candidates) {
      if ((c.kind ?? "").toLowerCase() === "workflow") {
        return [{ ...c, score: 1.0 }];
      }
    }
    return [];
  }

  const scored: RankedCandidate[] = [];
  for (const c of candidates) {
    const s = score(q, c, context);
    if (s > SCORE_FLOOR) {
      scored.push({ ...c, score: s });
    }
  }
  scored.sort((a, b) => b.score - a.score);
  return scored;
}
