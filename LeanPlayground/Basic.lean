namespace LeanPlayground

-- 示例 1：命题逻辑 —— 合取交换（项模式，内核直接检查）
theorem and_swap (p q : Prop) (h : p ∧ q) : q ∧ p :=
  ⟨h.2, h.1⟩

-- 示例 2：命题逻辑 —— tactic 模式
theorem and_comm (p q : Prop) : p ∧ q ↔ q ∧ p := by
  constructor
  · intro h
    exact ⟨h.2, h.1⟩
  · intro h
    exact ⟨h.2, h.1⟩

-- 示例 3：自然数 —— 内核归约
theorem add_zero (n : Nat) : n + 0 = n := rfl

-- 示例 4：算术 —— simp 自动证明
theorem zero_add (n : Nat) : 0 + n = n := by simp

end LeanPlayground
