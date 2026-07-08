# Credits & Acknowledgments

This project is an *integration*. Almost everything that gives it force was built
by other people, offered under permissive licenses precisely so it could be built
upon — and the honest thing, the thing this repo's whole doctrine is about, is to
say so plainly and keep their names attached.

## The idea it ports

- **Alexia Jolicoeur-Martineau** — *"Less is More: Recursive Reasoning with Tiny
  Networks"* ([arXiv:2510.04871](https://arxiv.org/abs/2510.04871)). The recursive
  draft→critique→rewrite loop this whole harness is a port of. None of this exists
  without that paper.

## The verification stack (what makes any claim here trustworthy)

- **Lean 4** — Leonardo de Moura and the [Lean FRO](https://lean-lang.org) / the
  `leanprover` community. The kernel is the one thing in this project trusted with
  zero reservations.
- **Mathlib** — the [`leanprover-community/mathlib4`](https://github.com/leanprover-community/mathlib4)
  community: hundreds of contributors over years of work.
- **`nanoda_lib`** — [ammkrn](https://github.com/ammkrn/nanoda_lib): the independent
  Rust reimplementation of the Lean kernel that re-checks our proofs. Independence
  is the entire point of using it.
- **`lean4export`, `comparator`, `leanchecker`** — the [Lean FRO / `leanprover`](https://github.com/leanprover)
  org. `comparator` was built for the AIMO prize's adversarial-proof setting, which
  is almost exactly ours.

## Retrieval, compression, and grounding

- **LightRAG** — [HKUDS](https://github.com/HKUDS/LightRAG).
- **Headroom** — [headroomlabs-ai](https://github.com/headroomlabs-ai/headroom).

## The mathematics it reasons over

The complexity-theory corpus (`examples/complexity-bibliography.txt`) rests on the
results of, among many others: Stephen Cook, Leonid Levin, Richard Karp, Baker–Gill–
Solovay, Ravi Kannan, Karp–Lipton, Razborov–Rudich, Aaronson–Wigderson, and Ryan
Williams. Every `[KNOWN]` claim in a run is a restatement of their work, and the
citation firewall exists specifically to keep that attribution honest.

## Contributors to this repository

- **Jonah Butterbaugh** (`gmrmk`) — author and director of the repository. Set its
  goals and its guardrails, and insisted throughout that the system stay honest and
  that everyone who built its parts be credited. Every design decision and every
  judgment call about what to build and how far to push it was his.
- **Claude** (Anthropic, via Claude Code) — AI assistant; co-author on the commits
  where it contributed (see the `Co-Authored-By` trailers throughout the history).
  The outputs it produced are the repository owner's under the terms of use; it
  claims no ownership. It is credited here for transparency about how the work was
  made, not to assert authorship in any legal or moral sense.
- *(Add any other tools or people — e.g. an assistant used on earlier history —
  that genuinely contributed. This line is left open rather than filled in with a
  guess: fabricated credit would betray the very honesty this file is for.)*

## Licenses

This repository is MIT-licensed. Its dependencies carry their own (largely MIT /
Apache-2.0) licenses, which permit use — including commercial use — on the condition
that attribution is preserved. Honoring that condition is not a burden this project
tolerates; it is the ethic it is built on. If you use, extend, or build a product
around this work, keep this file intact and add your own line to it.
