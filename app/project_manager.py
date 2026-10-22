"""
ProjectManager: Lean4 项目辅助管理 MVP（目标 5）

功能：
1. 项目扫描：.lean 文件 → theorem/def 声明索引、import 统计、namespace 结构
2. 未完成证明扫描：sorry / admit 定位（精确到文件行号，并归属到声明）
3. 批量构建：lake build（需要 lake 环境），复用 LeanCompiler 的诊断解析
4. AI 项目诊断：生成项目摘要 Prompt，交由 LLM 分析健康度并给出建议
"""

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

_DECL_KINDS = r"(theorem|lemma|def|instance|abbrev|structure|class|inductive)"
_DECL_RE = re.compile(rf"^\s*{_DECL_KINDS}\s+([A-Za-z_][A-Za-z0-9_!'?]*)")
_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_.']*)")
_END_RE = re.compile(r"^\s*end\b(\s|$)")
_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_.']*)")
_SORRY_RE = re.compile(r"\b(sorry|admit)\b")
_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")

# 扫描时跳过的目录
SKIP_DIRS = {".git", ".lake", ".cache", "build", "node_modules", "__pycache__", ".venv", "venv"}


# ============ 数据结构 ============

@dataclass
class TheoremInfo:
    """单个声明（theorem/def/...）的索引信息"""
    name: str
    kind: str           # theorem / lemma / def / instance / ...
    file: str           # 相对项目根的路径
    line: int           # 1-based 行号
    signature: str      # 声明签名（压缩空白）
    namespace: str      # 所在命名空间（可能为空）
    has_sorry: bool = False


@dataclass
class FileReport:
    """单个 .lean 文件的扫描报告"""
    path: str
    read_error: str = ""
    lines_total: int = 0
    imports: list[str] = field(default_factory=list)
    namespaces: list[str] = field(default_factory=list)
    decls: list[TheoremInfo] = field(default_factory=list)
    sorry_lines: list[int] = field(default_factory=list)
    sorry_details: list[tuple[int, str]] = field(default_factory=list)  # (行号, 内容)
    todo_lines: list[int] = field(default_factory=list)


