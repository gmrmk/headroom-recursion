theorem relativization_barrier
       {Oracle : Type} (PeqNP : Oracle → Prop)
       (oracle_A oracle_B : Oracle)
       (BGS_A : PeqNP oracle_A) (BGS_B : ¬ PeqNP oracle_B) :
       ¬ (∀ o : Oracle, PeqNP o) ∧ ¬ (∀ o : Oracle, ¬ PeqNP o) :=
     ⟨fun h => BGS_B (h oracle_B), fun h => h oracle_A BGS_A⟩
