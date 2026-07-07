/-
  Shannon's circuit-size lower bound (1949), machine-verified.

  Produced by the headroom-recursion harness during a P-vs-NP research run
  (payday2), then independently re-compiled against Mathlib and axiom-audited:
  every theorem below depends only on [propext, Classical.choice, Quot.sound]
  — the standard Mathlib axioms — and NONE on sorryAx. Zero trust in any model.

  Headline result `shannon_semantic`: for all m ≥ 2 and s with s·(2m+4) < 2^m,
  there is a Boolean function on m inputs computed by NO size-s circuit under a
  genuine gate-by-gate evaluator. This is the entry-level counting lower bound
  of circuit complexity — a [KNOWN] result (Shannon 1949), verified, NOT a step
  past the relativization/natural-proofs/algebrization barriers, which the run
  left explicitly untouched.

  Toolchain: Lean 4.31.0 + Mathlib v4.31.0.  Compile: `lake env lean` in a
  Mathlib project.  Verify axioms: append `#print axioms shannon_semantic`.
-/

import Mathlib
open Function

-- Claim 1 (VERIFIED): pigeonhole counting core.
theorem exists_hard_function {C F : Type*} [Fintype C] [Fintype F]
    (eval : C → F) (h : Fintype.card C < Fintype.card F) :
    ∃ f : F, ∀ c : C, eval c ≠ f := by
  by_contra hcon
  push_neg at hcon
  have hsurj : Surjective eval := by
    intro f
    obtain ⟨c, hc⟩ := hcon f
    exact ⟨c, hc⟩
  have hle := Fintype.card_le_of_surjective eval hsurj
  omega

-- Claim 4 (VERIFIED): concrete Shannon gap, m = s = 6.
theorem counting_gap : (3 * (6 + 6 + 2) ^ 2) ^ 6 < 2 ^ (2 ^ 6) := by
  norm_num

-- Descriptor space: s gate-records over s+m+2 wire sources.
abbrev Circuit (m s : ℕ) :=
  Fin s → (Fin 3 × Fin (s + m + 2) × Fin (s + m + 2))

-- Claim 2 (VERIFIED): exact census of the descriptor space.
theorem card_circuit (m s : ℕ) :
    Fintype.card (Circuit m s) = (3 * (s + m + 2) ^ 2) ^ s := by
  rw [Fintype.card_fun, Fintype.card_prod, Fintype.card_prod]
  simp only [Fintype.card_fin]
  ring

-- Claim 3 (VERIFIED): exact census of the Boolean-function space.
theorem card_F (m : ℕ) :
    Fintype.card ((Fin m → Bool) → Bool) = 2 ^ (2 ^ m) := by
  rw [Fintype.card_fun, Fintype.card_bool, Fintype.card_fun,
    Fintype.card_bool, Fintype.card_fin]

-- Claim 5 (VERIFIED): census gap, end-to-end, m = s = 6.
theorem card_gap_6 :
    Fintype.card (Circuit 6 6) < Fintype.card ((Fin 6 → Bool) → Bool) := by
  rw [card_circuit, card_F]
  norm_num

-- Claim 6 helper (VERIFIED): n < 2^n, name-drift-proof local version.
theorem lt_two_pow_self' (n : ℕ) : n < 2 ^ n := by
  induction n with
  | zero => norm_num
  | succ k ih =>
    have h1 : 1 ≤ 2 ^ k := Nat.one_le_two_pow
    have h2 : 2 ^ (k + 1) = 2 ^ k + 2 ^ k := by ring
    omega

