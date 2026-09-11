-- 错误的证明：应被 Lean 拒绝并报错
theorem demo_bad (p q : Prop) (h : p ∧ q) : q ∧ p :=
  ⟨h.1, h.2⟩
