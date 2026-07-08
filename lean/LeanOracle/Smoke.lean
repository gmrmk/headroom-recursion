import Mathlib

/-- The doctor's Mathlib-readiness smoke check: if this compiles under
`lake env lean`, the toolchain, Mathlib checkout, and olean cache all agree. -/
theorem smoke : 2 + 2 = 4 := by norm_num
