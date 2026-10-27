"""
NLP-to-Lean4 辅助证明助手
Streamlit 主入口

Tab1 命题生成：中文命题 → LLM + 编译反馈闭环 → 可编译 Lean4 证明
Tab1.5 结构化形式化：中文命题 → 高可读结构化 Lean4 证明（中文分步注释/命名中间结论/方法总结）
Tab2 证明翻译：Lean4 theorem → 结构化中文讲解
Tab3 证明检查：静态清单 + 真实编译 + #print axioms 伪证明检测
Tab4 项目管理：.lean 扫描 / sorry 定位 / lake build / AI 诊断
Tab5 定理检索与形式化：本地知识库加权检索 → LLM 候选兜底 → Lean 内核 #check 认证 → 陈述级形式化

启动：streamlit run app.py
"""

import base64
import json
import os
import sys
import threading

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# 确保能 import 同目录下的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import ProofAgent, AgentResult, run_demo
from checker_agent import (
    ProofChecker, CheckResult,
    LEVEL_PASS, LEVEL_ERROR, LEVEL_WARNING, LEVEL_INFO,
    SAMPLE_CHECK_CODE,
)
from compiler import LeanCompiler
from llm_client import LLMClient, PRESET_MODELS, model_supports_vision
from project_manager import (
    ProjectManager, ProjectScan, BuildReport,
    build_advisor_prompt, PROJECT_ADVISOR_SYSTEM_PROMPT,
)
from prompts import (
    FORMALIZE_SYSTEM,
    THEOREM_CANDIDATES_SYSTEM,
    build_formalize_fix_prompt,
    build_formalize_prompt,
    build_theorem_candidates_prompt,
    extract_code_from_response,
    extract_theorem_candidates,
)
from theorem_kb import TheoremEntry, get_entry, search_theorems
from translator import LeanTranslator, get_demo_translation


# ============ 页面配置 ============

st.set_page_config(
    page_title="NLP-to-Lean4 辅助证明助手",
    page_icon="∮",
    layout="wide",
    initial_sidebar_state="collapsed",
)

_WS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============ API Key 本地持久化（刷新/重启/换目录启动都不丢） ============
# 存储位置移到用户目录（%APPDATA%），不再放在仓库里，
# 避免 git clean、删仓库重克隆、同步盘回滚等操作把 Key 一并带走。

_KEYS_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "nlp-lean4"
)
_KEYS_FILE = os.path.join(_KEYS_DIR, "api_keys.json")
# 旧位置（仓库内），首次运行时自动迁移
_LEGACY_KEYS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".api_keys.json")


def _load_saved_keys() -> dict:
    """读取本地保存的 API Key 映射 {env_key: key}（含旧位置一次性迁移）"""
    def _read(path: str) -> dict | None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    data = _read(_KEYS_FILE)
    if data is None and os.path.exists(_LEGACY_KEYS_FILE):
        old = _read(_LEGACY_KEYS_FILE)
        if old:
            _write_keys(old)  # 迁移旧文件到新位置
            return old
    return data or {}


def _write_keys(data: dict):
    """原子写入：先写临时文件再 os.replace，避免半截文件覆盖旧数据"""
    try:
        os.makedirs(_KEYS_DIR, exist_ok=True)
        tmp = _KEYS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _KEYS_FILE)
    except OSError:
        pass


def _save_key(env_key: str, key: str):
    """把 API Key 合并写入用户目录的存储文件（不在仓库内，不会进版本库）"""
    _write_keys({**_load_saved_keys(), env_key: key})


def _remove_key(env_key: str):
    """删除本地保存的某个 API Key"""
    data = _load_saved_keys()
    data.pop(env_key, None)
    _write_keys(data)


def _save_active_key(env_key: str):
    """记住当前激活的 API Key（刷新页面后仍选中它）"""
    _write_keys({**_load_saved_keys(), "_active_key": env_key})


def _save_model_choice(env_key: str, model: str):
    """记住每把 Key 下用户选的模型（一键一模型，互不影响）"""
    data = _load_saved_keys()
    models = data.get("_models")
    if not isinstance(models, dict):
        models = {}
    if models.get(env_key) == model:
        return
    models[env_key] = model
    data["_models"] = models
    _write_keys(data)


# ============ 图片上传辅助（供支持视觉的模型读取） ============

def _encode_images(files) -> list[str]:
    """把 Streamlit 上传的图片文件转成 data URL 列表"""
    urls: list[str] = []
    for f in files or []:
        try:
            raw = f.read()
        except Exception:
            continue
        if not raw:
            continue
        mime = getattr(f, "type", None) or "image/png"
        b64 = base64.b64encode(raw).decode("ascii")
        urls.append(f"data:{mime};base64,{b64}")
    return urls


def image_uploader(key: str, model: str) -> list[str]:
    """渲染图片上传控件。
    - 模型不支持视觉时：提示并忽略图片，返回空列表
    - 返回当前选中图片的 data URL 列表
    """
    files = st.file_uploader(
        "📷 附加图片（可选，供支持视觉的模型读取，如手写题目/图表）",
        type=["png", "jpg", "jpeg", "webp", "gif"],
        accept_multiple_files=True,
        key=key,
    )
    if not files:
        return []
    if not model_supports_vision(model):
        st.warning(
            f"当前模型 **{model}** 不支持读取图片，已上传的图片将被忽略。"
            "如需读图，请在侧边栏改用 GPT-4o / GPT-4o Mini 或 Qwen VL Max（视觉）。",
            icon="🖼️",
        )
        return []
    return _encode_images(files)


# ============ 后台任务运行器（让长任务可"随时取消"） ============
# Streamlit 脚本运行期间按钮点击会被排队，无法即时打断。
# 方案：Agent 闭环放到后台线程，界面用 st.fragment 定时轮询渲染进度，
# 「⏹ 停止」按钮在 fragment 内即时响应，置位取消标志，Agent 在最近安全点中止。

_RUNS: dict[str, dict] = {}


