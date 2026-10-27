"""
Lean4 → 自然语言翻译器
将 Lean4 theorem 代码翻译为结构化中文：
  1. 命题自然语言化（theorem 签名 → 中文数学陈述）
  2. 证明分步骤解析（逐行 tactic + 含义）
  3. 数学背景补充（涉及的引理、类型、构造）
  4. 整体评注（证明思路概览、简洁度评估）
"""

from dataclasses import dataclass, field
from typing import Optional


# ============ System Prompt ============

TRANSLATE_SYSTEM_PROMPT = """你是一名 Lean4 形式化证明教学专家。你的任务是把用户提供的 Lean4 theorem 代码，翻译为结构化的中文自然语言讲解。

## 输出格式要求（严格按以下 Markdown 结构，不要额外加前后缀）

### 📐 命题自然语言化
用通俗易懂的中文重新陈述 theorem 声明的数学命题，包括：
- 类型参数的含义（如 `(n : Nat)` 即"对任意自然数 n"）
- 前提条件（如有 `h : ...` 即"已知 ..."）
- 结论要证明什么

### 🔍 证明分步骤解析
逐行（或逐块）解释 proof 中的每一步 tactic / term：
- `by` tactic 模式：逐个 tactic 说清楚它做了什么、为什么有效
- term 模式（如 `:= rfl` / `:= ⟨h.2, h.1⟩`）：直接解释构造含义
- 对每个 Lean 内置引理（如 `Nat.add_zero`、`dvd_mul_right`）附上一句中文说明它是什么

示例：
- `simp` → "自动化简：调用一系列标准引理简化目标，无需手动指定"
- `induction n with | zero => ... | succ n ih => ...` → "对 n 做数学归纳法：基例 n=zero，归纳假设 n=ih，归纳步骤 n=succ n"
- `obtain ⟨k, hk⟩ := h` → "从假设 h 中提取出存在量词的两个分量：一个 k 和一个 hk : n = 2*k"
- `rw [lemma1, lemma2]` → "依次用 lemma1 和 lemma2 重写当前目标"

### 📚 涉及的数学知识
- 列出 theorem 使用了哪些标准库引理（如 Nat.add_comm）
- 如有必要，补充该引理的中文名称和数学含义（如 Nat.add_comm → "自然数加法交换律：a + b = b + a"）

### 💡 整体证明思路
用 1-3 句话概括整个证明的策略（如"先展开偶数定义，再用乘法分配律得到 4 的倍数形式"）

## 注意事项
- Lean 保留字和 tactic 名用反引号括起来（如 `simp`、`induction`）
- 不要编造不存在的数学引理
- 证明很简单时（如 `:= rfl`），分步骤解析可以简化
- 如果代码中有 `sorry` 或 `admit`，明确指出"此处证明未完成（仅占位）"
"""


def build_translate_user_prompt(lean_code: str, mathlib_context: bool = True) -> str:
    """构造翻译请求的用户提示词"""
    ctx = "" if mathlib_context else "\n⚠ 注意：代码可能不依赖 Mathlib，仅使用 Lean4 核心库。\n"
    return f"""请将以下 Lean4 theorem 翻译为结构化中文讲解。{ctx}

```lean
{lean_code}
```

请严格按 System Prompt 中的 Markdown 结构输出，从 ### 📐 命题自然语言化 开始。"""


# ============ 主类 ============

@dataclass
class TranslationResult:
    """翻译结果"""
    lean_code: str
    markdown: str
    success: bool = True
    error: str = ""


class LeanTranslator:
    """Lean4 → 自然语言翻译器"""

    def __init__(self, llm_client, mathlib_context: bool = True):
        """
        Args:
            llm_client: 已构造好的 LLMClient 实例
            mathlib_context: 代码是否在 Mathlib 环境下（影响 Prompt 细节）
        """
        self.llm = llm_client
        self.mathlib_context = mathlib_context

    def translate(self, lean_code: str, images: list[str] | None = None) -> TranslationResult:
        """执行翻译；images 为可选图片 data URL 列表（视觉模型时随请求附带）"""
        if not lean_code or not lean_code.strip():
            return TranslationResult(
                lean_code=lean_code, markdown="", success=False, error="代码为空"
            )

        try:
            user_prompt = build_translate_user_prompt(lean_code, self.mathlib_context)
            response = self.llm.chat(TRANSLATE_SYSTEM_PROMPT, user_prompt, temperature=0.1, images=images)
            md = response.strip()

            # 如果 LLM 没有按格式输出（缺标题），补一个兜底
            if "###" not in md:
                md = f"### 📖 LLM 原始输出\n\n{md}"

            return TranslationResult(lean_code=lean_code, markdown=md, success=True)
        except Exception as e:
            return TranslationResult(
                lean_code=lean_code, markdown="", success=False, error=str(e)
            )


