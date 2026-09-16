"""
NLP-to-Lean4 辅助证明助手
Streamlit 主入口

启动：streamlit run app.py
"""

import os
import sys

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# 确保能 import 同目录下的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import ProofAgent, AgentResult, run_demo
from compiler import LeanCompiler
from llm_client import LLMClient, PRESET_MODELS


# ============ 页面配置 ============

st.set_page_config(
    page_title="NLP-to-Lean4 辅助证明助手",
    page_icon="∮",
    layout="wide",
)


# ============ 状态初始化 ============

if "lean_compiler" not in st.session_state:
    st.session_state.lean_compiler = LeanCompiler()
if "last_result" not in st.session_state:
    st.session_state.last_result = None


# ============ 标题区 ============

st.title("∮ NLP-to-Lean4 辅助证明助手")
st.caption("基于 LLM + Agent 反馈闭环：中文数学命题 → Lean4 形式化证明代码")

# 状态徽章
compiler = st.session_state.lean_compiler
if compiler.has_mathlib:
    st.success("Lean4 + Mathlib 已就绪（完整模式，支持高级定理）", icon="✅")
elif compiler.available:
    st.success("Lean4 编译器已就绪（核心库模式，不支持 Mathlib 专属定理）", icon="✅")
else:
    st.warning("未检测到 Lean4，将使用静态语法检查（模拟模式）", icon="⚠️")


# ============ 侧边栏设置 ============

with st.sidebar:
    st.header("⚙️ 设置")

    # 模型选择
    model_keys = list(PRESET_MODELS.keys())
    default_idx = model_keys.index("deepseek-chat") if "deepseek-chat" in model_keys else 0
    model_name = st.selectbox(
        "LLM 模型",
        options=model_keys,
        index=default_idx,
        format_func=lambda k: f"{k} - {PRESET_MODELS[k]['label']}",
        help="选择要使用的大模型。留空 API Key 可使用演示模式",
    )

    # API Key 输入
    api_key = st.text_input(
        "API Key",
        value=os.getenv(PRESET_MODELS[model_name]["env_key"], ""),
        type="password",
        placeholder="留空则使用演示模式",
        help="Key 仅本次会话使用，不会上传",
    )

    # 高级参数
    max_iter = st.slider("最大迭代次数", min_value=1, max_value=10, value=5, step=1)
    use_few_shot = st.checkbox("使用 Few-shot 示例", value=True)

    st.divider()

    # 演示按钮
    st.subheader("演示模式（无需 API Key）")
    demo_btn_add = st.button("演示：加法交换律", use_container_width=True)
    demo_btn_even = st.button("演示：偶数平方", use_container_width=True)


# ============ 主区域：输入与执行 ============

st.subheader("📝 命题输入")
user_input = st.text_area(
    "在此输入中文自然语言数学命题",
    height=120,
    placeholder="例如：证明：对任意自然数 n，n + 0 = n",
    label_visibility="collapsed",
)

# 执行按钮
col_run, col_hint = st.columns([1, 3])
with col_run:
    run_btn = st.button("🚀 开始生成", type="primary", use_container_width=True)
with col_hint:
    st.caption("按 Ctrl+Enter 快速提交 · 演示模式无需 API Key")


# ============ 执行逻辑 ============

def execute_demo(demo_key: str):
    """演示模式：直接返回预置结果"""
    with st.status(f"运行演示：{demo_key}", expanded=True) as status:
        st.write("📥 加载预置迭代序列...")
        result = run_demo(demo_key)
        st.write(f"✅ 共 {result.total_rounds} 轮迭代，最终{'通过' if result.pass_compilation else '未通过'}编译")
        status.update(label="演示完成", state="complete")
    return result


def execute_real(input_text: str, model: str, key: str, max_iter: int, few_shot: bool):
    """真实模式：调用 LLM + 编译器"""
    with st.status("调用 LLM 生成 Lean4 代码...", expanded=True) as status:
        try:
            st.write(f"🔌 连接模型：{model}")
            llm = LLMClient.from_preset(model, api_key=key)

            agent = ProofAgent(
                llm_client=llm,
                compiler=st.session_state.lean_compiler,
                max_iterations=max_iter,
                use_few_shot=few_shot,
            )

            # 进度回调
            def on_progress(round_num, _iter, status_text):
                if status_text == "generating":
                    st.write(f"🔄 第 {round_num} 轮：调用 LLM {'生成' if round_num == 1 else '修正'}代码...")
                elif status_text == "compiling":
                    st.write(f"⚙️ 第 {round_num} 轮：编译检查中...")

            st.write("🤖 启动 Agent 反馈闭环")
            result = agent.run(input_text, on_progress=on_progress)

            if result.success:
                status.update(label=f"✅ 编译通过（{result.total_rounds} 轮）", state="complete")
            else:
                status.update(label=f"⚠️ 未通过编译（{result.total_rounds} 轮）", state="error")
            return result
        except Exception as e:
            status.update(label=f"❌ 执行失败: {e}", state="error")
            st.error(f"错误：{e}")
            return None


