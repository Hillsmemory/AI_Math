-- 正确的证明：应通过编译
theorem demo_ok (p q : Prop) (h : p ∧ q) : q ∧ p :=
  ⟨h.2, h.1⟩

theorem demo_arith : 2 + 3 = 5 := rfl
