theorem karp_lipton_applied
       {NPinPpoly PHeqSigma2 : Prop}
       (karp_lipton : NPinPpoly → PHeqSigma2)
       (h : NPinPpoly) : PHeqSigma2 :=
     karp_lipton h
