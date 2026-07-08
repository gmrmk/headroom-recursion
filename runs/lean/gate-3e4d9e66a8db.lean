import Mathlib

/-- Propositional skeleton of the Karp–Lipton win–win used in ingredient (iii).
`NPeasy`, `PHcollapse`, `HardLangExists` stand for "NP ⊆ SIZE(n^k)",
"PH collapses to Σ2^p", and "a language in Σ2^p ∩ Π2^p outside SIZE(n^k)
exists", respectively. `case2` packages Karp–Lipton (item 3) as a black-box
hypothesis; `case2'` packages "collapse + the Σ3^p witness from (ii) gives
the conclusion"; `case1` packages "NP itself already witnesses the conclusion
directly". Neither Karp–Lipton nor the Σ3^p witness construction is re-proved
here — only the case-split logic combining them. -/
theorem karp_lipton_case_split
    (NPeasy PHcollapse HardLangExists : Prop)
    (case2 : NPeasy → PHcollapse)
    (case2' : PHcollapse → HardLangExists)
    (case1 : ¬ NPeasy → HardLangExists) :
    HardLangExists := by
  by_cases h : NPeasy
  · exact case2' (case2 h)
  · exact case1 h
