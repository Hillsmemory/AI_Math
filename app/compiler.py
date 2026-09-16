"""
Lean4 编译器封装
- 真实模式（带 Mathlib）：通过 lake env lean 编译，可使用 import Mathlib
- 真实模式（仅核心库）：直接调用 lean 命令，仅核心库可用
- 模拟模式：未安装 lean 时做静态语法检查
"""

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field


@dataclass
class CompilationResult:
    """编译结果"""
    success: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_explanations: list[str] = field(default_factory=list)
    warning_explanations: list[str] = field(default_factory=list)


class LeanCompiler:
    """Lean4 编译器封装：真实 / 模拟 双模式"""

    # Lean lake 项目根目录（本文件位于 <root>/app/compiler.py，
    # lakefile.toml、Mathlib 依赖与预编译缓存都在仓库根目录）
    _WS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _subprocess_env(self) -> dict:
        """子进程环境变量：让 lake/lean 复用项目内 .cache 下的 Mathlib 预编译缓存"""
        env = os.environ.copy()
        cache_dir = os.path.join(self._WS_DIR, ".cache")
        if os.path.isdir(cache_dir):
            env["XDG_CACHE_HOME"] = cache_dir
        return env

    def __init__(self):
        self._lean_bin = shutil.which("lean")
        self._lake_bin = shutil.which("lake")
        # 优先级：lean_ws + lake > 仅 lean > 模拟
        self._has_ws = (
            self._lake_bin is not None
            and os.path.isdir(self._WS_DIR)
            and os.path.isfile(os.path.join(self._WS_DIR, "lakefile.toml"))
        )
        self._available = self._has_ws or self._lean_bin is not None

    @property
    def available(self) -> bool:
        """是否真实模式"""
        return self._available

    @property
    def has_mathlib(self) -> bool:
        """是否带 Mathlib（仅 lean_ws 模式）"""
        return self._has_ws

    def compile_code(self, lean_code: str) -> CompilationResult:
        """编译 Lean4 代码字符串"""
        if self._has_ws:
            return self._compile_with_ws(lean_code)
        if self._lean_bin:
            return self._compile_real(lean_code)
        return self._compile_mock(lean_code)

    # ---------- 真实编译（带 Mathlib） ----------

    def _compile_with_ws(self, lean_code: str) -> CompilationResult:
        """通过 lake env lean 编译，支持 import Mathlib"""
        tmp_name = f"_tmp_compile_{uuid.uuid4().hex[:8]}.lean"
        tmp_path = os.path.join(self._WS_DIR, tmp_name)
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(lean_code)

            # 首次编译 Mathlib 加载较慢，给 300s
            proc = subprocess.run(
                [self._lake_bin, "env", "lean", tmp_name],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=self._WS_DIR,
                env=self._subprocess_env(),
            )

            output = proc.stdout + proc.stderr
            return self._parse_lean_output(output, proc.returncode)
        except subprocess.TimeoutExpired:
            return CompilationResult(
                success=False,
                errors=["编译超时（超过 300 秒）"],
                error_explanations=["可能是 Mathlib 首次加载较慢，或代码中存在无限循环"],
            )
        except Exception as e:
            return CompilationResult(
                success=False,
                errors=[f"编译器异常: {e}"],
            )
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    # ---------- 真实编译（仅核心库） ----------

    def _compile_real(self, lean_code: str) -> CompilationResult:
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".lean", delete=False, encoding="utf-8"
            ) as f:
                f.write(lean_code)
                tmp_path = f.name

            proc = subprocess.run(
                ["lean", tmp_path],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.path.dirname(tmp_path),
            )

            output = proc.stdout + proc.stderr
            return self._parse_lean_output(output, proc.returncode)
        except subprocess.TimeoutExpired:
            return CompilationResult(
                success=False,
                errors=["编译超时（超过 30 秒）"],
                error_explanations=["可能是代码中存在无限循环或递归无法终止"],
            )
        except Exception as e:
            return CompilationResult(
                success=False,
                errors=[f"编译器异常: {e}"],
            )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    # Lean 诊断起始行：文件:行:列[:结束行:结束列]: error|warning|info: 消息
    _DIAG_RE = re.compile(
        r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)"
        r"(?::(?P<end_line>\d+):(?P<end_col>\d+))?:\s*"
        r"(?P<sev>error|warning|info):\s*(?P<msg>.*)$"
    )

    def _parse_lean_output(self, output: str, returncode: int) -> CompilationResult:
        """解析 lean 编译器的输出（保留多行诊断：type mismatch 的
        expected/got、unsolved goals 的目标上下文等都是续行）"""
        errors = []
        warnings = []
        error_explanations = []
        warning_explanations = []

        sev = None
        body: list[str] = []

        def flush():
            """把累积的一条诊断（首行 + 续行）归档"""
            if sev is None or not body:
                return
            text = "\n".join(body).rstrip()
            if sev == "error":
                errors.append(text)
                error_explanations.append(self._explain_error(text))
            elif sev == "warning":
                warnings.append(text)
                warning_explanations.append(self._explain_warning(text))
            # info 忽略

        for raw_line in output.splitlines():
            m = self._DIAG_RE.match(raw_line.strip())
            if m:
                flush()
                sev = m.group("sev")
                # 起始行本身（含文件位置），续行追加在后面
                body = [raw_line.strip()]
            elif raw_line.strip() == "":
                # 空行视为诊断之间的分隔
                flush()
                sev = None
                body = []
            elif sev is not None:
                # 续行（expected/got、⊢ 目标、vs 等），保留原始缩进
                body.append(raw_line.rstrip())
        flush()

        success = returncode == 0 and len(errors) == 0
        return CompilationResult(
            success=success,
            errors=errors,
            warnings=warnings,
            error_explanations=error_explanations,
            warning_explanations=warning_explanations,
        )

    def _explain_error(self, line: str) -> str:
        """对常见错误给出中文解释"""
        if "unknown identifier" in line:
            if "add_zero" in line or "add_succ" in line or "succ_add" in line:
                return "该引理应在对应类型的命名空间下，如 Nat.add_zero（Lean4 标准库引理都在类型命名空间内）"
            return "未知标识符：检查拼写或是否需要导入对应模块（import Mathlib）"
        if "expected" in line and "got" in line:
            return "类型不匹配：检查表达式类型是否符合预期"
        if "unsolved goals" in line:
            return "证明未完成：仍有待证目标，需要补充更多 tactic 或引理"
        if "type mismatch" in line:
            return "类型不匹配：检查引理的参数顺序和类型"
        if "no goals" in line:
            return "已经没有目标但仍在尝试证明，可能多余的 tactic"
        if "token" in line and "expected" in line:
            return "语法错误：可能缺少括号、关键字拼写错误"
        if "synthetic" in line:
            return "Lean4 的可判定过程未能自动证明，需手动提供证明"
        return "编译错误，需检查代码语法和引理使用"

    def _explain_warning(self, line: str) -> str:
        if "sorry" in line:
            return "使用了 sorry 占位符，证明未真正完成，建议补充完整证明"
        if "deprecated" in line:
            return "使用了已废弃的语法，建议改用新写法"
        return "Lean4 警告，建议修正"

    # ---------- 模拟编译（未安装 lean） ----------

    def _compile_mock(self, lean_code: str) -> CompilationResult:
        """无 lean 时的静态语法检查"""
        errors = []
        warnings = []
        error_explanations = []
        warning_explanations = []

        lines = lean_code.splitlines()

        # 1. 检查 sorry / admit
        for i, line in enumerate(lines, 1):
            stripped = line.split("--")[0].strip()
            if re.search(r"\bsorry\b", stripped):
                warnings.append(f"第{i}行: 使用了 'sorry'")
                warning_explanations.append("sorry 是占位符，证明未真正完成")
            if re.search(r"\badmit\b", stripped):
                warnings.append(f"第{i}行: 使用了 'admit'")
                warning_explanations.append("admit 是占位符，证明未真正完成")

        # 2. 检查括号配对
        parens = {"(": ")", "{": "}", "[": "]"}
        stack = []
        for i, line in enumerate(lines, 1):
            for ch in line:
                if ch in parens:
                    stack.append((ch, i))
                elif ch in parens.values():
                    if not stack:
                        errors.append(f"第{i}行: 多余的 '{ch}'")
                        error_explanations.append("括号不匹配：右括号没有对应的左括号")
                    else:
                        top, _ = stack.pop()
                        if parens[top] != ch:
                            errors.append(f"第{i}行: 括号不匹配，期望 '{parens[top]}' 但得到 '{ch}'")
                            error_explanations.append("括号类型不匹配")
        for top, ln in stack:
            errors.append(f"第{ln}行: '{top}' 未闭合")
            error_explanations.append("括号未闭合")

        # 3. 检查 theorem 声明格式
        has_theorem = any(re.search(r"\btheorem\b", line) for line in lines)
        if not has_theorem:
            errors.append("缺少 'theorem' 声明")
            error_explanations.append("Lean4 代码应包含 theorem 声明")

        # 4. 检查 := by
        if has_theorem:
            full_code = " ".join(lines)
            if ":= by" not in full_code and ":=" not in full_code:
                errors.append("theorem 声明缺少 ':= by' 或 ':='")
                error_explanations.append("theorem 声明后应有 := by 加 tactic 证明，或 := 加 term 证明")

        # 5. 检查 import
        if not any(line.strip().startswith("import") for line in lines):
            warnings.append("缺少 'import' 语句")
            warning_explanations.append("建议添加 import Mathlib 以使用标准库")

        success = len(errors) == 0
        return CompilationResult(
            success=success,
            errors=errors,
            warnings=warnings,
            error_explanations=error_explanations,
            warning_explanations=warning_explanations,
        )
