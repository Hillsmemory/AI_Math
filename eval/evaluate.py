"""
评测脚本：在「NLP-to-Lean4 验证集」上评测补全证明能力（路径 A）。

对照实验：每一题同时产出两个指标——
  - 单次生成（single）：LLM 一次补完证明，编译一次，过即过
  - 反馈闭环（loop）：首版失败后，把编译报错喂回 LLM 反复修正（最多 max_rounds 轮），看最终能否过

用法示例：
  python evaluate.py                      # 无参数时打印编号菜单（1=SimpleLogic 等）交互选择
  python evaluate.py --benchmark SimpleArith --limit 6   # 简单算术题
  python evaluate.py --benchmark SimpleSet --limit 5     # 简单集合题
  python evaluate.py --benchmark ProofNet --limit 20
  python evaluate.py --file minif2f_valid.jsonl --limit 30 --model deepseek-chat --max_rounds 3
  python evaluate.py --benchmark FormalMATH-Lite --limit 10 --shuffle --seed 42

判分标准：编译通过（lean returncode=0 且无 error）且 证明体不含 sorry/admit。
"""

import argparse
import json
import os
import random
import re
import sys
import time


def _ensure_lean_path():
    """把 elan/Lean 的 bin 目录加入 PATH。

    从 Spyder 等 GUI 启动时通常继承不到 elan 的环境变量，导致 Lean 检测失败、退化成「模拟模式」。
    这里在导入 compiler 之前手动兜底，保证「点运行箭头」也能真编译。
    """
    if sys.platform != "win32":
        return
    for cand in (
        os.path.join(os.path.expanduser("~"), ".elan", "bin"),
        os.path.join(os.environ.get("USERPROFILE", ""), ".elan", "bin"),
    ):
        if os.path.isdir(cand):
            os.environ["PATH"] = cand + os.pathsep + os.environ.get("PATH", "")
            return


_ensure_lean_path()

# 复用 app/ 目录下的 compiler / llm_client / prompts 模块
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from compiler import LeanCompiler
from llm_client import LLMClient, PRESET_MODELS
from prompts import (
    PROOF_COMPLETION_SYSTEM,
    build_proof_completion_prompt,
    build_proof_refine_prompt,
    extract_code_from_response,
)

_DATASET_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "验证集"
)

# benchmark 名 -> 默认 jsonl 文件
BENCHMARK_FILES = {
    # 简单题（通用模型如 deepseek-chat 也基本能补全，适合跑出非零正确率、观察闭环价值）
    "SimpleLogic": "simple_logic.jsonl",
    "SimpleArith": "simple_arith.jsonl",
    "SimpleSet": "simple_set.jsonl",
    # 原始竞赛/研究级数据集（难度高）
    "miniF2F": "minif2f_valid.jsonl",
    "miniF2F_test": "minif2f_test.jsonl",
    "ProofNet": "proofnet.jsonl",
    "PutnamBench": "putnambench.jsonl",
    "CombiBench": "combibench.jsonl",
    "FormalMATH-Lite": "formalmmath_lite.jsonl",
    "MathOlympiadBench": "matholympiadbench.jsonl",
}


# ============ API Key 读取（与 app.py 相同的持久化位置） ============

def load_api_config() -> tuple[str, str, str]:
    """返回 (env_key, api_key, model)，从 %APPDATA%\\nlp-lean4\\api_keys.json 读取"""
    path = os.path.join(
        os.environ.get("APPDATA") or os.path.expanduser("~"),
        "nlp-lean4", "api_keys.json",
    )
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return "", "", ""
    env = data.get("_active_key") or ""
    key = data.get(env) if env else ""
    model = (data.get("_models") or {}).get(env) or data.get("_active") or ""
    return env, key, model


# ============ 判分 ============

def _clean_proof_body(s: str) -> str:
    """把模型返回的字符串规约成 `:= by` 之后的纯证明体。

    模型常不听话：可能返回完整 theorem 声明、import、多余的 by 前缀，
    直接拼到 statement 后会变成「unexpected identifier; expected command」等语法错。
    这里统一剥离，只保留真正的 tactic 序列。
    """
    s = (s or "").strip()
    s = re.sub(r"```[a-zA-Z]*", "", s).strip()  # 兜底去除代码块围栏
    s = "\n".join(ln for ln in s.splitlines() if not ln.strip().startswith("import ")).strip()
    if ":= by" in s:
        s = s.rsplit(":= by", 1)[-1].lstrip("\r\n").strip()  # 取最后一个 `:= by` 之后
    m = re.match(r"^by\b(.*)$", s, re.S)  # 去掉多余的 by 前缀
    if m:
        s = m.group(1).lstrip("\r\n").strip()
    return s