-- Claim 6 (VERIFIED): parametric census gap, s·(2m+4) < 2^m.
theorem shannon_gap_asymptotic (m s : ℕ) (hm : 2 ≤ m)
    (hs : s * (2 * m + 4) < 2 ^ m) :
    (3 * (s + m + 2) ^ 2) ^ s < 2 ^ (2 ^ m) := by
  obtain ⟨k, rfl⟩ : ∃ k, m = k + 2 := ⟨m - 2, by omega⟩
  have hk : k < 2 ^ k := lt_two_pow_self' k
  have h1 : k + 2 + 2 ≤ 2 ^ (k + 2) := by
    have h4k : 2 ^ (k + 2) = 4 * 2 ^ k := by ring
    omega
  have h2 : s < 2 ^ (k + 2) := calc
    s = s * 1 := (Nat.mul_one s).symm
    _ ≤ s * (2 * (k + 2) + 4) := Nat.mul_le_mul (Nat.le_refl s) (by omega)
    _ < 2 ^ (k + 2) := hs
  have h3 : s + (k + 2) + 2 < 2 ^ (k + 2 + 1) := by
    have hdbl : 2 ^ (k + 2 + 1) = 2 ^ (k + 2) + 2 ^ (k + 2) := by ring
    omega
  have h4 : 3 * (s + (k + 2) + 2) ^ 2 ≤ 2 ^ (2 * (k + 2) + 4) := by
    have hsq : (s + (k + 2) + 2) ^ 2 ≤ (2 ^ (k + 2 + 1)) ^ 2 :=
      Nat.pow_le_pow_left (Nat.le_of_lt h3) 2
    have hexp : (2 ^ (k + 2 + 1)) ^ 2 = 2 ^ ((k + 2 + 1) * 2) :=
      (pow_mul 2 (k + 2 + 1) 2).symm
    have hfour : 4 * 2 ^ ((k + 2 + 1) * 2) = 2 ^ (2 * (k + 2) + 4) := by
      ring
    omega
  calc (3 * (s + (k + 2) + 2) ^ 2) ^ s
      ≤ (2 ^ (2 * (k + 2) + 4)) ^ s := Nat.pow_le_pow_left h4 s
    _ = 2 ^ ((2 * (k + 2) + 4) * s) := (pow_mul 2 (2 * (k + 2) + 4) s).symm
    _ < 2 ^ (2 ^ (k + 2)) :=
        Nat.pow_lt_pow_right (by norm_num) (by rw [Nat.mul_comm]; exact hs)

-- Claim 7 (VERIFIED): hard-function existence for the whole family
-- s < 2^m/(2m+4) — the Ω(2^m/m)-shaped Shannon statement.
theorem hard_function_family (m s : ℕ) (hm : 2 ≤ m)
    (hs : s * (2 * m + 4) < 2 ^ m)
    (eval : Circuit m s → ((Fin m → Bool) → Bool)) :
    ∃ f : (Fin m → Bool) → Bool, ∀ c : Circuit m s, eval c ≠ f := by
  apply exists_hard_function
  rw [card_circuit, card_F]
  exact shannon_gap_asymptotic m s hm hs

-- NEW THIS STEP. Gate semantics: 0 ↦ AND, 1 ↦ OR, 2 ↦ NOT (first arg).
def gateOp (t : Fin 3) (x y : Bool) : Bool :=
  if t = 0 then x && y else if t = 1 then x || y else !x

-- NEW THIS STEP. Total straight-line semantics on the census type:
-- wires 0..m-1 are inputs; each gate record appends one wire; out-of-range
-- reads default to false (descriptor space is a SUPERSET of genuine
-- circuits — this only strengthens the miss statement); output = last wire.
def evalCircuit {m s : ℕ} (c : Circuit m s) (x : Fin m → Bool) : Bool :=
  let init : List Bool := (List.finRange m).map x
  let final : List Bool :=
    (List.finRange s).foldl
      (fun acc i =>
        let g := c i
        acc ++ [gateOp g.1 (acc.getD g.2.1.val false) (acc.getD g.2.2.val false)])
      init
  final.getLastD false

-- NEW THIS STEP. Claim 8: semantic Shannon theorem — some m-input Boolean
-- function is not computed by ANY size-s descriptor under evalCircuit,
-- for every m ≥ 2 and s with s·(2m+4) < 2^m.
theorem shannon_semantic (m s : ℕ) (hm : 2 ≤ m)
    (hs : s * (2 * m + 4) < 2 ^ m) :
    ∃ f : (Fin m → Bool) → Bool, ∀ c : Circuit m s, evalCircuit c ≠ f :=
  hard_function_family m s hm hs (fun c => evalCircuit c)