# 触发执行
if demo_btn_add:
    st.session_state.last_result = execute_demo("加法交换律")
elif demo_btn_even:
    st.session_state.last_result = execute_demo("偶数平方")
elif run_btn:
    if not user_input.strip():
        st.error("请先输入数学命题")
    elif not api_key:
        # 没 Key 自动走演示模式（按用户输入匹配）
        st.info("未填入 API Key，自动进入演示模式")
        demo_key = "加法交换律"  # 默认
        if "偶数" in user_input or "平方" in user_input:
            demo_key = "偶数平方"
        st.session_state.last_result = execute_demo(demo_key)
    else:
        if not LLMClient.is_available():
            st.error("未安装 openai 包，请运行：pip install openai")
        else:
            with st.spinner("执行中..."):
                st.session_state.last_result = execute_real(
                    user_input.strip(), model_name, api_key, max_iter, use_few_shot
                )


# ============ 结果展示 ============

def render_result(result: AgentResult):
    """渲染执行结果"""
    st.divider()

    # 摘要
    if result.pass_compilation:
        st.success(
            f"✅ **编译通过！** 经过 {result.total_rounds} 轮迭代，Lean4 代码编译成功"
            + ("" if result.lean4_available else "（模拟模式）"),
            icon="✅",
        )
    else:
        st.warning(
            f"⚠️ **未通过编译** 经过 {result.total_rounds} 轮迭代，代码仍有编译错误，请查看迭代历史"
            + ("" if result.lean4_available else "（模拟模式）"),
            icon="⚠️",
        )

    # 最终代码
    st.subheader("📄 最终 Lean4 代码")
    if result.final_explanation:
        st.info(f"💡 {result.final_explanation}")
    st.code(result.final_code, language="lean")

    # 一键复制按钮（通过下载实现）
    st.download_button(
        label="📥 导出为 .lean 文件",
        data=result.final_code,
        file_name="proof.lean",
        mime="text/plain",
    )

    # 迭代历史
    st.subheader(f"🔄 Agent 迭代历史（共 {len(result.iterations)} 轮）")

    if not result.iterations:
        st.info("无迭代记录")
        return

    for it in result.iterations:
        title = f"第 {it.round} 轮"
        if it.compilation.success:
            title += " ✅ 编译通过"
        else:
            title += f" ❌ {len(it.compilation.errors)} 个错误"

        with st.expander(title, expanded=(it.round == 1)):
            # 说明
            if it.explanation:
                st.markdown(f"**💡 说明**：{it.explanation}")

            # 代码
            st.code(it.code, language="lean")

            # 错误
            if it.compilation.errors:
                st.markdown(f"**❌ 编译错误 ({len(it.compilation.errors)})**")
                for i, err in enumerate(it.compilation.errors):
                    st.markdown(f"- `{err}`")
                    if i < len(it.compilation.error_explanations):
                        st.markdown(f"  - 💡 {it.compilation.error_explanations[i]}")

            # 警告
            if it.compilation.warnings:
                st.markdown(f"**⚠️ 警告 ({len(it.compilation.warnings)})**")
                for i, warn in enumerate(it.compilation.warnings):
                    st.markdown(f"- `{warn}`")
                    if i < len(it.compilation.warning_explanations):
                        st.markdown(f"  - 💡 {it.compilation.warning_explanations[i]}")

            # 提示是哪一轮
            if it.round == 1:
                st.caption("（初始生成）")
            else:
                st.caption("（基于上一轮错误修正）")


result: AgentResult | None = st.session_state.last_result
if not result:
    st.divider()
    st.markdown("""
    ### 👋 欢迎使用

    **使用流程**：
    1. 在左侧填入数学命题，或在侧边栏选择 LLM 模型并填入 API Key
    2. 点击 **🚀 开始生成**，或直接点演示按钮（无需 Key）
    3. 查看右侧的迭代历史，了解 Agent 反馈闭环过程

    **演示模式**：不填 API Key，点击侧边栏的「演示」按钮即可看到完整闭环。

    **反馈闭环**：LLM 生成 → 编译检查 → 错误反馈 → LLM 修正 → 重复，最多 5 轮。
    """)
else:
    render_result(result)