# ============ 本地快速测试（不依赖 LLM） ============

# 为了在没有 API Key 时也能展示翻译效果，预置几个静态翻译样例
_DEMO_TRANSLATIONS: dict[str, str] = {
    "add_zero": """### 📐 命题自然语言化
对任意自然数 n，n 加零等于 n。

### 🔍 证明分步骤解析
- `theorem add_zero (n : Nat) : n + 0 = n := rfl`
- `rfl` 即 reflexivity（自反性），Lean4 内核知道 `n + 0 = n` 是自然数加法的定义式，直接归约成立，无需展开任何 tactic。

### 📚 涉及的数学知识
- **无额外引理**，`rfl` 调用的是自然数加法的构造定义。

### 💡 整体证明思路
直接利用 Lean4 对自然数加法 `a + 0 = a` 的内置定义，一条 `rfl` 搞定。""",

    "zero_add": """### 📐 命题自然语言化
对任意自然数 n，零加 n 等于 n。

### 🔍 证明分步骤解析
- `theorem zero_add (n : Nat) : 0 + n = n := by simp`
- `simp`（simplification）自动选择一系列标准引理，把 `0 + n` 重写为 `n`。核心依赖自然数加法左零元引理。

### 📚 涉及的数学知识
- `Nat.zero_add`：零加任意自然数等于该自然数（与 add_zero 对称）

### 💡 整体证明思路
`0 + n = n` 比 `n + 0 = n` 多一步，因为加法是右递归定义的。`simp` 自动帮我们调对了引理。""",

    "and_comm": """### 📐 命题自然语言化
对任意命题 p 和 q，p ∧ q 等价于 q ∧ p（合取交换律）。

### 🔍 证明分步骤解析
分两条蕴含方向证明：
1. `constructor` 把 `↔` 拆成两个方向 `p ∧ q → q ∧ p` 和 `q ∧ p → p ∧ q`
2. `intro h` 引入假设 `h : p ∧ q`
3. `exact ⟨h.2, h.1⟩`：用 h 的第二分量作 q，第一分量作 p，构造出 `q ∧ p`
4. 反方向对称：同样 `intro h` 后 `exact ⟨h.2, h.1⟩`

### 📚 涉及的数学知识
- `h.1` / `h.2`：从合取 `p ∧ q` 中分别取 p 和 q
- `⟨a, b⟩`：构造合取 `a ∧ b`

### 💡 整体证明思路
用 `↔` = 双向蕴含，两个方向都是简单的合取交换，都用构造器 ⟨⟩ 重新打包分量。""",

    "even_square_div4": """### 📐 命题自然语言化
如果 n 是偶数，则 n² 能被 4 整除。

### 🔍 证明分步骤解析
1. `obtain ⟨k, hk⟩ := h` —— 从假设 `h : Even n` 中提取 k 和等式 `hk : n = 2*k`
2. `rw [hk, Nat.mul_pow]` —— 先把 n 替换成 2*k，再用 `Nat.mul_pow` 把 `(2*k)^2` 展开成 `2^2 * k^2`
3. `exact dvd_mul_right 4 (k^2)` —— 目标变成 `4 ∣ 4 * k^2`，直接调用 `dvd_mul_right`（对任意 a,b，a ∣ a*b）

### 📚 涉及的数学知识
- `Even n` 定义：存在 k 使 `n = 2 * k`
- `Nat.mul_pow`：`(a*b)^n = a^n * b^n`
- `dvd_mul_right`：对任意自然数 a,b，a 整除 a*b

### 💡 整体证明思路
"偶数 = 2k" 展开后直接平方得到 4k²，整除性显然成立——整个证明就是机械代数展开。""",
}


def get_demo_translation(key: str) -> Optional[str]:
    """获取预置演示翻译，失败返回 None"""
    return _DEMO_TRANSLATIONS.get(key)
