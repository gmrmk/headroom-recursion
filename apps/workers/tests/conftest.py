"""Worker test fixtures.

Day 8 doesn't actually need a broker fixture — all tests call the actor's
`.fn` directly. The full broker round-trip (send -> worker -> ack) needs
a live Worker thread and is exercised in Day 9 against Memurai. Keeping
this file as a placeholder so pytest's collection discovers the test
directory cleanly.
"""

from __future__ import annotations