@dataclass
class ProjectScan:
    """整个项目的扫描结果"""
    root: str
    files: list[FileReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_decls(self) -> int:
        return sum(len(f.decls) for f in self.files)

    @property
    def total_sorry(self) -> int:
        return sum(len(f.sorry_lines) for f in self.files)

    @property
    def total_lines(self) -> int:
        return sum(f.lines_total for f in self.files)

    def decls_flat(self) -> list[TheoremInfo]:
        """所有声明，按文件和行号排序"""
        flat = [d for f in self.files for d in f.decls]
        return sorted(flat, key=lambda d: (d.file, d.line))

    def import_stats(self) -> dict[str, int]:
        """import 频次统计（降序）"""
        stats: dict[str, int] = {}
        for f in self.files:
            for imp in f.imports:
                stats[imp] = stats.get(imp, 0) + 1
        return dict(sorted(stats.items(), key=lambda kv: -kv[1]))


@dataclass
class BuildReport:
    """批量构建结果"""
    success: bool
    returncode: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output: str = ""
    duration_sec: float = 0.0
    skipped: bool = False
    skip_reason: str = ""


# ============ AI 诊断 Prompt ============

PROJECT_ADVISOR_SYSTEM_PROMPT = """你是一名 Lean4 项目架构与形式化数学顾问。用户会提供 Lean4 项目的扫描摘要，请基于它输出项目诊断报告。

## 输出格式（严格遵循，用中文）
### 📊 项目健康度
（2-3 句话：声明规模、完成度、组织结构的总体评价）

### 🎯 优先事项
（如存在 sorry：建议优先补齐哪些证明、按什么顺序；无 sorry 则给出加强建议）

### 🗂️ 组织建议
（文件拆分、namespace 划分、import 依赖是否合理）

### 🚀 下一步路线
（2-4 条具体的下一步形式化方向建议）

## 注意
- 只基于提供的扫描信息分析，不要编造不存在的文件或定理
- 建议要具体可执行，引用真实的文件名和定理名
"""


def build_advisor_prompt(scan: ProjectScan) -> str:
    """构造 AI 项目诊断的用户提示词（项目扫描摘要）"""
    parts = [f"项目根目录：{scan.root}", "", "## 文件清单"]
    for f in scan.files:
        if f.read_error:
            parts.append(f"- {f.path}（读取失败: {f.read_error}）")
            continue
        imports = ", ".join(f.imports) if f.imports else "无"
        parts.append(
            f"- {f.path}（{f.lines_total} 行，{len(f.decls)} 个声明，"
            f"{len(f.sorry_lines)} 处 sorry，imports: {imports}）"
        )

    flat = scan.decls_flat()
    if flat:
        parts.append("")
        parts.append("## 声明清单（最多 60 条）")
        for d in flat[:60]:
            flag = "，含 sorry" if d.has_sorry else ""
            ns = f"，namespace {d.namespace}" if d.namespace else ""
            sig = f" | {d.signature}" if d.signature else ""
            parts.append(f"- [{d.kind}] {d.name}（{d.file}:{d.line}{ns}{flag}）{sig}")

    if scan.total_sorry:
        parts.append("")
        parts.append("## 未完成证明位置")
        for f in scan.files:
            for ln, txt in f.sorry_details:
                parts.append(f"- {f.path}:{ln}: {txt}")

    parts.append("")
    parts.append("请基于以上信息，按 System Prompt 的结构给出项目诊断报告。")
    return "\n".join(parts)


# ============ 主类 ============

class ProjectManager:
    """Lean4 项目辅助管理"""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    # ---------- 项目扫描 ----------

    def scan(self) -> ProjectScan:
        """扫描项目：所有 .lean 文件的声明索引、sorry 定位、import 统计"""
        scan = ProjectScan(root=self.root)
        if not os.path.isdir(self.root):
            scan.errors.append(f"目录不存在: {self.root}")
            return scan

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in sorted(filenames):
                if fn.endswith(".lean"):
                    scan.files.append(self._scan_file(os.path.join(dirpath, fn)))
        return scan

    def _scan_file(self, path: str) -> FileReport:
        rel = os.path.relpath(path, self.root).replace(os.sep, "/")
        report = FileReport(path=rel)
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            report.read_error = str(e)
            return report

        report.lines_total = len(lines)
        ns_stack: list[str] = []  # "" 表示 section

        for idx, raw in enumerate(lines, 1):
            no_comment = raw.split("--")[0].rstrip()

            m = _IMPORT_RE.match(no_comment)
            if m and not ns_stack:
                report.imports.append(m.group(1))
                continue

            mns = _NAMESPACE_RE.match(no_comment)
            if mns:
                ns_stack.append(mns.group(1))
                continue
            if _END_RE.match(no_comment):
                if ns_stack:
                    ns_stack.pop()
                continue

            mdecl = _DECL_RE.match(no_comment)
            if mdecl:
                kind, name = mdecl.group(1), mdecl.group(2)
                ns = ".".join(p for p in ns_stack if p)
                report.decls.append(TheoremInfo(
                    name=name, kind=kind, file=report.path, line=idx,
                    signature=self._extract_signature(lines, idx - 1), namespace=ns,
                ))
                continue

            if _SORRY_RE.search(no_comment):
                report.sorry_lines.append(idx)
                report.sorry_details.append((idx, no_comment.strip()))
            if _TODO_RE.search(no_comment):
                report.todo_lines.append(idx)

        # sorry 归属到声明：位于 [decl.line, next_decl.line) 区间内即属于该声明
        for j, d in enumerate(report.decls):
            end = report.decls[j + 1].line - 1 if j + 1 < len(report.decls) else report.lines_total
            d.has_sorry = any(d.line <= ln <= end for ln in report.sorry_lines)

        report.namespaces = sorted({d.namespace for d in report.decls if d.namespace})
        return report

    @staticmethod
    def _extract_signature(lines: list[str], start0: int) -> str:
        """从声明起始行提取签名（到 := 或 where 为止，最多 4 行）"""
        parts = []
        for k in range(start0, min(start0 + 4, len(lines))):
            seg = lines[k].split("--")[0].rstrip()
            parts.append(seg.strip())
            if ":=" in seg or seg.rstrip().endswith("where"):
                break
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    # ---------- 批量构建 ----------

    def build(self, compiler=None, timeout: int = 600) -> BuildReport:
        """执行 lake build（需要 lakefile 与 lake 环境）

        Args:
            compiler: LeanCompiler 实例（复用其 .cache 环境变量与诊断解析）；可为 None
            timeout: 构建超时秒数（Mathlib 首次构建很慢，默认 600s）
        """
        has_lakefile = (
            os.path.isfile(os.path.join(self.root, "lakefile.toml"))
            or os.path.isfile(os.path.join(self.root, "lakefile.lean"))
        )
        lake = shutil.which("lake")

        if not has_lakefile:
            return BuildReport(
                success=False, returncode=-1, skipped=True,
                skip_reason=f"目录下没有 lakefile.toml / lakefile.lean：{self.root}",
            )
        if lake is None:
            return BuildReport(
                success=False, returncode=-1, skipped=True,
                skip_reason="未安装 lake，无法批量编译（可先安装 elan 工具链）",
            )

        env = compiler._subprocess_env() if compiler is not None else os.environ.copy()
        t0 = time.time()
        try:
            proc = subprocess.run(
                [lake, "build"],
                cwd=self.root, capture_output=True, text=True,
                timeout=timeout, env=env,
            )
        except subprocess.TimeoutExpired:
            return BuildReport(
                success=False, returncode=-1,
                errors=[f"构建超时（超过 {timeout} 秒）"],
                output="", duration_sec=time.time() - t0,
            )
        except Exception as e:
            return BuildReport(
                success=False, returncode=-1,
                errors=[f"构建异常: {e}"],
                output="", duration_sec=time.time() - t0,
            )

        duration = time.time() - t0
        output = (proc.stdout or "") + (proc.stderr or "")
        if compiler is not None:
            cr = compiler.parse_output(output, proc.returncode)
            errors, warnings = cr.errors, cr.warnings
        else:
            errors, warnings = [], []
        return BuildReport(
            success=proc.returncode == 0,
            returncode=proc.returncode,
            errors=errors, warnings=warnings,
            output=output, duration_sec=duration,
        )
