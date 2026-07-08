import Mathlib

   theorem exists_hard_function
       {Circuit BoolFn : Type} [Fintype Circuit] [Fintype BoolFn]
       (compute : Circuit → BoolFn)
       (h : Fintype.card Circuit < Fintype.card BoolFn) :
       ∃ f : BoolFn, ∀ c : Circuit, compute c ≠ f := by
     by_contra hcon
     push_neg at hcon
     have hsurj : Function.Surjective compute := fun f => hcon f
     have hle := Fintype.card_le_of_surjective compute hsurj
     omega
