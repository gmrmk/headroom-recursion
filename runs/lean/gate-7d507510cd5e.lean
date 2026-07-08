import Mathlib

/-- Definitional skeleton of a natural property against a class `C`
    (Razborov–Rudich 1997). The quantitative constructive/large conditions
    are abstract; `useful` is given a simplified single-`n` form (the
    "infinitely many n" quantifier of the real definition is omitted). The
    barrier theorem (natural properties barred under a PRG assumption) is
    not formalized. -/
structure NaturalProperty (Fn : Type) (C : Fn → Prop) where
  Prop_        : Fn → Prop
  constructive : Prop
  large        : Prop
  useful       : ∀ f, C f → ¬ Prop_ f
