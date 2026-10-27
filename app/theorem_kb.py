"""
数学定理知识库 + 检索引擎
- 每条记录：中文名 / 别名 / 领域 / 自然语言陈述 / Mathlib 标识符
- 检索结果中的 Mathlib 标识符必须由 Lean 内核 #check 探针验证后才展示签名
  （"LLM 可以提议，但只有内核能认证" —— 防止幻觉定理名）
"""

from dataclasses import dataclass, field


@dataclass
class TheoremEntry:
    mathlib_id: str          # Mathlib 中的定理标识符（内核验证对象）
    zh: str                  # 中文名称
    domain: str              # 领域标签
    statement: str           # 自然语言陈述（中文）
    aliases: list[str] = field(default_factory=list)  # 检索别名（中/英/符号）


# ============ 知识库（条目按领域分组，id 均经本地 Mathlib 内核验证） ============

THEOREM_KB: list[TheoremEntry] = [
    # ---------- 基础代数 ----------
    TheoremEntry("add_comm", "加法交换律", "代数",
                 "对任意 a b，a + b = b + a", ["交换律", "commutativity", "add"]),
    TheoremEntry("mul_comm", "乘法交换律", "代数",
                 "对任意 a b，a * b = b * a", ["交换律", "commutativity", "mul"]),
    TheoremEntry("add_assoc", "加法结合律", "代数",
                 "对任意 a b c，(a + b) + c = a + (b + c)", ["结合律", "associativity"]),
    TheoremEntry("sub_eq_add_neg", "减法定义", "代数",
                 "a - b 等于 a + (-b)", ["减法", "负"]),
    TheoremEntry("sq_nonneg", "平方非负", "分析",
                 "对任意实数 a，0 ≤ a²", ["平方", "非负", "square"]),
    TheoremEntry("mul_self_nonneg", "自乘非负", "分析",
                 "对任意 a，0 ≤ a * a", ["非负"]),
    TheoremEntry("abs_add_le", "绝对值三角不等式", "分析",
                 "|a + b| ≤ |a| + |b|", ["三角不等式", "triangle", "绝对值"]),
    TheoremEntry("abs_mul", "绝对值乘法", "分析",
                 "|a * b| = |a| * |b|", ["绝对值"]),

    # ---------- 自然数算术 ----------
    TheoremEntry("Nat.add_zero", "自然数加零", "算术",
                 "对任意自然数 n，n + 0 = n", ["加零", "零"]),
    TheoremEntry("Nat.zero_add", "零加自然数", "算术",
                 "对任意自然数 n，0 + n = n", ["加零"]),
    TheoremEntry("Nat.succ_add", "后继与加法", "算术",
                 "succ m + n = succ (m + n)", ["后继", "succ", "归纳"]),
    TheoremEntry("Nat.mod_add_div", "带余除法恒等式", "算术",
                 "对任意自然数 a b，a % b + b * (a / b) = a", ["取模", "整除", "mod", "div"]),
    TheoremEntry("Int.mul_ediv_add_emod", "整数带余除法", "算术",
                 "对任意整数 a b，b * (a / b) + a % b = a", ["取模", "整除"]),
    TheoremEntry("Nat.pow_succ", "幂的递归定义", "算术",
                 "a ^ (b + 1) = a ^ b * a", ["幂", "指数", "pow"]),
    TheoremEntry("Nat.mul_add", "乘法对加法左分配", "算术",
                 "m * (n + k) = m * n + m * k", ["分配律", "distrib"]),
    TheoremEntry("Nat.add_mul", "乘法对加法右分配", "算术",
                 "(m + n) * k = m * k + n * k", ["分配律"]),

    # ---------- 数论 ----------
    TheoremEntry("Nat.Prime.dvd_mul", "素数整除性质（欧几里得引理）", "数论",
                 "若素数 p 整除 a * b，则 p 整除 a 或 p 整除 b", ["素数", "质数", "整除", "Euclid"]),
    TheoremEntry("Nat.exists_infinite_primes", "素数有无穷多个（欧几里得定理）", "数论",
                 "对任意 n，存在不小于 n 的素数", ["素数无穷", "质数", "Euclid", "infinite primes"]),
    TheoremEntry("Nat.Prime.irrational_sqrt", "素数平方根为无理数", "数论",
                 "若 p 是素数，则 √p 是无理数", ["无理数", "平方根", "irrational", "根号2"]),
    TheoremEntry("Nat.factorial_pos", "阶乘为正", "组合",
                 "对任意 n，0 < n!", ["阶乘", "factorial"]),
    TheoremEntry("Nat.choose", "二项式系数", "组合",
                 "n 选 k：组合数 C(n, k)", ["组合", "二项", "binomial", "C(n,k)"]),
    TheoremEntry("add_pow", "二项式定理", "组合",
                 "(x + y) ^ n = Σ C(n,k) x^k y^(n-k)", ["二项式定理", "binomial theorem", "牛顿"]),
    TheoremEntry("Finset.sum_range_id", "高斯求和公式", "组合",
                 "0 + 1 + ... + (n-1) = n * (n-1) / 2", ["求和", "高斯", "等差", "Gauss"]),
    TheoremEntry("ZMod.pow_card_sub_one_eq_one", "费马小定理", "数论",
                 "若 p 为素数且 p ∤ a，则 a^(p-1) ≡ 1 (mod p)", ["费马", "Fermat", "同余", "模"]),

    # ---------- 实分析与不等式 ----------
    TheoremEntry("Real.geom_mean_le_arith_mean", "加权均值不等式 (AM-GM)", "分析",
                 "非负实数的加权几何平均不超过加权算术平均", ["均值", "AM-GM", "算术平均", "几何平均"]),
    TheoremEntry("Real.sin_sq_add_cos_sq", "平方和恒等式", "分析",
                 "对任意实数 x，sin²x + cos²x = 1", ["三角恒等式", "sin", "cos", "毕达哥拉斯"]),
    TheoremEntry("Real.sin_add", "正弦加法公式", "分析",
                 "sin(x + y) = sin x * cos y + cos x * sin y", ["和角公式", "sin"]),
    TheoremEntry("Real.cos_add", "余弦加法公式", "分析",
                 "cos(x + y) = cos x * cos y - sin x * sin y", ["和角公式", "cos"]),
    TheoremEntry("Real.exp_add", "指数加法公式", "分析",
                 "exp(x + y) = exp x * exp y", ["指数", "exp"]),
    TheoremEntry("Real.log_mul", "对数乘法公式", "分析",
                 "对正实数 x y，log (x * y) = log x + log y", ["对数", "log"]),
    TheoremEntry("Real.sqrt_mul", "根号乘法", "分析",
                 "对非负实数 x y，√(x * y) = √x * √y", ["根号", "sqrt"]),

    # ---------- 微积分 ----------
    TheoremEntry("deriv_add", "导数加法法则", "微积分",
                 "可导函数和的导数等于导数之和 (f + g)' = f' + g'", ["求导", "导数", "derivative"]),
    TheoremEntry("deriv_mul", "乘积求导法则（莱布尼茨法则）", "微积分",
                 "(f * g)' = f' * g + f * g'", ["求导", "乘积", "莱布尼茨", "Leibniz"]),
    TheoremEntry("exists_deriv_eq_slope", "拉格朗日中值定理", "微积分",
                 "闭区间连续、开区间可导的函数，存在一点导数等于两端割线斜率", ["中值定理", "MVT", "Lagrange"]),
    TheoremEntry("IsLocalMin.deriv_eq_zero", "费马定理（极值点导数为零）", "微积分",
                 "内点极小值处若可导则导数为零", ["极值", "费马定理", "Fermat stationary"]),
    TheoremEntry("intermediate_value_Icc", "介值定理", "拓扑",
                 "闭区间上连续函数取到两端点值之间的一切值", ["介值", "IVT", "连续"]),

    # ---------- 极限与级数 ----------
    TheoremEntry("tendsto_const_nhds", "常数列收敛", "分析",
                 "常值函数（数列）趋于该常数的邻域", ["极限", "收敛", "常数列"]),
    TheoremEntry("summable_geometric_of_lt_one", "几何级数收敛", "分析",
                 "公比绝对值小于 1 时，Σ r^n 收敛于 1/(1-r)", ["等比", "几何级数", "收敛", "geometric"]),

    # ---------- 集合与逻辑 ----------
    TheoremEntry("Set.mem_union", "元素属于并集当且仅当属于其一", "集合论",
                 "x ∈ A ∪ B ↔ x ∈ A ∨ x ∈ B", ["并集", "union", "集合"]),
    TheoremEntry("Set.compl_union", "德摩根律（补集对并）", "集合论",
                 "A ∪ B 的补集等于 A 的补交 B 的补", ["德摩根", "De Morgan", "补集"]),
    TheoremEntry("Set.compl_inter", "德摩根律（补集对交）", "集合论",
                 "A ∩ B 的补集等于 A 的补并 B 的补", ["德摩根", "De Morgan", "交集"]),
    TheoremEntry("not_and_or", "逻辑德摩根律", "逻辑",
                 "¬(a ∧ b) ↔ ¬a ∨ ¬b", ["德摩根", "逻辑", "否定"]),
    TheoremEntry("Classical.em", "排中律", "逻辑",
                 "对任意命题 P，P ∨ ¬P 成立", ["排中", "excluded middle", "经典逻辑"]),
    TheoremEntry("Nat.le_antisymm", "自然数反对称性", "序理论",
                 "若 a ≤ b 且 b ≤ a，则 a = b", ["反对称", "偏序"]),

    # ---------- 组合与图论 ----------
    TheoremEntry("Fintype.exists_ne_map_eq_of_card_lt", "鸽巢原理（抽屉原理）", "组合",
                 "有限集 A 到更小的有限集 B 的映射必有两点同像", ["鸽巢", "抽屉", "pigeonhole"]),
]


