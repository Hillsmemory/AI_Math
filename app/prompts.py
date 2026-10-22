"""
Prompt 模板与 Few-shot 示例库
所有 LLM 调用都用这里的模板构造请求
"""

# ============ 系统提示词 ============

# 核心库版本（无 Mathlib）
SYSTEM_PROMPT_NO_MATHLIB = """你是一名 Lean4 形式化证明专家。你的任务是将用户提供的中文自然语言数学命题，转换为可编译通过的 Lean4 代码。

## ⚠ 重要环境约束
**本机未安装 Mathlib4 库**，请严格遵守以下规则：
1. **禁止使用 `import Mathlib`** —— 会导致 "unknown module prefix 'Mathlib'" 错误
2. **禁止使用 Mathlib 专属定义**，例如：
   - `InnerProductSpace`、`NormedSpace`、`EuclideanSpace` 等高级类型
   - `norm_add_sq_real`、`sq_add_sq`、`Pythagorean` 等 Mathlib 引理
   - `Real`、`Complex` 类型（属于 Mathlib，核心库只有 `Nat`/`Int`/`Rat`/`Float`）
3. **只使用 Lean4 核心库（Init/Std）的内置内容**，可用资源：
   - 类型：`Nat`（自然数，可写 ℕ）、`Int`（整数）、`Rat`（有理数）、`Bool`、`Prop`
   - `Nat` 命名空间下的引理：`Nat.add_zero`, `Nat.zero_add`, `Nat.add_succ`, `Nat.succ_add`, `Nat.mul_zero`, `Nat.zero_mul`, `Nat.mul_succ`, `Nat.add_comm`, `Nat.mul_comm` 等
   - 基础 tactic：`rfl`, `simp`, `rw`, `induction`, `cases`, `exact`, `apply`, `have`, `obtain`
4. **命题等价改写**：若原命题依赖 Mathlib 类型（如实数 `Real`、内积空间），改写为等价的代数形式：
   - 实数上的勾股定理 → 改写为有理数 `Rat` 上的等式，或限制为可化归的自然数命题
   - 内积空间命题 → 改写为展开后的代数等式
   - 实分析命题 → 改写为 `Nat`/`Rat` 上的离散版本

## 输出要求
1. 只输出 Lean4 代码块，用 ```lean ... ``` 包裹
2. **不要写 `import` 语句**（除非确实需要 Init，一般核心库默认可用）
3. 包含完整的 theorem 声明和 proof 实现
4. 优先使用核心库内置引理，避免重复证明
5. 不要使用 sorry 或 admit
6. 如果命题需要前提条件，在 theorem 中正确声明

## Lean4 语法要点
- 自然数类型：Nat（或 ℕ）
- 命题类型：Prop
- 证明使用 tactic：`by rw [lemma1, lemma2]` 或 `by induction x with | zero => ... | succ n ih => ...`
- 等式：`a = b`（非 `==`）
- 全称量词：`∀ x : Nat, P x`（注意是 Unicode ∀ 或 `forall`）
- 蕴含：`P → Q`

## 示例
用户: 证明对任意自然数 n, n + 0 = n
输出:
```lean
theorem add_zero_self (n : Nat) : n + 0 = n := by
  rw [Nat.add_zero]
```

用户: 证明对任意自然数 n, n * 0 = 0
输出:
```lean
theorem mul_zero_self (n : Nat) : n * 0 = 0 := by
  exact Nat.mul_zero n
```
"""

