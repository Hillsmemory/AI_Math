"""构建「验证集」目录下的统一验证集文件。

数据来源（均已 git clone 到本目录）：
  miniF2F-lean4/MiniF2F/{Valid,Test}/*.lean   miniF2F Lean 4 陈述（244 + 244）
  miniF2F_v2/datasets/*.json                  miniF2F 校订后的「自然语言 ↔ Lean 4」配对
  ProofNet-lean4/proofnet_lean4.jsonl         ProofNet 本科数学（374）
  PutnamBench/lean4/src/*.lean                PutnamBench 竞赛题（673）
  PutnamBench/informal/putnam.json            对应的自然语言题面
  CombiBench/lean/CombiBench/*.lean           CombiBench 组合数学（100）
  FormalMATH-Lite/FormalMATH_lite.json        FormalMATH-Lite（425）
  MathOlympiadBench/MOBench.jsonl             MathOlympiadBench 奥赛题（360）

输出（每行一条，UTF-8）：
  minif2f_valid.jsonl / minif2f_test.jsonl / proofnet.jsonl / putnambench.jsonl /
  combibench.jsonl / formalmath_lite.jsonl / matholympiadbench.jsonl / all.jsonl
"""

import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HEADER = (
    "import Mathlib\nimport Aesop\n\n"
    "set_option maxHeartbeats 0\n\n"
    "open BigOperators Real Nat Topology Rat Finset\n\n"
)


def normalize_statement(stmt):
    """统一成以 `:= by` 结尾的形式，方便模型直接补全证明。"""
    s = stmt.strip()
    s = re.sub(r"\s*sorry\s*$", "", s).rstrip()   # 去掉结尾的 sorry 占位符
    if s.endswith(":="):
        s += " by"
    return s


CUT_BY = re.compile(r":=\s*by\b")


def cut_statement(stmt):
    """只保留陈述部分：截断到 `:= by`，丢弃其后的证明内容。"""
    s = stmt.strip()
    m = CUT_BY.search(s)
    if m:
        return s[:m.start()].rstrip() + " := by"
    return normalize_statement(s)


def split_preamble(stmt, header):
    """把 theorem 之前的辅助定义（如 `abbrev xxx_solution := sorry`）移入 header。"""
    idx = stmt.find("theorem ")
    if idx <= 0:
        return stmt, header
    pre = stmt[:idx].strip()
    if not pre:
        return stmt, header
    return stmt[idx:].strip(), header.rstrip() + "\n\n" + pre + "\n\n"


def write_jsonl(filename, records):
    path = os.path.join(BASE, filename)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


# ---------------------------------------------------------------- miniF2F
def build_minif2f():
    lean_dir = os.path.join(BASE, "miniF2F-lean4", "MiniF2F")
    v2_dir = os.path.join(BASE, "miniF2F_v2", "datasets")

    def load_v2(name):
        with open(os.path.join(v2_dir, name), encoding="utf-8") as f:
            return {row["name"]: row for row in json.load(f)}

    v2c, v2s = load_v2("miniF2F_v2c.json"), load_v2("miniF2F_v2s.json")
    out = {}
    for sub, split, fname in [
        ("Valid", "valid", "minif2f_valid.jsonl"),
        ("Test", "test", "minif2f_test.jsonl"),
    ]:
        src = os.path.join(lean_dir, sub)
        records = []
        for fn in sorted(os.listdir(src)):
            if not fn.endswith(".lean"):
                continue
            pid = fn[:-5]
            c, s = v2c.get(pid), v2s.get(pid)
            if c:
                nl, formal, header = (
                    c["informal_statement"],
                    normalize_statement(c["formal_statement"]),
                    c.get("header") or DEFAULT_HEADER,
                )
            else:
                # v2 未覆盖的极少数题目：从原始 Lean 文件中截取陈述
                text = open(os.path.join(src, fn), encoding="utf-8").read()
                idx = text.find("theorem ")
                nl, formal = "", normalize_statement(text[idx:])
                header = text[:idx].strip() + "\n\n"
            formal, header = split_preamble(formal, header)
            records.append({
                "id": pid,
                "benchmark": "miniF2F",
                "split": split,
                "nl": nl,
                "nl_proof": c.get("informal_proof", "") if c else "",
                "nl_simplified": s["informal_statement"] if s else "",
                "formal_statement": formal,
                "header": header,
                "tags": [],
                "source": "miniF2F-v2 + miniF2F-lean4",
                "file": "miniF2F-lean4/MiniF2F/%s/%s" % (sub, fn),
            })
        out[fname] = write_jsonl(fname, records)
    return out


# ---------------------------------------------------------------- ProofNet
def build_proofnet():
    path = os.path.join(BASE, "ProofNet-lean4", "proofnet_lean4.jsonl")
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            formal, header = split_preamble(
                normalize_statement(r["formal_statement"]),
                r.get("header") or DEFAULT_HEADER,
            )
            records.append({
                "id": r["name"],
                "benchmark": "ProofNet",
                "split": "all",
                "nl": r.get("informal_stmt") or "",
                "nl_proof": r.get("informal_proof") or "",
                "nl_simplified": "",
                "formal_statement": formal,
                "header": header,
                "tags": [],
                "source": "ProofNet-lean4 (rahul3613)",
                "file": "ProofNet-lean4/formal/%s" % r.get("source", ""),
            })
    return write_jsonl("proofnet.jsonl", records)