# ============ 检索引擎 ============

def search_theorems(query: str, top_k: int = 6) -> list[tuple[TheoremEntry, float]]:
    """
    对知识库做加权关键词检索，返回 [(条目, 得分)]（按得分降序）。
    评分规则：中文名完全包含 > 别名命中 > 领域命中 > 陈述文本命中
    """
    q = query.strip().lower()
    if not q:
        return []

    scored: list[tuple[TheoremEntry, float]] = []
    for entry in THEOREM_KB:
        score = 0.0
        zh = entry.zh.lower()
        # 中文名整体命中权重最高；查询越短命中越可信
        if q in zh or zh in q:
            score += 10.0
        # 别名命中
        for alias in entry.aliases:
            a = alias.lower()
            if a and (a in q or q in a):
                score += 5.0
        # 领域命中
        if entry.domain.lower() in q:
            score += 3.0
        # 陈述文本逐字命中（粗略相关性）
        stmt = entry.statement.lower()
        for token in _tokenize(q):
            if len(token) >= 2 and token in stmt:
                score += 1.0
        # Mathlib id 直接命中（用户输入了英文标识符）
        if q in entry.mathlib_id.lower():
            score += 8.0
        if score > 0:
            scored.append((entry, score))

    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


def get_entry(mathlib_id: str) -> TheoremEntry | None:
    """按 Mathlib 标识符精确取条目"""
    for entry in THEOREM_KB:
        if entry.mathlib_id == mathlib_id:
            return entry
    return None


def _tokenize(s: str) -> list[str]:
    """简单分词：按非字母数字汉字切分"""
    import re
    return re.split(r"[^\w\u4e00-\u9fff]+", s)