def _uses_sorry(proof: str) -> bool:
    """检查证明体里是否用了 sorry / admit（去掉 -- 行尾注释后判断）"""
    cleaned = re.sub(r"--.*", "", proof)
    return bool(re.search(r"\b(sorry|admit)\b", cleaned))


def _classify_error(errors: list[str]) -> str:
    """把编译错误归类到粗粒度原因（用于失败原因分布统计）"""
    joined = "\n".join(errors).lower()
    if "unknown identifier" in joined or "unknown constant" in joined:
        return "unknown_identifier"
    if "type mismatch" in joined or "has type" in joined or "expected" in joined:
        return "type_mismatch"
    if "unsolved goals" in joined:
        return "unsolved_goals"
    if "timeout" in joined or "超时" in joined:
        return "timeout"
    if "no goals" in joined:
        return "no_goals"
    if "synthetic" in joined or "omega" in joined and "failed" in joined:
        return "tactic_failed"
    return "other"


def judge(header: str, statement: str, proof: str, compiler: LeanCompiler) -> tuple[bool, object, str]:
    """拼接 header + 陈述 + 证明体并编译，返回 (passed, result, reason)"""
    # 关键：proof 必须缩进拼在 `:= by` 之后，否则 Lean 会把 by 当空证明、
    # 把顶格的 proof 行当成顶层命令，报「unsolved goals + unexpected identifier」
    _indented = "\n".join(("  " + ln) if ln.strip() else ln for ln in (proof or "").splitlines())
    code = (header.strip() + "\n\n" + statement.strip() + "\n" + _indented + "\n")

    def _heartbeat(sec: int):
        # 编译期间每 5 秒心跳：Mathlib 冷加载很慢，让用户知道程序没死
        print(f"      ⏳ Lean 编译中...已 {sec} 秒", flush=True)

    res, _raw = compiler.compile_code_raw(code, on_progress=_heartbeat)
    passed = res.success and not _uses_sorry(proof)
    reason = "" if passed else _classify_error(res.errors or ["编译失败"])
    return passed, res, reason


# ============ 单题评测 ============

def run_one(llm: LLMClient, compiler: LeanCompiler, rec: dict, max_rounds: int) -> dict:
    """对一题跑「单次 vs 反馈闭环」，返回统计字典"""
    header = rec.get("header") or ""
    stmt = (rec.get("formal_statement") or "").rstrip()
    nl = rec.get("nl") or ""

    result = {
        "id": rec.get("id"),
        "benchmark": rec.get("benchmark"),
        "single_pass": False,
        "single_reason": "",
        "loop_pass": False,
        "final_rounds": 0,
        "loop_reason": "",
        "elapsed": 0.0,
    }

    # ---- 首轮（同时作为 single 的结果）----
    user_prompt = build_proof_completion_prompt(stmt, header, nl)
    print("  ├─ 调用 LLM 补全证明（首轮）...", flush=True)
    _t = time.time()
    try:
        resp = llm.chat(PROOF_COMPLETION_SYSTEM, user_prompt)
        print(f"  ├─ LLM 已返回（{round(time.time()-_t,1)}s），开始编译检查", flush=True)
    except Exception as e:
        print(f"  ├─ ❌ LLM 调用失败：{e}", flush=True)
        result["single_reason"] = f"llm_error: {e}"
        result["loop_reason"] = f"llm_error: {e}"
        return result

    proof, _ = extract_code_from_response(resp)
    proof = _clean_proof_body(proof)
    passed, res, reason = judge(header, stmt, proof, compiler)

    result["single_pass"] = passed
    result["single_reason"] = reason
    if passed:
        result["loop_pass"] = True
        result["final_rounds"] = 1
        print("  └─ ✅ 首轮即通过", flush=True)
        return result
    print(f"  ├─ ❌ 首轮未通过（原因: {reason}）", flush=True)
    _cp = " ".join((proof or "").split()) or "(空)"
    if len(_cp) > 200:
        _cp = _cp[:197] + "..."
    print(f"  │   模型证明体：{_cp}", flush=True)
    for _e in (res.errors or [])[:8]:
        print(f"  │   📌 {_e}", flush=True)

    # ---- 修正循环（闭环）----
    for rnd in range(2, max_rounds + 1):
        refine_prompt = build_proof_refine_prompt(proof, res.errors or ["编译失败"])
        print(f"  ├─ 调用 LLM 修正（第 {rnd} 轮）...", flush=True)
        _t = time.time()
        try:
            resp = llm.chat(PROOF_COMPLETION_SYSTEM, refine_prompt)
            print(f"  ├─ LLM 已返回（{round(time.time()-_t,1)}s），重新编译", flush=True)
        except Exception as e:
            print(f"  ├─ ❌ LLM 调用失败：{e}", flush=True)
            result["loop_reason"] = f"llm_error(round{rnd}): {e}"
            break
        proof, _ = extract_code_from_response(resp)
        proof = _clean_proof_body(proof)
        passed, res, reason = judge(header, stmt, proof, compiler)
        result["final_rounds"] = rnd
        if passed:
            result["loop_pass"] = True
            print(f"  └─ ✅ 第 {rnd} 轮修正后通过", flush=True)
            break
        result["loop_reason"] = reason
        print(f"  ├─ ❌ 第 {rnd} 轮仍未通过（原因: {reason}）", flush=True)
        _m = " ".join((proof or "").split())
        print(f"  │   本轮证明体：{_m[:200]}", flush=True)
        for _e in (res.errors or [])[:8]:
            print(f"  │   📌 {_e}", flush=True)

    return result