def _start_run(run_key: str, agent: ProofAgent, input_text: str, images: list[str] | None):
    """后台线程启动 Agent 闭环"""
    cancel = threading.Event()
    state = {
        "progress": [],          # 只存阶段转换 (round, status)
        "compile_heartbeat": None,  # 编译心跳 (round, secs)，原地更新不堆叠
        "result": None,
        "error": None,
        "done": False,
        "cancel": cancel,
    }
    _RUNS[run_key] = state

    def _on_progress(r, _i, s, detail=None):
        # detail 非 None 表示编译心跳：原地更新秒数，不往列表里堆重复行
        if detail is not None:
            state["compile_heartbeat"] = (r, detail)
        else:
            state["progress"].append((r, s))
            state["compile_heartbeat"] = None  # 新阶段开始，清空上次心跳

    def work():
        try:
            state["result"] = agent.run(
                input_text,
                on_progress=_on_progress,
                images=images,
                should_stop=cancel.is_set,
            )
        except Exception as e:
            state["error"] = str(e)
        finally:
            state["done"] = True

    threading.Thread(target=work, daemon=True).start()


def _render_live_run(run_key: str, result_key: str, label: str):
    """轮询渲染运行状态 + 停止按钮；完成后把结果写入 session_state 并整页重跑"""
    state = _RUNS.get(run_key)
    if not state:
        return

    @st.fragment(run_every=2)
    def _watch():
        if state["done"]:
            _RUNS.pop(run_key, None)
            if state["error"]:
                st.session_state[f"{result_key}_err"] = state["error"]
                st.session_state[result_key] = None
            else:
                st.session_state[result_key] = state["result"]
            st.rerun()

        with st.status(label, expanded=True, state="running"):
            # progress 只含阶段转换 (round, status)；编译心跳单独原地更新
            for r, s in state["progress"]:
                if s == "generating":
                    st.write(f"🔄 第 {r} 轮：调用 LLM {'生成' if r == 1 else '修正'}代码...")
                elif s == "compiling":
                    hb = state.get("compile_heartbeat")
                    if hb and hb[0] == r:
                        secs = hb[1]
                        extra = "（这条提示会原地更新秒数，不代表卡住）" if secs >= 60 else ""
                        st.write(f"⚙️ 第 {r} 轮：Lean 编译检查中... 已等待 {secs} 秒{extra}")
                    else:
                        st.write(f"⚙️ 第 {r} 轮：Lean 编译检查中...")
        if st.button("⏹ 停止", key=f"stop_{run_key}"):
            state["cancel"].set()
            st.toast("已请求停止，将在当前步骤结束后取消", icon="⏹")

    _watch()


# ============ 后台检查运行器（证明检查：编译心跳 + 实时进度） ============

def _start_check_run(run_key: str, checker, code: str, use_llm_review: bool, images: list[str] | None):
    """后台线程启动证明检查；checker.check 的 on_progress 把阶段与编译心跳写进 state"""
    state = {
        "progress": [],            # 阶段列表 ["static", "compiling", "llm"]
        "compile_heartbeat": None,  # 编译心跳秒数
        "result": None,
        "error": None,
        "done": False,
    }
    _RUNS[run_key] = state

    def _on_progress(status, detail=None):
        if detail is not None:
            state["compile_heartbeat"] = detail
        else:
            state["progress"].append(status)
            state["compile_heartbeat"] = None

    def work():
        try:
            state["result"] = checker.check(
                code,
                use_llm_review=use_llm_review,
                images=images,
                on_progress=_on_progress,
            )
        except Exception as e:
            state["error"] = str(e)
        finally:
            state["done"] = True

    threading.Thread(target=work, daemon=True).start()


def _render_check_run(run_key: str, result_key: str, label: str):
    """轮询渲染证明检查进度；完成后写入 session_state 并整页重跑"""
    state = _RUNS.get(run_key)
    if not state:
        return

    @st.fragment(run_every=2)
    def _watch():
        if state["done"]:
            _RUNS.pop(run_key, None)
            if state["error"]:
                st.session_state[f"{result_key}_err"] = state["error"]
                st.session_state[result_key] = None
            else:
                st.session_state[result_key] = state["result"]
            st.rerun()

        with st.status(label, expanded=True, state="running"):
            for s in state["progress"]:
                if s == "static":
                    st.write("🧪 执行静态检查清单...")
                elif s == "compiling":
                    hb = state.get("compile_heartbeat")
                    if hb is not None:
                        st.write(f"⚙️ Lean 编译检查中... 已等待 {hb} 秒（Mathlib 首次加载可能较慢）")
                    else:
                        st.write("⚙️ Lean 编译检查中...")
                elif s == "llm":
                    st.write("🤖 LLM 深度审查中...")

    _watch()


