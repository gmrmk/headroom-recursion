import Mathlib

/-- Win-win skeleton of item 4. `InSigma2`, `NotInSize` are abstract; `hyp`
    is "NP ⊆ P/poly". Each branch (SAT hard, or the diagonal language L)
    exhibits a Σ2 language outside SIZE(n^k); the case split makes the
    existence unconditional. The complexity content is argued in prose. -/
theorem winwin {Lang : Type*} (InSigma2 NotInSize : Lang → Prop) (hyp : Prop)
    (case1 : ¬ hyp → ∃ L, InSigma2 L ∧ NotInSize L)
    (case2 :   hyp → ∃ L, InSigma2 L ∧ NotInSize L) :
    ∃ L, InSigma2 L ∧ NotInSize L := by
  by_cases h : hyp
  · exact case2 h
  · exact case1 h