# Mathlib 版本（完整模式）
SYSTEM_PROMPT_WITH_MATHLIB = """你是一名 Lean4 形式化证明专家。你的任务是将用户提供的中文自然语言数学命题，转换为可编译通过的 Lean4 代码。

## 环境信息
**本机已安装 Mathlib4 库**，可使用全部 Mathlib 资源：
- 代码开头可写 `import Mathlib` 引入完整 Mathlib 库
- 可使用 `Real`、`Complex`、`InnerProductSpace`、`NormedSpace`、`EuclideanSpace` 等高级类型
- 可使用 `norm_add_sq_real`、`sq_add_sq`、`Pythagorean` 等 Mathlib 引理
- 可使用 `Set`、`Filter`、`TopologicalSpace` 等结构
- 可使用 Mathlib 的 `Tactic` 命名空间（如 `polyrith`, `nlinarith`, `ring_nf` 等）

## 输出要求
1. 只输出 Lean4 代码块，用 ```lean ... ``` 包裹
2. 代码开头使用 `import Mathlib` 引入标准库
3. 包含完整的 theorem 声明和 proof 实现
4. 优先使用 Mathlib4 内置引理，避免重复证明
5. 不要使用 sorry 或 admit
6. 如果命题需要前提条件，在 theorem 中正确声明

## Lean4 语法要点
- 自然数类型：Nat（或 ℕ）
- 实数类型：Real（来自 Mathlib）
- 命题类型：Prop
- 证明使用 tactic：`by rw [lemma1, lemma2]` 或 `by induction x with | zero => ... | succ n ih => ...`
- 等式：`a = b`（非 `==`）
- 全称量词：`∀ x : Nat, P x`（注意是 Unicode ∀ 或 `forall`）
- 蕴含：`P → Q`

## 示例
用户: 证明对任意自然数 n, n + 0 = n
输出:
```lean
import Mathlib

theorem add_zero_self (n : Nat) : n + 0 = n := by
  rw [Nat.add_zero]
```

用户: 证明勾股定理（在实内积空间中）
输出:
```lean
import Mathlib.Analysis.InnerProductSpace.Pythagorean

theorem pythagorean_real {V : Type*} (x y : V) [InnerProductSpace ℝ V] :
    ‖x + y‖^2 = ‖x‖^2 + ‖y‖^2 ↔ ⟪x, y⟫ = 0 := by
  exact norm_add_sq_real
```
"""

# 向后兼容：默认使用无 Mathlib 版本
SYSTEM_PROMPT = SYSTEM_PROMPT_NO_MATHLIB


def get_system_prompt(has_mathlib: bool) -> str:
    """根据 Mathlib 可用性返回合适的 System Prompt"""
    return SYSTEM_PROMPT_WITH_MATHLIB if has_mathlib else SYSTEM_PROMPT_NO_MATHLIB


# ============ Few-shot 示例（按有无 Mathlib 分两套） ============

# Mathlib 版本（完整模式）
FEW_SHOT_EXAMPLES_WITH_MATHLIB = [
    # 代数题：加法交换律
    {
        "input": "证明：对任意自然数 a 和 b，a + b = b + a",
        "output": """```lean
import Mathlib

-- 加法交换律：对任意自然数 a b，a + b = b + a
theorem add_comm_self (a b : Nat) : a + b = b + a := by
  -- 直接调用 Mathlib4 标准库中的 Nat.add_comm 引理
  rw [Nat.add_comm]
```"""
    },
    # 数论题：偶数平方被 4 整除
    {
        "input": "证明：如果 n 是偶数，则 n² 是 4 的倍数",
        "output": """```lean
import Mathlib

-- 偶数的定义：存在自然数 k 使 n = 2*k
-- 证明：n² = (2*k)² = 4*k² 是 4 的倍数
theorem even_square_div4 (n : Nat) (h : Even n) : 4 ∣ n^2 := by
  -- 展开 Even 的定义：n = 2 * k
  obtain ⟨k, hk⟩ := h
  -- 用 hk 重写 n
  rw [hk]
  -- 展开 (2*k)^2 = 4 * k^2
  rw [mul_pow, pow_mul]
  -- 使用 Nat.mul_comm 整理：4 * (k^2 * 1) 形式
  -- 4 ∣ 4 * k^2 可直接用 dv_mul_left
  apply dvd_mul_right
```"""
    },
    # 集合论题：并集交换律
    {
        "input": "证明：对任意集合 A B，A ∪ B = B ∪ A",
        "output": """```lean
import Mathlib

-- 集合并集交换律
theorem union_comm {α : Type*} (A B : Set α) : A ∪ B = B ∪ A := by
  -- 用集合扩展性原理：左右互相包含
  apply Set.eq_of_subset_of_subset
  · intro x hx
    -- A ∪ B ⊆ B ∪ A
    rcases hx with h | h
    · right; exact h
    · left; exact h
  · intro x hx
    rcases hx with h | h
    · right; exact h
    · left; exact h
```"""
    },
]