@st.cache_data(show_spinner=False)
def load_env_info() -> tuple[str, str]:
    """读取本环境的 Lean 工具链与 Mathlib 锁定版本（用于展示检索依据）"""
    lean_tc, mathlib_rev = "", ""
    try:
        with open(os.path.join(_WS_DIR, "lean-toolchain"), encoding="utf-8") as f:
            lean_tc = f.read().strip()
    except OSError:
        pass
    try:
        with open(os.path.join(_WS_DIR, "lake-manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        for pkg in manifest.get("packages", []):
            if pkg.get("name") == "mathlib":
                mathlib_rev = (pkg.get("rev") or "")[:12]
                break
    except (OSError, json.JSONDecodeError):
        pass
    return lean_tc, mathlib_rev


# ============ 状态初始化 ============

if "lean_compiler" not in st.session_state:
    st.session_state.lean_compiler = LeanCompiler()
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "struct_result" not in st.session_state:
    st.session_state.struct_result = None
if "check_result" not in st.session_state:
    st.session_state.check_result = None
# 定理检索相关：签名缓存 / 检索结果 / 形式化结果
if "thm_sig_cache" not in st.session_state:
    st.session_state.thm_sig_cache = {}
if "thm_results" not in st.session_state:
    st.session_state.thm_results = None
if "thm_form" not in st.session_state:
    st.session_state.thm_form = None


# ============ 标题区 ============

st.title("∮ NLP-to-Lean4 辅助证明助手")
st.caption("基于 LLM + Agent 反馈闭环：中文数学命题 ↔ Lean4 形式化证明")

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

    # ---- 多 API 配置：一把 Key 一个配置项，点选 Key 后再选其下模型 ----
    st.subheader("🔑 API 配置")
    _saved_keys = _load_saved_keys()
    model_keys = list(PRESET_MODELS.keys())

    # 服务商显示名（按 Key 的环境变量名分组）
    _PROVIDER_NAMES = {
        "OPENAI_API_KEY": "OpenAI",
        "DEEPSEEK_API_KEY": "DeepSeek",
        "QWEN_API_KEY": "通义千问",
        "MOONSHOT_API_KEY": "Kimi",
    }

    def _key_for(env_key: str) -> str:
        """本地记忆优先，其次环境变量"""
        return _saved_keys.get(env_key, "") or os.getenv(env_key, "")

    def _mask(k: str) -> str:
        return k if len(k) <= 8 else f"{k[:4]}…{k[-4:]}"

    # 按 PRESET_MODELS 声明顺序，把模型归组到各自的 Key 下
    _env_order: list[str] = []
    _env_models: dict[str, list[str]] = {}
    for _m in model_keys:
        _e = PRESET_MODELS[_m]["env_key"]
        if _e not in _env_models:
            _env_models[_e] = []
            _env_order.append(_e)
        _env_models[_e].append(_m)

    # 已配置 Key 的服务商：一把 Key = 一个配置项
    configured_envs = [e for e in _env_order if _key_for(e)]

    # 激活 Key：优先上次点选的；兼容旧字段（_active 曾存模型名）
    _active_env = _saved_keys.get("_active_key", "")
    if _active_env not in configured_envs:
        _old = _saved_keys.get("_active", "")
        if _old in PRESET_MODELS and PRESET_MODELS[_old]["env_key"] in configured_envs:
            _active_env = PRESET_MODELS[_old]["env_key"]
    if _active_env not in configured_envs:
        _active_env = ""

    if configured_envs:
        _default_env = (
            _active_env
            or ("DEEPSEEK_API_KEY" if "DEEPSEEK_API_KEY" in configured_envs else configured_envs[0])
        )
        active_env = st.radio(
            "当前使用的 API Key（点选切换）",
            options=configured_envs,
            index=configured_envs.index(_default_env),
            format_func=lambda e: f"{_PROVIDER_NAMES.get(e, e)} · {_mask(_key_for(e))}",
            key="api_active_env",
        )
        if active_env != _saved_keys.get("_active_key"):
            _save_active_key(active_env)

        # 第二级：这把 Key 下可用的模型，记住各自上次的选择
        _ms = _env_models[active_env]
        _models_pref = _saved_keys.get("_models")
        _pref = _models_pref.get(active_env, "") if isinstance(_models_pref, dict) else ""
        if _pref not in _ms and _saved_keys.get("_active", "") in _ms:
            _pref = _saved_keys["_active"]  # 首次迁移：沿用旧字段记住的模型
        model_name = st.selectbox(
            "使用的模型",
            options=_ms,
            index=_ms.index(_pref) if _pref in _ms else 0,
            format_func=lambda k: PRESET_MODELS[k]["label"],
            key=f"api_model_{active_env}",
        )
        _save_model_choice(active_env, model_name)
    else:
        _default_model = "deepseek-chat" if "deepseek-chat" in model_keys else model_keys[0]
        model_name = st.selectbox(
            "LLM 模型",
            options=model_keys,
            index=model_keys.index(_default_model),
            format_func=lambda k: f"{k} - {PRESET_MODELS[k]['label']}",
            help="尚未配置任何 API Key：可在下方「管理 API Key」添加，或直接使用演示模式",
        )

    # 当前激活模型的可用 Key
    api_key = _key_for(PRESET_MODELS[model_name]["env_key"])

    # ---- 管理 API Key：按服务商增删 Key ----
    with st.expander("🗂 管理 API Key", expanded=not configured_envs):
        manage_env = st.selectbox(
            "服务商（一把 Key 一个账号）",
            options=_env_order,
            index=_env_order.index("DEEPSEEK_API_KEY") if "DEEPSEEK_API_KEY" in _env_order else 0,
            format_func=lambda e: f"{_PROVIDER_NAMES.get(e, e)}（{_env_models[e][0]} 等 {len(_env_models[e])} 个模型）",
            key="api_manage_env",
        )
        _m_cur = _key_for(manage_env)
        if _m_cur:
            _src = "本地记忆" if _saved_keys.get(manage_env) else "环境变量"
            st.caption(f"当前可用：`{_mask(_m_cur)}`（来自{_src}）")
        new_key = st.text_input(
            "新 API Key",
            type="password",
            placeholder=f"填入 {manage_env} 的 Key",
            key="api_manage_key",
        )
        col_save, col_del = st.columns(2)
        with col_save:
            if st.button("💾 保存并启用", use_container_width=True, key="api_save"):
                k = (new_key or "").strip()
                if not k:
                    st.warning("请先输入 Key")
                else:
                    _save_key(manage_env, k)
                    _save_active_key(manage_env)
                    st.rerun()
        with col_del:
            if st.button("🗑 删除本地 Key", use_container_width=True, key="api_del"):
                if _saved_keys.get(manage_env):
                    _remove_key(manage_env)
                    if "api_active_env" in st.session_state:
                        del st.session_state["api_active_env"]
                    st.rerun()
                else:
                    st.info("该服务商没有本地保存的 Key（可能来自环境变量，无法在此删除）")

    # 高级参数
    max_iter = st.slider("最大迭代次数", min_value=1, max_value=10, value=5, step=1)
    use_few_shot = st.checkbox("使用 Few-shot 示例", value=True)

    st.divider()

    # 命题生成演示按钮
    st.subheader("命题生成演示")
    demo_btn_add = st.button("演示：加法交换律", use_container_width=True, key="demo_gen_add")
    demo_btn_even = st.button("演示：偶数平方", use_container_width=True, key="demo_gen_even")

    # 翻译演示选择
    st.subheader("证明翻译演示")
    demo_trans_keys = ["add_zero", "zero_add", "and_comm", "even_square_div4"]
    demo_trans_labels = ["add_zero (rfl)", "zero_add (simp)", "and_comm (↔证明)", "even_square_div4"]
    demo_trans_choice = st.selectbox(
        "预置示例",
        options=demo_trans_keys,
        format_func=lambda k: demo_trans_labels[demo_trans_keys.index(k)],
        key="demo_trans_choice",
    )

    st.divider()
    lean_tc, mathlib_rev = load_env_info()
    st.caption(f"Lean 工具链：`{lean_tc or '未检测到'}`")
    st.caption(f"Mathlib rev：`{mathlib_rev or '未锁定'}`")


# ============ Tab 结构 ============

tab_gen, tab_struct, tab_trans, tab_chk, tab_proj, tab_theorem = st.tabs(
    ["🪄 命题证明", "📐 结构化形式化", "📖 证明翻译", "🔍 证明检查", "📁 项目管理", "📚 定理检索与形式化"]
)


# =====================================================
# Tab 1：命题生成
# =====================================================
with tab_gen:
    st.subheader("📝 命题输入")
    user_input = st.text_area(
        "在此输入中文自然语言数学命题",
        height=120,
        placeholder="例如：证明：对任意自然数 n，n + 0 = n",
        label_visibility="collapsed",
        key="gen_user_input",
    )

    gen_images = image_uploader("gen_images", model_name)

    col_run, col_hint = st.columns([1, 3])
    with col_run:
        run_btn = st.button("🚀 开始生成", type="primary", use_container_width=True, key="gen_run")
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


    def _launch(input_text: str, images: list[str] | None, structured: bool, run_key: str):
        """构造 Agent 并后台启动（可取消）；structured=True 时生成高可读结构化证明"""
        llm = LLMClient.from_preset(model_name, api_key=api_key)
        agent = ProofAgent(
            llm_client=llm,
            compiler=st.session_state.lean_compiler,
            max_iterations=max_iter,
            use_few_shot=use_few_shot,
            structured=structured,
        )
        _start_run(run_key, agent, input_text, images)


    # 触发执行
    if demo_btn_add:
        st.session_state.last_result = execute_demo("加法交换律")
    elif demo_btn_even:
        st.session_state.last_result = execute_demo("偶数平方")
    elif run_btn:
        if not user_input.strip() and not gen_images:
            st.error("请先输入数学命题，或上传一张题目图片（需视觉模型）")
        elif not api_key:
            st.info("未填入 API Key，自动进入演示模式")
            demo_key = "加法交换律"
            if "偶数" in user_input or "平方" in user_input:
                demo_key = "偶数平方"
            st.session_state.last_result = execute_demo(demo_key)
        else:
            if not LLMClient.is_available():
                st.error("未安装 openai 包，请运行：pip install openai")
            else:
                # 只传图未输文字时，指示模型先读图
                input_text = user_input.strip()
                if not input_text and gen_images:
                    input_text = (
                        "数学题目见所附图片。请先读取并转录图片中的题目，"
                        "再将其形式化为 Lean4 定理并给出证明。"
                    )
                _launch(input_text, gen_images, structured=False, run_key="gen")

    # 后台任务进行中：实时进度 + ⏹ 停止
    _render_live_run("gen", "last_result", "🤖 Agent 反馈闭环运行中...")
    _gen_err = st.session_state.pop("last_result_err", None)
    if _gen_err:
        st.error(f"错误：{_gen_err}")


    # ============ 结果展示 ============

    def render_result(result: AgentResult):
        """渲染执行结果"""
        st.divider()

        # 摘要
        if getattr(result, "cancelled", False):
            st.warning(
                f"⏹ **已手动取消**（进行到第 {result.total_rounds} 轮，下方保留已有中间结果）",
                icon="⏹",
            )
        elif result.pass_compilation:
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

        col_dl, col_tr = st.columns([1, 1])
        with col_dl:
            st.download_button(
                label="📥 导出为 .lean 文件",
                data=result.final_code,
                file_name="proof.lean",
                mime="text/plain",
            )
        with col_tr:
            st.caption("💡 可在「证明检查」Tab 一键填充此结果做深度检查")

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
                if it.explanation:
                    st.markdown(f"**💡 说明**：{it.explanation}")
                st.code(it.code, language="lean")
                if it.compilation.errors:
                    st.markdown(f"**❌ 编译错误 ({len(it.compilation.errors)})**")
                    for i, err in enumerate(it.compilation.errors):
                        st.markdown(f"- `{err}`")
                        if i < len(it.compilation.error_explanations):
                            st.markdown(f"  - 💡 {it.compilation.error_explanations[i]}")
                if it.compilation.warnings:
                    st.markdown(f"**⚠️ 警告 ({len(it.compilation.warnings)})**")
                    for i, warn in enumerate(it.compilation.warnings):
                        st.markdown(f"- `{warn}`")
                        if i < len(it.compilation.warning_explanations):
                            st.markdown(f"  - 💡 {it.compilation.warning_explanations[i]}")
                if it.round == 1:
                    st.caption("（初始生成）")
                else:
                    st.caption("（基于上一轮错误修正）")


    result: AgentResult | None = st.session_state.last_result
    if not result:
        st.divider()
        st.markdown("""
        ### 👋 欢迎使用 — 命题证明

        **使用流程**：
        1. 在上方填入中文数学命题
        2. 点击 **🚀 开始生成**（无 API Key 时自动进入演示模式）
        3. LLM + Agent 自动迭代：生成代码 → 编译检查 → 错误反馈 → 修正 → 直到通过

        **演示模式**：侧边栏「命题生成演示」按钮
        """)
    else:
        render_result(result)


# =====================================================
# Tab 1.5：结构化形式化（自然语言 → 高可读结构化 Lean4 证明）
# =====================================================
with tab_struct:
    st.subheader("📐 自然语言 → 结构化 Lean4 证明")
    st.caption(
        "生成**可读性极高**的结构化证明：docstring 定理说明、`-- 第 N 步` 中文分步注释、"
        "命名中间结论、复杂命题拆辅助引理、末尾方法总结；同样经过 Lean 编译修正闭环"
    )

    struct_input = st.text_area(
        "在此输入中文自然语言数学命题",
        height=120,
        placeholder="例如：证明：若 n 是偶数，则 n² 是 4 的倍数",
        label_visibility="collapsed",
        key="struct_user_input",
    )

    struct_images = image_uploader("struct_images", model_name)

    col_srun, col_shint = st.columns([1, 3])
    with col_srun:
        struct_run_btn = st.button("🧩 生成结构化证明", type="primary", use_container_width=True, key="struct_run")
    with col_shint:
        st.caption("需要 API Key · 修正轮会保持结构化风格不丢失")

    if struct_run_btn:
        if not struct_input.strip() and not struct_images:
            st.error("请先输入数学命题，或上传一张题目图片（需视觉模型）")
        elif not api_key:
            st.error("结构化形式化需要真实 LLM，请在侧边栏填写 API Key")
        elif not LLMClient.is_available():
            st.error("未安装 openai 包，请运行：pip install openai")
        else:
            # 只传图未输文字时，指示模型先读图
            struct_text = struct_input.strip()
            if not struct_text and struct_images:
                struct_text = (
                    "数学题目见所附图片。请先读取并转录图片中的题目，"
                    "再将其形式化为高可读结构化 Lean4 证明。"
                )
            _launch(struct_text, struct_images, structured=True, run_key="struct")

    # 后台任务进行中：实时进度 + ⏹ 停止
    _render_live_run("struct", "struct_result", "🧩 结构化证明生成中...")
    _struct_err = st.session_state.pop("struct_result_err", None)
    if _struct_err:
        st.error(f"错误：{_struct_err}")

    struct_result: AgentResult | None = st.session_state.struct_result
    if struct_result:
        render_result(struct_result)
    else:
        st.divider()
        st.markdown("""
        **与「🪄 命题证明」的区别**：同一个编译修正闭环，但强制输出教学级可读结构 ——

        | 要求 | 说明 |
        | --- | --- |
        | `/-- ... -/` docstring | 主定理前写清命题的自然语言含义 |
        | `-- 第 N 步：...` | 每个证明阶段前一行中文注释 |
        | 命名中间结论 | `have h_name : 命题 := by ...`，拒绝无名长链 |
        | 辅助引理 | 复杂命题先证 `helper_xxx` 再引用 |
        | 方法总结 | 代码末尾一行注释概括证明策略 |
        """)


# =====================================================
# Tab 2：证明翻译
# =====================================================
with tab_trans:
    st.subheader("📖 Lean4 → 自然语言翻译")
    st.caption("把 Lean4 theorem 代码翻译成结构化中文讲解，帮助理解每一步 tactic 做了什么")

    # 预置样例
    demo_trans_code_map = {
        "add_zero": "theorem add_zero (n : Nat) : n + 0 = n := rfl",
        "zero_add": "theorem zero_add (n : Nat) : 0 + n = n := by simp",
        "and_comm": """theorem and_comm (p q : Prop) : p ∧ q ↔ q ∧ p := by
  constructor
  · intro h
    exact ⟨h.2, h.1⟩
  · intro h
    exact ⟨h.2, h.1⟩""",
        "even_square_div4": """import Mathlib

theorem even_square_div4 (n : Nat) (h : Even n) : 4 ∣ n ^ 2 := by
  obtain ⟨k, hk⟩ := h
  rw [hk, Nat.mul_pow]
  exact dvd_mul_right 4 (k ^ 2)""",
    }

    # 示例操作按钮（放在输入框之前，避免对 widget key 的 session_state 赋值时序问题）
    col_fill, col_demo_show, _spare = st.columns([1, 1, 2])
    with col_fill:
        fill_btn = st.button("📥 用侧边栏示例填充输入框", use_container_width=True, key="trans_fill")
    with col_demo_show:
        demo_only_btn = st.button("🎭 仅看翻译演示（无需 LLM）", use_container_width=True, key="trans_demo_only")

    if fill_btn:
        st.session_state.trans_input = demo_trans_code_map.get(demo_trans_choice, "")
        st.info(f"已填充示例：{demo_trans_choice}")

    # 输入区
    lean_code_input = st.text_area(
        "粘贴 Lean4 theorem 代码",
        height=200,
        placeholder="例如：\ntheorem add_zero (n : Nat) : n + 0 = n := rfl",
        label_visibility="collapsed",
        key="trans_input",
    )

    trans_images = image_uploader("trans_images", model_name)

    col_trans_run, col_trans_hint = st.columns([1, 3])
    with col_trans_run:
        trans_run_btn = st.button("💬 翻译讲解", type="primary", use_container_width=True, key="trans_run")
    with col_trans_hint:
        st.caption("无 API Key 且输入与预置示例完全一致时，自动展示预置翻译")

    # 渲染翻译结果
    def render_translation(markdown: str, source_code: str, is_demo: bool = False):
        """渲染翻译结果"""
        st.divider()
        if is_demo:
            st.info("🎭 以下是预置演示翻译（静态样例，未调用 LLM）", icon="🎭")

        with st.expander("📄 原始 Lean4 代码", expanded=False):
            st.code(source_code, language="lean")

        st.markdown(markdown)

        st.download_button(
            label="📥 导出为 Markdown",
            data=markdown,
            file_name="translation.md",
            mime="text/markdown",
        )

    # 触发翻译
    if demo_only_btn:
        demo_md = get_demo_translation(demo_trans_choice)
        if demo_md:
            render_translation(demo_md, demo_trans_code_map.get(demo_trans_choice, ""), is_demo=True)
        else:
            st.warning("该示例暂无预置翻译，请使用真实模式（填入 API Key）或选其他示例")

    elif trans_run_btn:
        code = (lean_code_input or "").strip()
        if not code:
            st.error("请先粘贴 Lean4 代码，或点「📥 用示例填充」")
        elif not api_key:
            st.info("未填入 API Key，尝试匹配预置示例")
            matched_key = None
            for k, v in demo_trans_code_map.items():
                if v.strip() == code:
                    matched_key = k
                    break
            if matched_key:
                demo_md = get_demo_translation(matched_key)
                if demo_md:
                    render_translation(demo_md, code, is_demo=True)
                else:
                    st.warning("匹配到示例但暂无预置翻译")
            else:
                st.warning("未能匹配预置示例，请填入 API Key 使用真实 LLM 翻译")
        else:
            if not LLMClient.is_available():
                st.error("未安装 openai 包，请运行：pip install openai")
            else:
                result = None
                translate_error = None
                with st.status("调用 LLM 翻译中...", expanded=True) as status:
                    try:
                        llm = LLMClient.from_preset(model_name, api_key=api_key)
                        translator = LeanTranslator(
                            llm_client=llm,
                            mathlib_context=compiler.has_mathlib,
                        )
                        st.write("🔌 已连接模型，开始翻译...")
                        result = translator.translate(code, images=trans_images or None)
                        if result.success:
                            status.update(label="✅ 翻译完成", state="complete")
                        else:
                            status.update(label=f"❌ 翻译失败: {result.error}", state="error")
                    except Exception as e:
                        translate_error = str(e)
                        status.update(label=f"❌ 执行失败: {e}", state="error")

                # st.status 底层是一个 expander，不能在其内部再放 expander；
                # 因此等 status 闭合后、回到顶层再渲染翻译结果（render_translation 内含 expander）
                if translate_error:
                    st.error(f"错误：{translate_error}")
                elif result is not None and result.success:
                    render_translation(result.markdown, code)


# =====================================================
# Tab 3：证明检查
# =====================================================
with tab_chk:
    st.subheader("🔍 独立证明检查")
    st.caption("静态检查清单 + 真实编译 + sorryAx 深度检查（#print axioms）+ 可选 LLM 审查")

    # 填充按钮（在输入框之前，避免 session_state 时序问题）
    prefill_cols = st.columns([1, 1, 2])
    with prefill_cols[0]:
        prefill_gen = st.button("📥 填充最近生成结果", use_container_width=True, key="chk_prefill_gen")
    with prefill_cols[1]:
        prefill_sample = st.button("📥 填充检查示例（含 sorry）", use_container_width=True, key="chk_prefill_sample")

    last_result: AgentResult | None = st.session_state.get("last_result")
    if prefill_gen:
        if last_result and last_result.final_code:
            st.session_state.chk_input = last_result.final_code
            st.info("已填充最近一次生成的代码")
        else:
            st.warning("暂无生成结果，请先在「命题证明」Tab 运行一次")
    if prefill_sample:
        st.session_state.chk_input = SAMPLE_CHECK_CODE
        st.info("已填充检查示例（含 sorry，用于演示检查器抓伪证明）")

    chk_code = st.text_area(
        "粘贴待检查的 Lean4 代码",
        height=200,
        placeholder="theorem ... := by ...",
        label_visibility="collapsed",
        key="chk_input",
    )

    use_llm_review = st.checkbox("附加 LLM 深度审查（需 API Key）", value=False, key="chk_use_llm")
    chk_images = image_uploader("chk_images", model_name) if use_llm_review else []
    chk_run_btn = st.button("🔍 开始检查", type="primary", use_container_width=True, key="chk_run")

    # 渲染检查结果
    _LEVEL_ICON = {LEVEL_PASS: "✅", LEVEL_ERROR: "❌", LEVEL_WARNING: "⚠️", LEVEL_INFO: "ℹ️"}

    def render_check_result(res: CheckResult):
        """渲染检查结果"""
        st.divider()
        if res.passed:
            st.success(f"✅ **检查通过**：{res.summary}")
        else:
            st.error(f"❌ **检查未通过**：{res.summary}")

        for item in res.items:
            icon = _LEVEL_ICON.get(item.level, "•")
            with st.expander(f"{icon} {item.name}：{item.message}", expanded=(item.level == LEVEL_ERROR)):
                if item.details:
                    for d in item.details:
                        st.markdown(f"- `{d}`")
                else:
                    st.caption("无附加详情")

        if res.compilation and res.compilation.errors:
            st.subheader("编译错误详情")
            for err in res.compilation.errors:
                st.markdown(f"- `{err}`")

        if res.llm_review:
            st.subheader("🤖 LLM 深度审查")
            st.markdown(res.llm_review)

    if chk_run_btn:
        code = (chk_code or "").strip()
        if not code:
            st.error("请先粘贴 Lean4 代码，或用上方按钮填充")
        else:
            llm = None
            do_llm = False
            if use_llm_review:
                if not api_key:
                    st.warning("未填 API Key，跳过 LLM 审查，仅做规则检查")
                elif not LLMClient.is_available():
                    st.warning("未安装 openai 包，跳过 LLM 审查")
                else:
                    llm = LLMClient.from_preset(model_name, api_key=api_key)
                    do_llm = True

            checker = ProofChecker(compiler=st.session_state.lean_compiler, llm_client=llm)
            _start_check_run("chk", checker, code, do_llm, chk_images or None)

    # 轮询渲染进行中的检查（后台线程 + fragment 心跳，编译期间显示已等待秒数）
    _render_check_run("chk", "check_result", "🔍 证明检查中...")

    _check_err = st.session_state.pop("check_result_err", None)
    if _check_err:
        st.error(f"检查出错：{_check_err}")

    check_res: CheckResult | None = st.session_state.check_result
    if check_res:
        render_check_result(check_res)


# =====================================================
# Tab 4：项目管理
# =====================================================
with tab_proj:
    st.subheader("📁 Lean4 项目辅助管理")
    st.caption("扫描 .lean 文件、索引 theorem、发现未完成证明、批量构建、AI 项目诊断")

    # 项目根目录
    _default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "proj_root" not in st.session_state:
        st.session_state.proj_root = _default_root
    proj_root = st.text_input(
        "项目根目录（包含 lakefile.toml）",
        key="proj_root",
        help="默认为本仓库根目录；也可指向其他 Lean4 项目",
    )

    col_scan, col_build = st.columns(2)
    with col_scan:
        scan_btn = st.button("🔄 扫描项目", use_container_width=True, key="proj_scan")
    with col_build:
        build_btn = st.button("🏗️ 批量编译（lake build）", use_container_width=True, key="proj_build")

    # 执行扫描
    if scan_btn:
        with st.status("扫描中...", expanded=True) as status:
            st.write(f"📂 扫描 {proj_root} ...")
            st.session_state.proj_scan_result = ProjectManager(proj_root).scan()
            scan_tmp = st.session_state.proj_scan_result
            st.write(f"✅ {scan_tmp.total_files} 个 .lean 文件，{scan_tmp.total_decls} 个声明")
            status.update(label="扫描完成", state="complete")

    # 执行构建
    if build_btn:
        with st.status("构建中（Mathlib 首次构建可能很慢）...", expanded=True) as status:
            st.session_state.proj_build_result = ProjectManager(proj_root).build(
                compiler=st.session_state.lean_compiler
            )
            rep_tmp: BuildReport = st.session_state.proj_build_result
            if rep_tmp.skipped:
                status.update(label="已跳过构建", state="error")
            elif rep_tmp.success:
                status.update(label=f"✅ 构建成功（{rep_tmp.duration_sec:.1f}s）", state="complete")
            else:
                status.update(label=f"❌ 构建失败（{len(rep_tmp.errors)} 个错误）", state="error")

    # ---- 渲染扫描结果 ----
    scan: ProjectScan | None = st.session_state.get("proj_scan_result")
    if scan:
        st.divider()
        st.caption(f"扫描结果：{scan.root}")
        if scan.errors:
            for err in scan.errors:
                st.warning(f"扫描异常: {err}")

        # 指标行
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("📄 文件数", scan.total_files)
        m2.metric("📐 声明数", scan.total_decls)
        m3.metric("⚠️ sorry 数", scan.total_sorry)
        m4.metric("📝 总行数", scan.total_lines)

        if scan.total_sorry:
            st.error(f"发现 {scan.total_sorry} 处未完成证明（sorry/admit）")
        else:
            st.success("未发现 sorry/admit 占位符")

        # 声明索引表
        flat = scan.decls_flat()
        if flat:
            st.subheader("📋 声明索引")
            df = pd.DataFrame([
                {
                    "文件": d.file,
                    "行": d.line,
                    "类型": d.kind,
                    "名称": d.name,
                    "命名空间": d.namespace or "-",
                    "签名": d.signature[:80],
                    "sorry": "⚠️" if d.has_sorry else "",
                }
                for d in flat
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

        # import 统计
        stats = scan.import_stats()
        if stats:
            st.subheader("📦 import 统计")
            imp_items = list(stats.items())[:10]
            imp_md = "\n".join(f"- `{imp}`（{cnt} 个文件）" for imp, cnt in imp_items)
            st.markdown(imp_md)

        # sorry 详情
        files_with_sorry = [f for f in scan.files if f.sorry_details]
        if files_with_sorry:
            st.subheader("⚠️ 未完成证明详情")
            for f in files_with_sorry:
                with st.expander(f"{f.path}（{len(f.sorry_details)} 处）", expanded=True):
                    for ln, txt in f.sorry_details:
                        st.markdown(f"- **第 {ln} 行**: `{txt}`")

        # AI 项目诊断
        st.divider()
        st.subheader("🤖 AI 项目诊断")
        st.caption("将扫描摘要交给 LLM，分析项目健康度并给出改进建议")
        if st.button("🤖 开始诊断（需 API Key）", key="proj_ai"):
            if not api_key:
                st.warning("请在侧边栏填入 API Key")
            elif not LLMClient.is_available():
                st.error("未安装 openai 包，请运行：pip install openai")
            elif not scan.files:
                st.warning("扫描结果为空，请确认目录下有 .lean 文件")
            else:
                with st.status("AI 分析中...", expanded=True) as status:
                    try:
                        llm = LLMClient.from_preset(model_name, api_key=api_key)
                        st.write("📊 生成项目摘要...")
                        prompt = build_advisor_prompt(scan)
                        st.write("🤖 调用 LLM 分析...")
                        resp = llm.chat(PROJECT_ADVISOR_SYSTEM_PROMPT, prompt, temperature=0.2)
                        st.session_state.proj_ai_md = resp.strip()
                        status.update(label="✅ 诊断完成", state="complete")
                    except Exception as e:
                        status.update(label=f"❌ 诊断失败: {e}", state="error")
                        st.error(f"错误：{e}")
        ai_md = st.session_state.get("proj_ai_md")
        if ai_md:
            st.markdown(ai_md)

    # ---- 渲染构建结果 ----
    build_rep: BuildReport | None = st.session_state.get("proj_build_result")
    if build_rep:
        st.divider()
        st.subheader("🏗️ 构建结果")
        if build_rep.skipped:
            st.warning(f"已跳过：{build_rep.skip_reason}")
        elif build_rep.success:
            st.success(f"✅ 构建成功（{build_rep.duration_sec:.1f}s）")
        else:
            st.error(f"❌ 构建失败（exit {build_rep.returncode}，{build_rep.duration_sec:.1f}s），共 {len(build_rep.errors)} 个错误")
            for err in build_rep.errors[:20]:
                st.markdown(f"- `{err}`")
        if build_rep.warnings:
            st.markdown(f"**⚠️ 警告 ({len(build_rep.warnings)})**")
            for warn in build_rep.warnings[:10]:
                st.markdown(f"- `{warn}`")
        if build_rep.output:
            with st.expander("查看原始输出"):
                st.code(build_rep.output[-8000:], language="text")

    # 空状态引导
    if not scan and not build_rep:
        st.info("""
        👈 点击 **🔄 扫描项目** 开始：

        - 📋 **声明索引**：所有 theorem/def 的名称、签名、所在位置
        - ⚠️ **未完成证明**：定位所有 sorry/admit（含 AI 诊断补齐建议）
        - 🏗️ **批量编译**：lake build 一键构建并汇总错误
        - 🤖 **AI 项目诊断**：LLM 分析项目健康度与下一步路线
        """)


# =====================================================
# Tab 5：定理检索与形式化
# =====================================================
with tab_theorem:
    st.markdown(
        """
        **三段式管线**：本地定理知识库加权检索 → LLM 候选名兜底 → **Lean 内核 `#check` 探针认证**。
        只有经内核验证存在的定理才会展示真实类型签名——*LLM 可以提议，但只有内核能认证*。
        """
    )

    lean_tc, mathlib_rev = load_env_info()
    env_bits = [b for b in [lean_tc and f"Lean `{lean_tc}`",
                            mathlib_rev and f"Mathlib `{mathlib_rev}`"] if b]
    if env_bits:
        st.caption(" · ".join(env_bits) + " · 签名认证以本环境锁定版本为准")

    # ---------- 检索 ----------
    st.subheader("🔍 定理检索")
    col_q, col_btn = st.columns([4, 1])
    with col_q:
        query = st.text_input(
            "输入定理名称或自然语言描述",
            placeholder="例如：三角不等式 / 鸽巢原理 / 费马小定理 / add_comm",
            label_visibility="collapsed",
        )
    with col_btn:
        st.write("")
        search_btn = st.button("检索并认证", type="primary", use_container_width=True)

    if search_btn:
        if not query.strip():
            st.error("请先输入检索内容")
        else:
            with st.status("执行检索管线...", expanded=True) as status:
                # 1) 本地知识库加权检索
                st.write("① 本地知识库检索（45 条已认证定理，加权评分）...")
                kb_hits = search_theorems(query.strip(), top_k=6)
                kb_names = [e.mathlib_id for e, _ in kb_hits]

                # 2) 知识库无命中时：LLM 候选名兜底
                llm_names: list[str] = []
                if not kb_hits:
                    if api_key and LLMClient.is_available():
                        st.write("② 知识库未命中 → 请求 LLM 给出候选 Mathlib 名（仅候选，待认证）...")
                        try:
                            llm = LLMClient.from_preset(model_name, api_key=api_key)
                            resp = llm.chat(
                                THEOREM_CANDIDATES_SYSTEM,
                                build_theorem_candidates_prompt(query.strip()),
                            )
                            llm_names = extract_theorem_candidates(resp)
                            st.write(f"   LLM 候选：{llm_names if llm_names else '（无）'}")
                        except Exception as e:
                            st.write(f"   LLM 候选失败：{e}")
                    else:
                        st.write("② 知识库未命中，且未配置 API Key → 跳过 LLM 兜底")
                else:
                    st.write("② 知识库已命中，跳过 LLM 兜底")

                # 3) 内核 #check 批量认证（结果缓存，避免重复加载 Mathlib）
                all_names = kb_names + [n for n in llm_names if n not in kb_names]
                cache: dict = st.session_state.thm_sig_cache
                sigs: dict[str, str | None] = {}
                if all_names:
                    if compiler.kernel_available():
                        pending = [n for n in all_names if n not in cache]
                        if pending:
                            st.write(f"③ Lean 内核认证 {len(pending)} 个新标识符（#check 探针，需加载 Mathlib，约 1~2 分钟）...")
                            probed = compiler.probe_signatures(pending)
                            if probed is not None:
                                cache.update(probed)
                        sigs = {n: cache.get(n) for n in all_names}
                    else:
                        st.write("③ 内核不可用 → 无法认证，仅展示候选")
                status.update(label="检索完成", state="complete")

            # 组装结果
            results = []
            for e, score in kb_hits:
                results.append({
                    "name": e.mathlib_id, "entry": e, "score": score,
                    "sig": sigs.get(e.mathlib_id) if sigs else None,
                    "verified": bool(sigs),
                })
            for n in llm_names:
                if n in kb_names:
                    continue
                results.append({
                    "name": n, "entry": None, "score": 0.0,
                    "sig": sigs.get(n) if sigs else None,
                    "verified": bool(sigs),
                })
            st.session_state.thm_results = results

    # ---------- 结果展示 ----------
    results = st.session_state.thm_results
    if results is not None:
        st.divider()
        st.subheader("📚 检索结果")
        if not results:
            st.info("未检索到相关定理。可尝试更通用的关键词，或在侧边栏配置 API Key 启用 LLM 候选兜底。")
        else:
            verified_map: dict[str, str] = {}
            for r in results:
                e: TheoremEntry | None = r["entry"]
                header = f"**{e.zh if e else r['name']}** · `{r['name']}`"
                meta_bits = []
                if e:
                    meta_bits.append(f"领域：{e.domain}")
                    meta_bits.append(f"检索得分：{r['score']:.0f}")
                with st.expander(header, expanded=(r is results[0])):
                    if e:
                        st.markdown(f"**自然语言陈述**：{e.statement}")
                    if not r["verified"]:
                        st.warning("⚠️ 当前环境内核不可用，该条目**未经认证**", icon="⚠️")
                    elif r["sig"]:
                        st.markdown(
                            "✅ **内核签名已验证**（`#check` 于当前 Mathlib 版本实际存在）",
                        )
                        st.code(f"{r['name']} : {r['sig']}", language="lean")
                        verified_map[r["name"]] = r["sig"]
                    else:
                        st.markdown(
                            "❌ **当前 Mathlib 版本中不存在该标识符**（内核 `#check` 报 unknown identifier）",
                        )
            st.session_state.thm_verified = verified_map

    # ---------- 形式化翻译 ----------
    st.divider()
    st.subheader("📐 命题形式化翻译")
    st.caption(
        "将自然语言命题翻译为 Lean4 theorem 陈述；证明体以 `by sorry` 占位，"
        "陈述本身会经过内核编译验证（允许引用上方已认证的定理签名）。"
    )

    verified: dict[str, str] = st.session_state.get("thm_verified") or {}
    options = ["（自定义命题）"] + list(verified.keys())
    sel = st.selectbox(
        "选择参考定理（或自定义）",
        options,
        help="下拉项均为本轮检索中经内核认证的定理",
    )
    default_stmt = ""
    if sel != "（自定义命题）":
        ent = get_entry(sel)
        if ent:
            default_stmt = ent.statement
    statement = st.text_area(
        "待形式化的自然语言命题",
        value=default_stmt,
        height=90,
        placeholder="例如：对任意实数 a b，|a + b| ≤ |a| + |b|",
    )

    if st.button("📐 翻译并经内核验证", type="primary", disabled=not statement.strip()):
        if not (api_key and LLMClient.is_available()):
            st.error("形式化翻译需要 LLM：请在侧边栏选择模型并填入 API Key")
        else:
            rounds = []
            with st.status("LLM 翻译 → 内核编译验证...", expanded=True) as status:
                try:
                    llm = LLMClient.from_preset(model_name, api_key=api_key)
                    prompt = build_formalize_prompt(statement.strip(), verified)
                    final_code, final_ok = "", False
                    for rnd in range(1, 4):
                        st.write(f"🔄 第 {rnd} 轮：调用 LLM 生成/修正陈述...")
                        resp = llm.chat(FORMALIZE_SYSTEM, prompt)
                        code, expl = extract_code_from_response(resp)
                        if not code:
                            rounds.append({"round": rnd, "code": "", "ok": False,
                                           "errors": ["LLM 未返回代码块"], "expl": ""})
                            continue
                        st.write(f"⚙️ 第 {rnd} 轮：lake env lean 编译验证陈述...")
                        comp = compiler.compile_code(code)
                        rounds.append({"round": rnd, "code": code, "ok": comp.success,
                                       "errors": comp.errors, "expl": expl})
                        final_code, final_ok = code, comp.success
                        if comp.success:
                            break
                        prompt = build_formalize_fix_prompt(code, comp.errors)
                    if final_ok:
                        status.update(label="✅ 陈述通过内核编译验证", state="complete")
                    else:
                        status.update(label="⚠️ 陈述未通过编译", state="error")
                    st.session_state.thm_form = {"code": final_code, "ok": final_ok,
                                                 "rounds": rounds}
                except Exception as e:
                    status.update(label=f"❌ 执行失败: {e}", state="error")
                    st.error(f"错误：{e}")

    form = st.session_state.thm_form
    if form and form["code"]:
        if form["ok"]:
            st.success("✅ 该 Lean4 陈述已通过内核编译（证明体为 `sorry` 占位，语义待证明）", icon="✅")
        else:
            st.warning("⚠️ 经过多轮修正仍未通过编译，可查看各轮报错", icon="⚠️")
        st.code(form["code"], language="lean")
        st.download_button("📥 导出为 .lean 文件", data=form["code"],
                           file_name="statement.lean", mime="text/plain")
        for it in form["rounds"]:
            title = f"第 {it['round']} 轮 " + ("✅ 编译通过" if it["ok"] else f"❌ {len(it['errors'])} 个错误")
            with st.expander(title, expanded=(not it["ok"])):
                if it["expl"]:
                    st.markdown(f"**💡 说明**：{it['expl']}")
                if it["code"]:
                    st.code(it["code"], language="lean")
                for err in it["errors"]:
                    st.markdown(f"- `{err}`")