# ---------------------------------------------------------------- PutnamBench
def build_putnam():
    src_dir = os.path.join(BASE, "PutnamBench", "lean4", "src")
    informal_path = os.path.join(BASE, "PutnamBench", "informal", "putnam.json")
    with open(informal_path, encoding="utf-8") as f:
        informal = {row["problem_name"]: row for row in json.load(f)}

    records = []
    for fn in sorted(os.listdir(src_dir)):
        if not fn.endswith(".lean"):
            continue
        pid = fn[:-5]
        text = open(os.path.join(src_dir, fn), encoding="utf-8").read()
        idx = text.find("theorem ")
        header = text[:idx]
        # 去掉题面注释块 /-- ... -/
        doc = header.rfind("/--")
        if doc != -1 and header.rfind("-/") > doc:
            header = header[:doc]
        row = informal.get(pid, {})
        formal, header = split_preamble(
            normalize_statement(text[idx:]), header.strip() + "\n\n"
        )
        records.append({
            "id": pid,
            "benchmark": "PutnamBench",
            "split": "all",
            "nl": row.get("informal_statement", ""),
            "nl_proof": row.get("informal_solution") or "",
            "nl_simplified": "",
            "formal_statement": formal,
            "header": header,
            "tags": row.get("tags", []),
            "source": "PutnamBench (trishullab)",
            "file": "PutnamBench/lean4/src/%s" % fn,
        })
    return write_jsonl("putnambench.jsonl", records)


# ---------------------------------------------------------------- CombiBench
COMBI_DOC_DECL = re.compile(
    r"/--([\s\S]*?)-/[ \t]*\r?\n[ \t]*(theorem|lemma)\s+(\S+)"
)


def build_combibench():
    src_dir = os.path.join(BASE, "CombiBench", "lean", "CombiBench")
    records = []
    for fn in sorted(os.listdir(src_dir)):
        if not fn.endswith(".lean"):
            continue
        pid = fn[:-5]
        text = open(os.path.join(src_dir, fn), encoding="utf-8").read()
        m = COMBI_DOC_DECL.search(text)
        if not m:
            continue
        nl = m.group(1).strip().replace("\\n", "\n")
        start = m.start(2)
        records.append({
            "id": pid,
            "benchmark": "CombiBench",
            "split": "all",
            "nl": nl,
            "nl_proof": "",
            "nl_simplified": "",
            "formal_statement": cut_statement(text[start:]),
            "header": text[:m.start()].strip() + "\n\n",
            "tags": [pid.split("_")[0]],
            "source": "CombiBench (Moonshot AI / AI-MO)",
            "file": "CombiBench/lean/CombiBench/%s" % fn,
        })
    return write_jsonl("combibench.jsonl", records)


# ---------------------------------------------------------------- FormalMATH
FORMALMATH_DECL = re.compile(r"(?m)^[ \t]*(?:theorem|lemma|def|abbrev)\s+(\S+)")


def build_formalmmath():
    path = os.path.join(BASE, "FormalMATH-Lite", "FormalMATH_lite.json")
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)

    records = []
    for i, r in enumerate(rows):
        code = r.get("autoformalization") or ""
        decls = list(FORMALMATH_DECL.finditer(code))
        if not decls:
            continue
        start = decls[-1].start()
        tags = [t.strip() for t in re.split(r"[;,]", r.get("domain") or "") if t.strip()]
        records.append({
            "id": r.get("theorem_names") or "formalmmath_%d" % i,
            "benchmark": "FormalMATH-Lite",
            "split": "all",
            "nl": (r.get("refined_statement") or "").strip(),
            "nl_proof": (r.get("solution") or "").strip(),
            "nl_simplified": "",
            "formal_statement": cut_statement(code[start:]),
            "header": code[:start].strip() + "\n\n",
            "tags": tags,
            "source": "FormalMATH-Lite (%s)" % (r.get("source") or ""),
            "file": "FormalMATH-Lite/FormalMATH_lite.json#%d" % i,
        })
    return write_jsonl("formalmmath_lite.jsonl", records)


# ---------------------------------------------------------------- MathOlympiadBench
def build_matholympiad():
    path = os.path.join(BASE, "MathOlympiadBench", "MOBench.jsonl")
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            nl = (r.get("informal_prefix") or "").strip()
            if nl.startswith("/-!"):
                nl = nl[3:].strip()
            if nl.endswith("-/"):
                nl = nl[:-2].strip()
            records.append({
                "id": r.get("name") or r["problem_id"],
                "benchmark": "MathOlympiadBench",
                "split": r.get("split") or "all",
                "nl": nl,
                "nl_proof": "",
                "nl_simplified": "",
                "formal_statement": cut_statement(r["formal_statement"]),
                "header": (r.get("header") or DEFAULT_HEADER).strip() + "\n\n",
                "tags": r.get("tags") or [],
                "source": "MathOlympiadBench (Goedel-LM)",
                "file": "MathOlympiadBench/MOBench.jsonl",
            })
    return write_jsonl("matholympiadbench.jsonl", records)


def main():
    stats = build_minif2f()
    stats["proofnet.jsonl"] = build_proofnet()
    stats["putnambench.jsonl"] = build_putnam()
    stats["combibench.jsonl"] = build_combibench()
    stats["formalmmath_lite.jsonl"] = build_formalmmath()
    stats["matholympiadbench.jsonl"] = build_matholympiad()

    # 合并全部数据
    all_records = []
    for fname in ["minif2f_valid.jsonl", "minif2f_test.jsonl",
                  "proofnet.jsonl", "putnambench.jsonl",
                  "combibench.jsonl", "formalmmath_lite.jsonl",
                  "matholympiadbench.jsonl"]:
        with open(os.path.join(BASE, fname), encoding="utf-8") as f:
            all_records.extend(json.loads(line) for line in f)
    stats["all.jsonl"] = write_jsonl("all.jsonl", all_records)

    for name, n in stats.items():
        print("%-26s %4d 条" % (name, n))


if __name__ == "__main__":
    main()
