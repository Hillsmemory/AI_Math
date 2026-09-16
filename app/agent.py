"""
ProofAgent: 反馈闭环 Agent
LLM 生成代码 → 编译器检查 → 报错反馈 → LLM 修正 → 重复直到通过或达到上限
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from compiler import LeanCompiler, CompilationResult
from demo_data import DemoResult, IterationRecord, get_demo
from prompts import (
    SYSTEM_PROMPT,
    get_system_prompt,
    build_user_prompt,
    build_refinement_prompt,
    extract_code_from_response,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Agent 执行结果"""
    success: bool
    user_input: str
    final_code: str = ""
    final_explanation: str = ""
    pass_compilation: bool = False
    lean4_available: bool = False
    total_rounds: int = 0
    iterations: list[IterationRecord] = field(default_factory=list)
    error_message: str = ""


class ProofAgent:
    """
    反馈闭环 Agent
    1. LLM 生成初始 Lean4 代码
    2. 编译器检查
    3. 失败则将错误反馈给 LLM 重新生成
    4. 重复直到通过或达到 max_iterations
    """

    def __init__(
        self,
        llm_client,
        compiler: LeanCompiler,
        max_iterations: int = 5,
        use_few_shot: bool = True,
    ):
        self.llm = llm_client
        self.compiler = compiler
        self.max_iterations = max_iterations
        self.use_few_shot = use_few_shot

    def run(self, user_input: str, on_progress: Optional[Callable] = None) -> AgentResult:
        """
        执行反馈闭环
        on_progress(round, iteration, status) 可选的进度回调
        """
        iterations: list[IterationRecord] = []
        current_code = ""
        current_explanation = ""

        for round_num in range(1, self.max_iterations + 1):
            if on_progress:
                on_progress(round_num, None, "generating")

            # 1. 生成代码（首轮 vs 修正轮）
            sys_prompt = get_system_prompt(self.compiler.has_mathlib)
            if round_num == 1:
                user_prompt = build_user_prompt(user_input, self.use_few_shot)
                response = self.llm.chat(sys_prompt, user_prompt)
                current_code, current_explanation = extract_code_from_response(response)
                if not current_code:
                    return AgentResult(
                        success=False,
                        user_input=user_input,
                        lean4_available=self.compiler.available,
                        error_message="LLM 未返回有效代码",
                    )
            else:
                # 修正轮：用上一轮错误做提示
                last_comp = iterations[-1].compilation
                refine_prompt = build_refinement_prompt(
                    current_code,
                    last_comp.errors,
                    last_comp.warnings,
                )
                response = self.llm.chat(sys_prompt, refine_prompt)
                new_code, new_explanation = extract_code_from_response(response)
                if not new_code:
                    new_code = current_code
                    new_explanation = new_explanation or current_explanation
                current_code = new_code
                current_explanation = new_explanation

            logger.info(f"第 {round_num} 轮生成代码（{len(current_code)} 字符）")

            # 2. 编译检查
            if on_progress:
                on_progress(round_num, None, "compiling")
            compilation = self.compiler.compile_code(current_code)

            # 3. 记录本轮迭代
            iterations.append(IterationRecord(
                round=round_num,
                code=current_code,
                explanation=current_explanation,
                compilation=compilation,
            ))

            # 4. 通过则提前返回
            if compilation.success:
                logger.info(f"✅ 第 {round_num} 轮编译通过")
                return AgentResult(
                    success=True,
                    user_input=user_input,
                    final_code=current_code,
                    final_explanation=current_explanation,
                    pass_compilation=True,
                    lean4_available=self.compiler.available,
                    total_rounds=round_num,
                    iterations=iterations,
                )

            logger.warning(f"❌ 第 {round_num} 轮编译失败：{len(compilation.errors)} 个错误")

        # 5. 达到上限仍未通过
        return AgentResult(
            success=False,
            user_input=user_input,
            final_code=current_code,
            final_explanation=current_explanation,
            pass_compilation=False,
            lean4_available=self.compiler.available,
            total_rounds=len(iterations),
            iterations=iterations,
            error_message=f"达到最大迭代次数 {self.max_iterations} 仍未通过",
        )


def run_demo(demo_key: str) -> AgentResult:
    """演示模式：直接返回预置的迭代序列"""
    demo: DemoResult = get_demo(demo_key)
    return AgentResult(
        success=True,
        user_input=demo.user_input,
        final_code=demo.final_code,
        final_explanation=demo.final_explanation,
        pass_compilation=demo.pass_compilation,
        lean4_available=False,  # 演示模式不计真实 lean 状态
        total_rounds=demo.total_rounds,
        iterations=list(demo.iterations),
    )
