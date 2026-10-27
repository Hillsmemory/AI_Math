"""
ProofChecker: 独立证明检查 Agent（快速加固目标）

与 ProofAgent（生成代码）不同，ProofChecker 只做"审计"，不生成代码：
1. 静态检查清单（无需 Lean 环境）：sorry/admit、theorem 声明、括号配对、import 一致性
2. 真实编译检查（需要 Lean）：复用 LeanCompiler
3. axiom 深度检查（需要 Lean）：通过 #print axioms 揪出依赖 sorryAx 的"伪证明"
   —— 这是静态检查抓不到的：sorry 能编译通过，但 axiom 依赖会暴露它
4. LLM 深度审查（可选，需要 API Key）：审查证明的真正完成度与非平凡性
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from compiler import LeanCompiler, CompilationResult

# ============ 检查级别 ============

LEVEL_PASS = "pass"        # 检查通过
LEVEL_ERROR = "error"      # 必须修复
LEVEL_WARNING = "warning"  # 建议修复
LEVEL_INFO = "info"        # 提示信息


@dataclass
class CheckItem:
    """单项检查结果"""
    name: str
    passed: bool
    level: str  # pass / error / warning / info
    message: str
    details: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    """整体检查结果"""
    code: str
    items: list[CheckItem] = field(default_factory=list)
    compilation: Optional[CompilationResult] = None
    llm_review: str = ""
    passed: bool = False
    summary: str = ""


# ============ LLM 审查 Prompt ============

REVIEW_SYSTEM_PROMPT = """你是一名严格的 Lean4 证明审稿人。你的任务是审查用户提供的 Lean4 证明代码，判断证明是否真正完成、是否非平凡。

## 审查维度
1. **完成度**：是否使用了 sorry / admit 等占位符？证明是否覆盖了命题的所有部分？
2. **正确性**：证明逻辑是否真的证明了声明的命题？（编译通过不代表证明对了——比如命题本身可能被弱化成空洞形式）
3. **非平凡性**：命题是否是恒真式/空洞命题（如 x = x）？前提是否弱化了原命题的本意？
4. **代码质量**：是否有更简洁标准的写法（如用标准库已有引理替代手工构造）？

## 输出格式（严格遵循）
### 🧾 总体结论
（✅ 通过 / ⚠️ 有疑点 / ❌ 有问题，一句话说明）

### 🔎 发现的问题
（逐条列出，无问题写"未发现明显问题"）

### 💡 改进建议
（1-3 条，无则写"无"）

## 注意
- 不要编造 Lean 引理名
- 用中文输出
"""


def build_review_user_prompt(lean_code: str) -> str:
    """构造 LLM 审查的用户提示词"""
    return f"""请审查以下 Lean4 证明：

```lean
{lean_code}
```

