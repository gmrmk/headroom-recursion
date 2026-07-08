import Mathlib

/-- If `C` differs from every `d ∈ D` at two or more points, then flipping
    `C` at any single `x0` still differs from every `d` somewhere, and its
    value at `x0` is complemented. The "robust witness" step of item 5. -/
theorem flip_preserves_difference
    {X : Type*} [DecidableEq X] (D : Set (X → Bool)) (C : X → Bool)
    (hC : ∀ d ∈ D, ∃ x1 x2 : X, x1 ≠ x2 ∧ C x1 ≠ d x1 ∧ C x2 ≠ d x2)
    (x0 : X) :
    (∀ d ∈ D, ∃ x : X, Function.update C x0 (!C x0) x ≠ d x)
      ∧ Function.update C x0 (!C x0) x0 = ! C x0 := by
  constructor
  · intro d hd
    obtain ⟨x1, x2, hne, h1, h2⟩ := hC d hd
    rcases eq_or_ne x1 x0 with hx1 | hx1
    · have hx2 : x2 ≠ x0 := by rw [← hx1]; exact Ne.symm hne
      exact ⟨x2, by simpa [Function.update_apply, hx2] using h2⟩
    · exact ⟨x1, by simpa [Function.update_apply, hx1] using h1⟩
  · simp [Function.update_apply]
