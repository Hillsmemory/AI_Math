import Mathlib

/-- 
微积分链式法则（Chain Rule）：
设 \(g: \mathbb{R} \to \mathbb{R}\) 在 \(a\) 处可微，导数 \(g'(a) = g'\)；
设 \(f: \mathbb{R} \to \mathbb{R}\) 在 \(b = g(a)\) 处可微，导数 \(f'(b) = f'\)。
则复合函数 \(F = f \circ g\) 在 \(a\) 处也可微，且 \(F'(a) = f'(g(a)) \cdot g'(a)\)。
-/
theorem chain_rule (f g : ℝ → ℝ) (f' g' a : ℝ)
    (hg : HasDerivAt g g' a) (hf : HasDerivAt f f' (g a)) :
    HasDerivAt (f ∘ g) (f' * g') a := by
  -- 第 1 步：明确已知条件，即内层函数 g 在 a 处可导、外层函数 f 在 g(a) 处可导
  -- 这对应图片 Step 1 中对 f 和 g 在相应点可微的提取。
  have hg_deriv : HasDerivAt g g' a := hg
  have hf_deriv : HasDerivAt f f' (g a) := hf

  -- 第 2 步：将内外层导数复合，利用核心的“线性主部相乘”性质
  -- 对应图片 Step 2 到 Step 4 的核心推导：
  -- 通过将增量 g(a+h) - g(a) 展开为 g'(a)h + o(h) 代入外层函数，
  -- 并利用 f 可导的 Weierstrass 分解 f(b+k) - f(b) = f'(b)k + o(k)，
  -- 在极限下消去余项，得到整体的线性主部为 f'(g(a)) * g'(a)。
  -- 在 Lean 中，这个极限处理过程正好封装在复合求导定理 `HasDerivAt.comp` 内部。
  have h_comp : HasDerivAt (f ∘ g) (f' * g') a := hf_deriv.comp a hg_deriv

  -- 第 3 步：整理最终结论
  -- 对应图片 Step 5，复合函数的微分 d(f∘g)_a = df_{g(a)} ∘ dg_a，
  -- 在一维情况下也就是对任意 h，导数为 f'(g(a)) * g'(a)。
  exact h_comp

-- 方法总结：提取内外层函数在相应点的导数条件 → 应用复合求导引理（对应增量展开与无穷小余项消去）→ 完成链式法则证明。