# 核心库版本（禁止 import Mathlib）
FEW_SHOT_EXAMPLES_NO_MATHLIB = [
    # 代数题：加法零元
    {
        "input": "证明：对任意自然数 n，n + 0 = n",
        "output": """```lean
theorem add_zero_self (n : Nat) : n + 0 = n := by
  rw [Nat.add_zero]
```"""
    },
    # 代数题：乘法零元
    {
        "input": "证明：对任意自然数 n，n * 0 = 0",
        "output": """```lean
theorem mul_zero_self (n : Nat) : n * 0 = 0 := by
  exact Nat.mul_zero n
```"""
    },
    # 命题逻辑：合取交换（term 模式）
    {
        "input": "证明：对任意命题 p 和 q，p ∧ q 蕴含 q ∧ p",
        "output": """```lean
theorem and_swap (p q : Prop) (h : p ∧ q) : q ∧ p := by
  exact ⟨h.2, h.1⟩
```"""
    },
]

# 向后兼容：默认返回 Mathlib 版本
FEW_SHOT_EXAMPLES = FEW_SHOT_EXAMPLES_WITH_MATHLIB


def get_few_shot_examples(has_mathlib: bool) -> list[dict]:
    """根据 Mathlib 可用性返回对应的 Few-shot 示例"""
    return FEW_SHOT_EXAMPLES_WITH_MATHLIB if has_mathlib else FEW_SHOT_EXAMPLES_NO_MATHLIB


# ============ 提示词构造函数 ============

def build_user_prompt(user_input: str, use_few_shot: bool = True, has_mathlib: bool = True) -> str:
    """构造首轮用户提示词"""
    prompt = ""

    if use_few_shot:
        examples = get_few_shot_examples(has_mathlib)
        prompt += "以下是一些示例，供你参考写法：\n\n"
        for i, ex in enumerate(examples, 1):
            prompt += f"【示例 {i}】\n输入: {ex['input']}\n输出:\n{ex['output']}\n\n"
        prompt += "---\n\n"

    prompt += f"""请将下面的中文数学命题转换为 Lean4 代码：

命题：{user_input}

要求：
1. 输出标准 Mathlib4 写法的 Lean4 代码
2. 用 ```lean 代码块包裹
3. 包含 theorem 声明和完整证明
4. 不要使用 sorry 或 admit
5. 在代码块前可以用一行中文简要说明证明思路

请直接输出，无需额外解释。"""
    return prompt


def build_refinement_prompt(prev_code: str, errors: list[str], warnings: list[str] = None) -> str:
    """构造修正轮的提示词（基于编译错误反馈）"""
    err_text = "\n".join(f"- {e}" for e in errors) if errors else "（无明确错误行）"
    warn_text = ""
    if warnings:
        warn_text = "\n警告：\n" + "\n".join(f"- {w}" for w in warnings)

    return f"""上次生成的 Lean4 代码编译失败，请根据报错信息修复代码。

## 上次代码
```lean
{prev_code}
```

## 编译报错
{err_text}{warn_text}

## 修复要求
1. 仔细分析每个报错，理解错误原因
2. 修复所有错误，确保编译能通过
3. 不要使用 sorry 或 admit
4. 仍然使用标准 Mathlib4 库引理
5. 输出格式：先用一行中文说明修复思路，然后输出 ```lean 代码块```

请直接输出修复后的代码。"""


def extract_code_from_response(response: str) -> tuple[str, str]:
    """从 LLM 响应中提取代码和说明文字
    返回 (code, explanation)
    """
    # 提取代码块
    code = ""
    if "```lean" in response:
        start = response.find("```lean") + len("```lean")
        end = response.find("```", start)
        if end > start:
            code = response[start:end].strip()
    elif "```" in response:
        start = response.find("```") + 3
        end = response.find("```", start)
        if end > start:
            code = response[start:end].strip()
    else:
        # 没有代码块，整段当代码
        code = response.strip()

    # 提取说明文字（代码块之前的内容）
    explanation = ""
    if "```lean" in response:
        pre = response[:response.find("```lean")].strip()
        if pre:
            explanation = pre.split("\n")[-1].strip()
    elif "```" in response:
        pre = response[:response.find("```")].strip()
        if pre:
            explanation = pre.split("\n")[-1].strip()

    return code, explanation


