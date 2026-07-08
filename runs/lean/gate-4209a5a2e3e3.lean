import Mathlib

/-- Abstract pigeonhole fact underlying ingredient (1a): if there are strictly
fewer circuits of size ≤ n^k than there are Boolean functions on the input
domain (here modeled abstractly as a Fintype `Circuit` with an evaluation map
into functions `Dom → Bool`, where `Dom` stands for the m-input domain), then
some function is not computed by any such circuit. This captures exactly the
elementary counting step and nothing about where in the complexity spectrum the
resulting hard function lies — i.e. it proves (1a) only, not (1b). -/
theorem exists_hard_function
    {Circuit : Type*} [Fintype Circuit]
    {Dom : Type*} [Fintype Dom] [DecidableEq Dom]
    (eval : Circuit → (Dom → Bool))
    (h : Fintype.card Circuit < Fintype.card (Dom → Bool)) :
    ∃ f : Dom → Bool, ∀ c : Circuit, eval c ≠ f := by
  by_contra hcon
  push_neg at hcon
  have hsurj : Function.Surjective eval := hcon
  have hcard := Fintype.card_le_of_surjective eval hsurj
  omega