# ============ 主流程 ============

def load_rows(fname: str) -> list[dict]:
    path = os.path.join(_DATASET_DIR, fname)
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到验证集文件：{path}")
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    ap = argparse.ArgumentParser(description="NLP-to-Lean4 验证集补全证明评测")
    ap.add_argument("--benchmark", default=None,
                    help="benchmark 名（见 BENCHMARK_FILES）；留空则打印编号菜单交互选择")
    ap.add_argument("--file", default="", help="直接指定 jsonl 文件名（覆盖 --benchmark）")
    ap.add_argument("--limit", type=int, default=5, help="评测题数（默认 5，编译较慢请勿设太大）")
    ap.add_argument("--model", default="", help="模型名；留空则用 APPDATA 里记住的模型")
    ap.add_argument("--max_rounds", type=int, default=3, help="反馈闭环最大轮数（含首轮）")
    ap.add_argument("--shuffle", action="store_true", help="随机抽样而非取前 N 条")
    ap.add_argument("--seed", type=int, default=42)
    args, _ = ap.parse_known_args()  # 忽略 Spyder 等环境注入的未知参数，避免直接报错退出

    # ---- 模型 ----
    env_key, api_key, model = load_api_config()
    model = args.model or model
    if not model or not api_key:
        print("❌ 未找到可用的 API Key/模型。请先在界面侧边栏配置，或用 --model 指定（并保证对应 key 已保存）。")
        return
    if model not in PRESET_MODELS:
        print(f"❌ 未知模型 {model}，可选：{list(PRESET_MODELS.keys())}")
        return
    llm = LLMClient.from_preset(model, api_key=api_key)
    print(f"🔧 模型 = {model}（{PRESET_MODELS[model]['label']}）")

    compiler = LeanCompiler()
    print(f"🔧 Lean 内核 = {'✅ Mathlib' if compiler.has_mathlib else ('✅ 核心库' if compiler.available else '❌ 模拟')}")

    # ---- 选择数据集：命令行指定优先，否则打印编号菜单用 1/2/3 交互选择 ----
    choices = list(BENCHMARK_FILES.keys())
    if args.file:
        fname = args.file
    elif args.benchmark:
        if args.benchmark not in BENCHMARK_FILES:
            print(f"❌ 未知 benchmark {args.benchmark}，可选：{choices}")
            return
        fname = BENCHMARK_FILES[args.benchmark]
    else:
        print("\n可评测的测试集：")
        for i, name in enumerate(choices, 1):
            tag = "  ← 简单题" if name.startswith("Simple") else ""
            print(f"  {i}) {name}{tag}")
        raw = input("请输入序号（回车 = 1）：").strip()
        idx = 0
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            idx = int(raw) - 1
        else:
            print(f"⚠ 无效输入，自动使用默认：{choices[0]}")
        fname = BENCHMARK_FILES[choices[idx]]

    try:
        rows = load_rows(fname)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return
    if args.shuffle:
        random.seed(args.seed)
        random.shuffle(rows)
    rows = rows[: args.limit]
    print(f"📄 数据集 = {fname}，评测 {len(rows)} 题（max_rounds={args.max_rounds}）\n")

    # ---- 预热 Mathlib：把预编译 .olean 加载进系统页缓存，后续每题判分走热路径 ----
    #   Mathlib 源码早已编译成 .olean，无需重编；慢的是每个新 lean 进程要重载这个大的缓存。
    #   这里先跑一个仅 `import Mathlib` 的空文件触发首次加载，之后每轮判分的编译都会快得多。
    if compiler.has_mathlib:
        print("🔥 预热 Mathlib（首次约 1-3 分钟，仅此一次）...", flush=True)
        _w0 = time.time()

        def _warm_heartbeat(sec: int):
            print(f"   ⏳ Mathlib 预热中...已 {sec} 秒", flush=True)

        _wres, _ = compiler.compile_code_raw("import Mathlib\n", on_progress=_warm_heartbeat)
        if _wres.success:
            print(f"✅ Mathlib 预热完成（用时 {round(time.time() - _w0, 1)}s），后续判分将明显加快\n", flush=True)
        else:
            print("⚠ Mathlib 预热未完全通过（但缓存已载入），继续评测...\n", flush=True)

    # ---- 跑 ----
    results = []
    for i, rec in enumerate(rows, 1):
        print(f"\n{'='*64}")
        print(f"▶ 第 {i}/{len(rows)} 题：{rec['id']}（{rec.get('benchmark','')}）")
        _nl = " ".join((rec.get("nl") or "").split())
        if _nl:
            _nl = _nl if len(_nl) <= 180 else _nl[:177] + "..."
            print(f"   题目：{_nl}", flush=True)
        print('='*64, flush=True)
        t0 = time.time()
        r = run_one(llm, compiler, rec, args.max_rounds)
        r["elapsed"] = round(time.time() - t0, 1)
        results.append(r)
        _reason = r.get("single_reason") or r.get("loop_reason") or ""
        print(f"结果：single={'✅' if r['single_pass'] else '❌'}  "
              f"loop={'✅' if r['loop_pass'] else '❌'}  rounds={r['final_rounds']}  "
              f"[{_reason}]  用时 {r['elapsed']}s", flush=True)

    # ---- 统计 ----
    n = len(results)
    single_pass = sum(r["single_pass"] for r in results)
    loop_pass = sum(r["loop_pass"] for r in results)
    solved_rounds = [r["final_rounds"] for r in results if r["loop_pass"]]
    avg_rounds = (sum(solved_rounds) / len(solved_rounds)) if solved_rounds else 0

    from collections import Counter
    single_reason = Counter(r["single_reason"] for r in results if not r["single_pass"])
    loop_reason = Counter(r["loop_reason"] for r in results if not r["loop_pass"])

    print("\n" + "=" * 60)
    print("评测汇总")
    print("=" * 60)
    print(f"题数            : {n}")
    print(f"单次生成的 pass@1 : {single_pass}/{n} = {single_pass/n*100:.1f}%")
    print(f"反馈闭环的 pass@1 : {loop_pass}/{n} = {loop_pass/n*100:.1f}%")
    print(f"闭环相对单次提升  : +{loop_pass - single_pass} 题（{(loop_pass-single_pass)/n*100:.1f} 个百分点）" if n else "")
    print(f"通过题平均收敛轮数: {avg_rounds:.2f}（共 {len(solved_rounds)} 题收敛）")
    if single_reason:
        print(f"\n首轮失败原因分布: {dict(single_reason)}")
    if loop_reason:
        print(f"闭环终态失败原因: {dict(loop_reason)}")

    # ---- 保存结果 ----
    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        f"eval_results_{os.path.splitext(fname)[0]}_{n}.json",
    )
    summary = {
        "model": model,
        "dataset": fname,
        "max_rounds": args.max_rounds,
        "total": n,
        "single_pass": single_pass,
        "loop_pass": loop_pass,
        "single_pass_rate": round(single_pass / n, 4),
        "loop_pass_rate": round(loop_pass / n, 4),
        "avg_rounds": round(avg_rounds, 3),
        "single_fail_reasons": dict(single_reason),
        "loop_fail_reasons": dict(loop_reason),
        "details": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n💾 详细结果已保存: {out_path}")


if __name__ == "__main__":
    main()