# tasks/lessons.md — lessons captured per CLAUDE.md §self-improvement

The rule: after ANY correction, write down the pattern + a self-rule that prevents recurrence.

---

## 2026-05-11 — GitHub-first research is non-negotiable

**Context:** building an AI-image detector. I started writing hand-rolled heuristics immediately after the user asked. User interrupted: "wait, look on github."

**Self-rule:** for any new ML / detection / classification / parsing feature, the FIRST action is to dispatch a parallel GitHub-recon subagent. Don't write a single line of detection code until the recon returns with concrete repos + maintenance signals + license notes + a recommendation.

**Why:** CLAUDE.md `development-workflow.md §0 Research & Reuse`:
- "GitHub code search first"
- "Library docs second"
- "Search for adaptable implementations"

The cost of writing from scratch when a battle-tested repo exists is high (we end up with worse coverage AND maintenance debt). The cost of the recon subagent is ~1 minute.

**Trigger phrase:** if a user ask is "build a thing that does X classification / detection / parsing / scoring", the prompt for me reflexively becomes "what github project already does X?"

---

## 2026-05-11 — Plan-first is non-negotiable for non-trivial tasks

**Context:** same incident. User said "Lets align with the CLAUDE.md."

**Self-rule:** for any non-trivial task (3+ steps OR new adapter OR architectural decision), the FIRST artifact is `tasks/todos.md` with the plan. Verify the plan with the user, THEN implement.

**Why:** CLAUDE.md `WORKFLOW ORCHESTRATION §Plan mode default`:
- "Enter plan mode for ANY non-trivial task"
- "If something goes sideways, STOP and re-plan immediately"

User interrupting mid-build IS "something going sideways." The protocol is STOP → plan → verify → resume.

**Trigger phrase:** before writing code for a new adapter / feature / workflow, the prompt for me reflexively becomes "have I written the plan to tasks/todos.md yet?"

---

## 2026-05-11 — Privacy directives override persona recommendations

**Context:** Naomi (legal/compliance persona) recommended a per-query audit log so the investigator could prove single-target use. User overrode with "I want it logless. Anyones data fed through this should never be stored."

**Self-rule:** user's stated values about target data > any persona's defensive recommendation. When a persona says "log this for protection" and the user says "don't log," the user wins. Codify the user's choice in a binding policy doc + structural CI guard, so the persona's recommendation can't quietly creep back in via future refactors.

**Why:** the user's investigative ethics frame protecting *target data* as primary. Self-protection paper-trails are secondary. The right pattern is "redact, don't record" — match the spirit of the OSINT discipline.

**Trigger phrase:** when applying a persona's recommendation, the prompt for me reflexively becomes "does this conflict with any user-stated value? if yes, the user value wins."