# ============ 定理检索：LLM 候选兜底 ============

THEOREM_CANDIDATES_SYSTEM = """你是 Lean4 Mathlib 定理索引专家。
用户会用自然语言描述一个数学定理或性质，你需要给出 Mathlib4 中最可能对应的定理全名。

规则：
1. 只输出 Mathlib4 中真实存在的声明全名（如 `Nat.Prime.dvd_mul`、`Set.mem_union`）
2. 按可能性从高到低排列，最多 8 个
3. 严格输出 JSON 数组，不要输出任何其他文字，例如：
["add_comm", "Nat.add_comm"]
4. 若完全不确定，输出空数组 []"""


def build_theorem_candidates_prompt(query: str) -> str:
    """构造"给 LLM 找候选定理名"的用户提示词"""
    return f"请给出与下述描述对应的 Mathlib4 定理全名候选列表（JSON 数组）：\n\n{query.strip()}"


def extract_theorem_candidates(response: str) -> list[str]:
    """从 LLM 响应中解析候选定理名 JSON 数组（容忍代码块包裹）"""
    import json
    import re

    text = response.strip()
    # 去掉可能的 ```json ... ``` 包裹
    m = re.search(r"\[[\s\S]*?\]", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    names = []
    for item in data:
        if isinstance(item, str) and re.fullmatch(r"[A-Za-z][\w.']{1,80}", item.strip()):
            names.append(item.strip())
    # 去重保序
    seen, uniq = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq[:8]


# ============ 定理形式化翻译（陈述级，sorry 占位） ============

FORMALIZE_SYSTEM = """你是 Lean4 形式化数学专家。你的任务是：给定一个自然语言数学命题，以及若干【已经过 Lean 内核验证】的 Mathlib 定理签名，把该命题翻译成一条 Lean4 theorem 陈述。

## 严格规则
1. 只翻译"陈述"（statement），证明体一律用 `by sorry` 占位——本步骤验证的是陈述是否形式化正确，不是证明
2. 引用定理时**只能使用下方提供的签名中的名字**，禁止臆造任何 Mathlib 定理名
3. 代码第一行必须是 `import Mathlib`
4. 使用规范的 Mathlib4 记号：ℕ ℤ ℝ、`≤`、`∈`、`∀`、`∃`、`∣`（整除）、`¬`、`∧`、`∨`、`→`、`↔`
5. 类型注解要完整：变量写在 theorem 的参数表中，前提条件写成假设
6. 输出格式：先一行中文说明翻译思路，然后 ```lean 代码块```，代码块内只有 import 与一条 theorem 声明"""


def build_formalize_prompt(
    statement: str,
    verified: dict[str, str],
    extra_hint: str = "",
) -> str:
    """
    构造形式化翻译的用户提示词。
    statement: 自然语言命题
    verified:  {mathlib_id: 内核验证过的签名} —— 只包含 #check 通过的条目
    extra_hint: 可选补充说明
    """
    sig_block = "\n".join(f"- `{name}` : {sig}" for name, sig in verified.items()) or "（本次没有可用的已验证定理签名，请仅用 Lean/Mathlib 基础记号直接翻译命题本身）"
    hint = f"\n\n补充说明：{extra_hint}" if extra_hint else ""
    return f"""## 待形式化的命题
{statement.strip()}
{hint}

## 已经 Lean 内核验证的定理签名（只允许引用这些）
{sig_block}

请输出该命题的 Lean4 theorem 陈述（证明用 `by sorry` 占位）。"""


def build_formalize_fix_prompt(prev_code: str, errors: list[str]) -> str:
    """形式化陈述编译失败时的修正提示词"""
    err_text = "\n".join(f"- {e}" for e in errors) if errors else "（无明确错误行）"
    return f"""上面的 Lean4 陈述编译失败，请根据报错修复。

## 上次代码
```lean
{prev_code}
```

## 编译报错
{err_text}

## 修复要求
1. 保持命题语义不变，只修复语法/类型错误
2. 仍然只允许引用之前提供的已验证定理名
3. 证明体保持 `by sorry`
4. 输出一行中文修复说明 + ```lean 代码块```"""