请严格按 System Prompt 的 Markdown 结构输出审查报告。"""


class ProofChecker:
    """独立证明检查器：静态检查 + 编译检查 + axiom 深度检查（+ 可选 LLM 审查）"""

    # theorem/lemma 声明（捕获名字）
    _THEOREM_RE = re.compile(r"^\s*(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_!'?]*)", re.MULTILINE)
    # sorry / admit 占位符
    _PLACEHOLDER_RE = re.compile(r"\b(sorry|admit)\b")
    # #print axioms 输出：'foo' depends on axioms: [sorryAx, propext]
    _AXIOM_DEP_RE = re.compile(r"'([^']+)'\s+depends on axioms:\s*\[([^\]]*)\]")

    def __init__(self, compiler: LeanCompiler, llm_client=None):
        """
        Args:
            compiler: LeanCompiler 实例（决定能否做编译级检查）
            llm_client: 可选的 LLMClient，提供则支持 LLM 深度审查
        """
        self.compiler = compiler
        self.llm = llm_client

    # ---------- 主入口 ----------

    def check(self, lean_code: str, use_llm_review: bool = False,
              images: list[str] | None = None,
              on_progress=None) -> CheckResult:
        """执行完整检查清单。

        on_progress(status, detail=None) 可选的进度回调：
          - status="static"    静态检查开始
          - status="compiling" 编译开始（detail 非 None 时为其心跳秒数）
          - status="llm"       LLM 审查开始
        images 供可选 LLM 审查附带图片（视觉模型）
        """
        items: list[CheckItem] = []
        code = lean_code or ""

        # 1. 静态检查清单（始终执行，纯正则，秒级）
        if on_progress:
            on_progress("static")
        items.append(self._check_theorem_decl(code))
        items.append(self._check_sorry(code))
        items.append(self._check_brackets(code))
        items.append(self._check_import_consistency(code))

        # 2. 真实编译 + axiom 深度检查（有 Lean 时）
        #    合并为「一次」编译：直接附加 #print axioms，
        #    同一次 Mathlib 加载即可同时拿到编译结果和 axiom 依赖，避免重复编译两遍。
        compilation: Optional[CompilationResult] = None
        if self.compiler.available:
            if on_progress:
                on_progress("compiling")

            def _hb(secs: int):
                if on_progress:
                    on_progress("compiling", secs)

            # 附加 #print axioms 后一次编译（即使原代码编译失败，#print 也不影响错误定位）
            names = self._THEOREM_RE.findall(code)
            augmented = code.rstrip() + "\n\n" + "\n".join(f"#print axioms {n}" for n in names) + "\n"
            compilation, raw = self.compiler.compile_code_raw(augmented, on_progress=_hb)

            if compilation.success:
                items.append(CheckItem("真实编译", True, LEVEL_PASS, "Lean 编译器验证通过"))
                items.append(self._parse_axioms_entry(raw, names))
            else:
                items.append(CheckItem(
                    "真实编译", False, LEVEL_ERROR,
                    f"Lean 编译失败，共 {len(compilation.errors)} 个错误",
                    details=compilation.errors[:5],
                ))
        else:
            items.append(CheckItem(
                "真实编译", True, LEVEL_INFO,
                "未安装 Lean，跳过编译检查（静态检查仍有效）",
            ))

        # 3. 可选 LLM 深度审查
        llm_review = ""
        if use_llm_review and self.llm is not None:
            if on_progress:
                on_progress("llm")
            llm_review = self._llm_review(code, images)

        n_pass = sum(1 for i in items if i.level == LEVEL_PASS)
        n_error = sum(1 for i in items if i.level == LEVEL_ERROR)
        n_warn = sum(1 for i in items if i.level == LEVEL_WARNING)
        has_error = n_error > 0

        return CheckResult(
            code=code,
            items=items,
            compilation=compilation,
            llm_review=llm_review,
            passed=not has_error,
            summary=f"通过 {n_pass} 项 · 错误 {n_error} 项 · 警告 {n_warn} 项",
        )

    # ---------- 静态检查 ----------

    def _check_theorem_decl(self, code: str) -> CheckItem:
        """检查是否存在 theorem/lemma 声明"""
        names = self._THEOREM_RE.findall(code)
        if names:
            return CheckItem("theorem 声明", True, LEVEL_PASS, f"声明了 {len(names)} 个 theorem/lemma")
        return CheckItem("theorem 声明", False, LEVEL_ERROR, "未找到 theorem/lemma 声明")

    def _check_sorry(self, code: str) -> CheckItem:
        """检查 sorry / admit 占位符"""
        found = []
        for i, line in enumerate(code.splitlines(), 1):
            stripped = line.split("--")[0]
            if self._PLACEHOLDER_RE.search(stripped):
                found.append(f"第{i}行: {stripped.strip()}")
        if found:
            return CheckItem(
                "sorry/admit 检测", False, LEVEL_ERROR,
                f"发现 {len(found)} 处未完成证明占位符",
                details=found,
            )
        return CheckItem("sorry/admit 检测", True, LEVEL_PASS, "未发现 sorry/admit 占位符")

    def _check_brackets(self, code: str) -> CheckItem:
        """括号配对检查"""
        pairs = {"(": ")", "{": "}", "[": "]"}
        closers = set(pairs.values())
        stack: list[tuple[str, int]] = []
        problems: list[str] = []
        for i, line in enumerate(code.splitlines(), 1):
            no_comment = line.split("--")[0]
            for ch in no_comment:
                if ch in pairs:
                    stack.append((ch, i))
                elif ch in closers:
                    if not stack:
                        problems.append(f"第{i}行: 多余的 '{ch}'")
                    else:
                        top, ln = stack.pop()
                        if pairs[top] != ch:
                            problems.append(f"第{i}行: 括号不匹配，'{top}'（第{ln}行）被 '{ch}' 关闭")
        for ch, ln in stack:
            problems.append(f"第{ln}行: '{ch}' 未闭合")
        if problems:
            return CheckItem("括号配对", False, LEVEL_ERROR, f"{len(problems)} 处括号问题", details=problems)
        return CheckItem("括号配对", True, LEVEL_PASS, "括号配对正常")

    def _check_import_consistency(self, code: str) -> CheckItem:
        """检查 import Mathlib 与当前环境的一致性"""
        has_import = re.search(r"^\s*import\s+Mathlib", code, re.MULTILINE) is not None
        if has_import and not self.compiler.has_mathlib:
            return CheckItem(
                "import 一致性", False, LEVEL_ERROR,
                "代码 import 了 Mathlib，但当前环境未安装 Mathlib（编译必然失败）",
                details=["移除 import Mathlib 并改用核心库引理，或在安装了 Mathlib 的环境运行"],
            )
        if not has_import and self.compiler.has_mathlib:
            return CheckItem(
                "import 一致性", True, LEVEL_WARNING,
                "环境有 Mathlib 但代码未 import，Mathlib 专属引理不可用",
            )
        if has_import:
            return CheckItem("import 一致性", True, LEVEL_PASS, "import 与环境一致")
        return CheckItem("import 一致性", True, LEVEL_INFO, "未使用 import（核心库默认可用）")

    # ---------- axiom 深度检查 ----------

    def _parse_axioms_entry(self, raw: str, names: list[str]) -> CheckItem:
        """解析带 #print axioms 的编译原始输出，检查是否依赖 sorryAx（伪证明）。

        原理：sorry 能让编译通过，但任何依赖 sorry 的定理在 #print axioms
        输出中会出现 sorryAx。这是编译检查无法发现的"漏网之鱼"。
        （编译本身已在 check() 里完成，这里只做输出解析，避免重复编译。）
        """
        if not names:
            return CheckItem("axiom 深度检查", True, LEVEL_INFO, "未找到 theorem/lemma 声明，跳过")

        if not raw:
            return CheckItem("axiom 深度检查", True, LEVEL_INFO, "无法获取编译器输出，跳过 axiom 检查")

        deps = self._AXIOM_DEP_RE.findall(raw)
        if not deps:
            return CheckItem(
                "axiom 深度检查", True, LEVEL_INFO,
                "未解析到 axiom 依赖信息（可能定理在 namespace 内需全限定名，或编译器输出格式不同）",
            )

        bad = {name: axs for name, axs in deps if "sorryAx" in axs}
        if bad:
            det = [f"{name} 依赖: [{axs}]" for name, axs in bad.items()]
            return CheckItem(
                "axiom 深度检查", False, LEVEL_ERROR,
                f"{len(bad)} 个定理依赖 sorryAx（伪证明）",
                details=det,
            )
        clean = ", ".join(name for name, _ in deps)
        return CheckItem("axiom 深度检查", True, LEVEL_PASS, f"无 sorryAx 依赖（涉及: {clean}）")

    # ---------- LLM 审查 ----------

    def _llm_review(self, code: str, images: list[str] | None = None) -> str:
        """调用 LLM 做深度审查，返回 markdown 报告"""
        try:
            resp = self.llm.chat(REVIEW_SYSTEM_PROMPT, build_review_user_prompt(code), temperature=0.1, images=images)
            return resp.strip()
        except Exception as e:
            return f"> ⚠️ LLM 审查失败：{e}"


# ============ 预置检查示例 ============

# 含 sorry 的示例：用于演示检查器能抓出未完成证明
SAMPLE_CHECK_CODE = """theorem bad_proof (n : Nat) : n + 1 = n := by
  sorry
"""
