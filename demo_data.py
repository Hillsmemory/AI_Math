"""
内置演示数据——无需 Lean4 和 LLM API 也能完整展示 Agent 反馈闭环流程
"""

from dataclasses import dataclass, field


@dataclass
class CompilationResult:
    """编译结果"""
    success: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_explanations: list[str] = field(default_factory=list)
    warning_explanations: list[str] = field(default_factory=list)


@dataclass
class IterationRecord:
    """单轮迭代记录"""
    round: int
    code: str
    explanation: str
    compilation: CompilationResult


@dataclass
class DemoResult:
    """演示结果"""
    user_input: str
    final_code: str
    final_explanation: str
    pass_compilation: bool
    total_rounds: int
    iterations: list[IterationRecord]


# ============ 加法交换律演示 ============

ADD_COMM_DEMO = DemoResult(
    user_input="证明：对任意自然数 a 和 b，a + b = b + a",
    final_code="""import Mathlib

theorem add_comm (a b : ℕ) : a + b = b + a := by
  induction a with
  | zero => simp
  | succ a ih =>
    rw [Nat.succ_add, Nat.add_succ]
    rw [ih]""",
    final_explanation="修正：使用 Nat 命名空间中的标准引理，如 Nat.add_zero 等。",
    pass_compilation=True,
    total_rounds=2,
    iterations=[
        IterationRecord(
            round=1,
            code="""import Mathlib

theorem add_comm (a b : ℕ) : a + b = b + a := by
  induction a with
  | zero => simp [add_zero, zero_add] -- 错误：add_zero 未定义
  | succ a ih => simp [ih, add_succ, succ_add]
    sorry""",
            explanation="通过对 a 进行归纳来证明加法交换律，base case 和归纳步骤都使用 simp 简化。",
            compilation=CompilationResult(
                success=False,
                errors=[
                    "第4行, 第12列: unknown identifier 'add_zero'",
                    "第6行, 第14列: unknown identifier 'add_succ'",
                ],
                warnings=["第7行: 使用了 'sorry'"],
                error_explanations=[
                    "add_zero 不是顶层名称，应使用 Nat.add_zero（Lean4 标准库的引理都在对应类型的命名空间下）",
                    "add_succ 同理，应使用 Nat.add_succ",
                ],
                warning_explanations=[
                    "sorry 是占位符，表示证明未完成，编译通过但逻辑不严谨",
                ],
            ),
        ),
        IterationRecord(
            round=2,
            code="""import Mathlib

theorem add_comm (a b : ℕ) : a + b = b + a := by
  induction a with
  | zero => simp
  | succ a ih =>
    rw [Nat.succ_add, Nat.add_succ]
    rw [ih]""",
            explanation="修正：使用 Nat 命名空间中的标准引理，如 Nat.add_zero 等。",
            compilation=CompilationResult(
                success=True,
                errors=[],
                warnings=[],
            ),
        ),
    ],
)


# ============ 偶数平方演示 ============

EVEN_SQUARE_DEMO = DemoResult(
    user_input="证明：如果 n 是偶数，则 n² 是 4 的倍数",
    final_code="""import Mathlib

theorem even_sq_div4 (n : Nat) (h : Even n) : 4 ∣ n ^ 2 := by
  obtain ⟨k, hk⟩ := h
  rw [hk, Nat.mul_pow]
  exact dvd_mul_right 4 (k ^ 2)""",
    final_explanation="利用 Even 定义展开 n = 2k，再展开 (2k)^2 = 4k^2，直接得出 4 | 4k^2。",
    pass_compilation=True,
    total_rounds=2,
    iterations=[
        IterationRecord(
            round=1,
            code="""import Mathlib

theorem even_sq (n : Nat) (h : Even n) : 4 ∣ n^2 := by
  cases h with
  | intro k hk =>
    rw [hk]
    simp [mul_pow]
    sorry""",
            explanation="展开偶数定义 n=2k，用 simp 化简 (2k)^2 的形式。",
            compilation=CompilationResult(
                success=False,
                errors=[
                    "第4行: unexpected token 'cases', expected 'obtain' or 'rcases'",
                    "第7行: simp failed to simplify",
                ],
                warnings=["第8行: 使用了 'sorry'"],
                error_explanations=[
                    "Lean4 中解构 Even 应使用 obtain ⟨k, hk⟩ := h 或 rcases h with ⟨k, hk⟩",
                    "simp 无法自动化简此目标，需要手动提供引理或用 rw 重写",
                ],
                warning_explanations=[
                    "sorry 是占位符，证明未完成",
                ],
            ),
        ),
        IterationRecord(
            round=2,
            code="""import Mathlib

theorem even_sq_div4 (n : Nat) (h : Even n) : 4 ∣ n ^ 2 := by
  obtain ⟨k, hk⟩ := h
  rw [hk, Nat.mul_pow]
  exact dvd_mul_right 4 (k ^ 2)""",
            explanation="利用 Even 定义展开 n = 2k，再展开 (2k)^2 = 4k^2，直接得出 4 | 4k^2。",
            compilation=CompilationResult(
                success=True,
                errors=[],
                warnings=[],
            ),
        ),
    ],
)


# ============ 演示数据映射 ============

DEMO_MAP = {
    "加法交换律": ADD_COMM_DEMO,
    "偶数平方": EVEN_SQUARE_DEMO,
}


def get_demo(demo_key: str) -> DemoResult:
    """获取演示数据，默认返回加法交换律"""
    return DEMO_MAP.get(demo_key, ADD_COMM_DEMO)
