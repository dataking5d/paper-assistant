"""Three-layer academic reference classification system.

Layer 1: Rule-based — title/abstract keyword patterns.
Layer 2: Metadata-driven — year + cited_by_count heuristic scoring.
Layer 3: LLM semantic — deep understanding of abstract + context.
Fusion engine: merge L1+L2, trigger L3 only when needed, gate by confidence.

Output: ClassificationResult{role, confidence, reasons, source}
"""

import re
from dataclasses import dataclass, field
from collections import defaultdict


# ── Role definitions ─────────────────────────────────────────────────

ROLES = [
    "Theory Foundation",    # 理论基础文献 — 提出/系统阐述核心理论或概念框架
    "Methodology",          # 方法文献 — 提出/描述研究方法、工具、技术
    "Empirical Evidence",   # 实证研究 — 报告原创数据、实验、案例发现
    "Review",               # 综述文献 — 系统性文献梳理、元分析、研究进展评述
    "Frontier",             # 前沿研究 — 探索新兴方向、新范式
    "Unknown",              # 信息不足，无法判断
]

# ── Dataclasses ───────────────────────────────────────────────────────

@dataclass
class ClassificationInput:
    title: str
    authors: str = ""
    year: int | None = None
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    venue: str = ""
    cited_by_count: int | None = None


@dataclass
class ClassificationResult:
    role: str                 # One of ROLES
    confidence: float         # 0.00-1.00
    reasons: list[str]        # Auditable, traceable reasons
    source: str               # "rule+metadata" | "rule+metadata+llm"


# ======================================================================
# Layer 1: Rule-based classification
# ======================================================================

# (regex_pattern, target_role, confidence, reason_template)
RULE_SET: list[tuple[str, str, float, str]] = [
    # ── Review ──
    (
        r"(?i)\b(systematic\s+review|meta[-\s]?analysis|literature\s+review"
        r"|state\s+of\s+the\s+art|critical\s+review|scoping\s+review)\b",
        "Review", 0.92,
        "标题明确标识为综述/元分析",
    ),
    (
        r"(?i)\b(survey\s+of|a\s+survey\s+on|comprehensive\s+survey)\b",
        "Review", 0.85,
        "标题标识为领域综述",
    ),
    (
        r"(综述|述评|回顾|研究进展|研究现状|研究综述|元分析|荟萃分析)",
        "Review", 0.88,
        "中文标题标识为综述类文献",
    ),

    # ── Theory Foundation ──
    (
        r"(?i)^(theory\s+of|toward(s?)\s+(a\s+)?theory|foundations?\s+of"
        r"|principles?\s+of|theoretical\s+framework|conceptual\s+framework"
        r"|toward\s+a\s+|general\s+theory)",
        "Theory Foundation", 0.85,
        "标题标识为理论构建文献",
    ),
    (
        r"(理论(基础|构建|框架|体系|探索)|原理|概念框架|学说)",
        "Theory Foundation", 0.80,
        "中文标题标识为理论性文献",
    ),

    # ── Methodology ──
    (
        r"(?i)\b(method\s+for|methodology\s+for|novel\s+(method|approach|technique)"
        r"|new\s+(method|algorithm|approach)|tool\s+for|instrument\s+for"
        r"|framework\s+for\s+(measuring|assessing|evaluating|analy[sz]ing))\b",
        "Methodology", 0.80,
        "标题标识为方法论/工具文献",
    ),
    (
        r"(方法(研究|设计|构建|提出)|方法论|测量工具|评估框架|分析框架)",
        "Methodology", 0.78,
        "中文标题标识为方法论文献",
    ),

    # ── Empirical Evidence ──
    (
        r"(?i)\b(empirical\s+(study|research|investigation|analysis)"
        r"|experimental\s+(study|investigation|evidence)"
        r"|case\s+study\s+(of|on)|evidence\s+from|data\s+from"
        r"|findings?\s+from|results?\s+of\s+(a\s+)?(study|survey|experiment))\b",
        "Empirical Evidence", 0.82,
        "标题标识为实证研究",
    ),
    (
        r"(实证(研究|分析|调查)|实验(研究|设计|结果)|案例(研究|分析|调查)"
        r"|调查(研究|报告|分析)|数据(分析|采集|挖掘))",
        "Empirical Evidence", 0.80,
        "中文标题标识为实证研究",
    ),

    # ── Frontier ──
    (
        r"(?i)\b(emerging|frontier|novel\s+paradigm|new\s+direction"
        r"|rethinking|reimagining|beyond\s+the|post-\w+)\b",
        "Frontier", 0.70,
        "标题标识为前沿/新兴方向",
    ),
    (
        r"(前沿|新兴|新范式|新方向|重新审视|后\w+时代)",
        "Frontier", 0.70,
        "中文标题标识为前沿研究",
    ),
]


def layer1_rule_classify(title: str, abstract: str = "",
                         keywords: list[str] | None = None) -> ClassificationResult:
    """Rule-based classification from title/abstract keyword patterns.

    Returns a ClassificationResult. If no rule matches, returns role="Unknown",
    confidence=0.0 with no reasons.
    """
    search_text = f"{title}\n{abstract[:500]}"
    if keywords:
        search_text += "\n" + " ".join(keywords)

    best_role = ""
    best_conf = 0.0
    best_reason = ""

    for pattern, role, conf, reason_tpl in RULE_SET:
        if re.search(pattern, search_text):
            if conf > best_conf:
                best_role = role
                best_conf = conf
                best_reason = reason_tpl

    if best_role:
        return ClassificationResult(
            role=best_role,
            confidence=best_conf,
            reasons=[best_reason],
            source="rule+metadata",
        )
    return ClassificationResult(
        role="Unknown", confidence=0.0, reasons=[], source="rule+metadata",
    )


# ======================================================================
# Layer 2: Metadata-driven classification
# ======================================================================

def layer2_metadata_classify(year: int | None,
                              cited_by_count: int | None) -> dict:
    """Bibliometric heuristic scoring. Returns evidence distribution, not a single role.

    Returns: {"scores": {role: weight, ...}, "reasons": [str], "dominant_signal": str|None}
    """
    scores: dict[str, float] = defaultdict(float)
    reasons: list[str] = []
    dominant: str | None = None

    current_year = 2026
    age = (current_year - year) if year else None

    # ── Age signal ──
    if age is not None:
        if age >= 25:
            scores["Theory Foundation"] += 0.35
            reasons.append(f"发表{age}年，属于学科早期文献")
            dominant = "high_age"
        elif age >= 15:
            scores["Theory Foundation"] += 0.20
            reasons.append(f"发表{age}年，较早期文献")
        elif age <= 2:
            scores["Frontier"] += 0.35
            reasons.append(f"{year}年发表，近期文献")
            if not dominant:
                dominant = "very_recent"
        elif age <= 5:
            scores["Frontier"] += 0.20
            reasons.append(f"{year}年发表，较新文献")

    # ── Cited_by_count signal ──
    if cited_by_count is not None:
        if cited_by_count >= 2000:
            scores["Theory Foundation"] += 0.45
            reasons.append(f"被引{cited_by_count}次，极高学术影响力")
            if not dominant:
                dominant = "highly_cited"
        elif cited_by_count >= 500:
            scores["Theory Foundation"] += 0.25
            scores["Review"] += 0.20
            reasons.append(f"被引{cited_by_count}次，高影响力")
        elif cited_by_count >= 100:
            scores["Review"] += 0.15
            scores["Empirical Evidence"] += 0.10
            reasons.append(f"被引{cited_by_count}次，中等影响力")
        elif cited_by_count >= 10:
            scores["Empirical Evidence"] += 0.05
        elif cited_by_count < 5 and age is not None and age <= 3:
            scores["Frontier"] += 0.20
            reasons.append("近期发表且被引量低，符合前沿研究特征")

    # ── Cross-interaction: high age + high citations → strong theory signal ──
    if (age is not None and age >= 15
            and cited_by_count is not None and cited_by_count >= 500):
        scores["Theory Foundation"] += 0.15
        reasons.append("早期高被引→理论奠基特征强")

    # ── Cross-interaction: recent + high citations → breakthrough ──
    if (age is not None and age <= 5
            and cited_by_count is not None and cited_by_count >= 100):
        scores["Frontier"] += 0.10
        scores["Empirical Evidence"] += 0.10
        reasons.append("近期高被引→突破性研究")

    return {
        "scores": dict(scores),
        "reasons": reasons,
        "dominant_signal": dominant,
    }


def _build_l2_summary_for_llm(l2_result: dict) -> str:
    """Render L2 evidence distribution as contextual hints for the L3 LLM."""
    lines: list[str] = []
    if l2_result.get("reasons"):
        lines.append("引文计量分析发现:")
        for r in l2_result["reasons"]:
            lines.append(f"  - {r}")

    scores = l2_result.get("scores", {})
    if scores:
        top_role = max(scores, key=scores.get)
        if scores[top_role] >= 0.3:
            lines.append(
                f"元数据证据最支持: {top_role} "
                f"(权重={scores[top_role]:.2f})"
            )

    return "\n".join(lines) if lines else "无额外元数据信号"


# ======================================================================
# Layer 3: LLM semantic classification
# ======================================================================

_LLM_SYSTEM_PROMPT = (
    "你是一位学术文献分类专家。你需要判断一篇文献在其研究领域中的学术角色。\n\n"
    "## 角色定义\n\n"
    "1. Theory Foundation — 理论基础文献\n"
    "   特征: 提出新理论、新概念框架、新范式；系统阐述某个理论的原理和体系；"
    "定义领域核心概念；构建分析模型\n"
    "   例: \"Nida提出的功能对等理论\" \"Lakoff的概念隐喻理论\"\n"
    "   NOT: 仅仅引用或应用已有理论\n\n"
    "2. Methodology — 方法文献\n"
    "   特征: 提出或详细描述某种研究方法、测量工具、分析技术、算法、流程\n"
    "   例: \"结构方程模型方法指南\" \"语料库分析方法\" \"问卷量表开发与验证\"\n"
    "   NOT: 使用了某种方法做实证研究（那是Empirical Evidence）\n\n"
    "3. Empirical Evidence — 实证研究\n"
    "   特征: 报告原创的实证数据、实验结果、调查发现、案例分析结论\n"
    "   例: \"对X群体进行实验发现Y\" \"基于Z数据的实证分析\" \"某案例的调查结果\"\n"
    "   NOT: 综述他人实证发现（那是Review）\n\n"
    "4. Review — 综述文献\n"
    "   特征: 对已有文献的系统性梳理、批判性分析、研究进展评述、元分析\n"
    "   例: \"X领域近二十年研究综述\" \"Y主题的系统综述与元分析\"\n"
    "   NOT: 在引言中简要回顾文献\n\n"
    "5. Frontier — 前沿研究\n"
    "   特征: 探索新兴方向、提出新问题、尝试新方法、挑战已有范式；"
    "通常是近期发表（近3-5年），开辟了新的研究方向\n"
    "   例: \"将机器学习应用于传统翻译质量评估\"\n"
    "   NOT: 在已有成熟框架内的增量研究\n\n"
    "6. Unknown — 无法判断\n"
    "   条件: 信息严重不足（无摘要、仅知标题且标题信息量少）\n\n"
    "## 置信度标定\n"
    "- 0.90-0.95: 摘要明确展示了该角色特征，几乎毫无疑问\n"
    "- 0.78-0.89: 摘要信息充分，有明确指向\n"
    "- 0.60-0.77: 可合理推断，但存在一定模糊性\n"
    "- <0.60: 信息严重不足，应设为 Unknown\n\n"
    "## 重要提醒\n"
    "- 不要根据标题看起来重要就判断为 Theory Foundation\n"
    "  → 必须看到摘要中有实质性的理论构建描述\n"
    "- 不要将使用了某个方法误判为 Methodology\n"
    "  → Methodology 角色要求文献以方法本身为核心贡献\n"
    "- 如果文献既是实证又包含理论贡献\n"
    "  → 看主要贡献。若核心是数据发现 → Empirical Evidence\n"
    "- 中文文献如摘要信息不足\n"
    "  → 诚实标注 confidence < 0.60 → Unknown，禁止强行分类\n\n"
    "## 输出格式\n"
    "严格输出 JSON，不要任何解释性文字:\n"
    '{\n'
    '  "role": "Theory Foundation|Methodology|Empirical Evidence|'
    'Review|Frontier|Unknown",\n'
    '  "confidence": 0.85,\n'
    '  "reasons": ["理由1", "理由2", "理由3"]\n'
    '}'
)


def _build_l3_user_message(ref: ClassificationInput,
                            l1_hint: str | None,
                            l2_summary: str) -> str:
    parts = [
        "## 文献信息",
        f"标题: {ref.title}",
        f"作者: {ref.authors or '未知'}",
        f"年份: {ref.year or '未知'}",
        f"摘要: {ref.abstract or '(无摘要)'}",
        f"关键词: {', '.join(ref.keywords) if ref.keywords else '(无)'}",
        f"发表期刊: {ref.venue or '未知'}",
        f"被引次数: {ref.cited_by_count if ref.cited_by_count is not None else '未知'}",
        "",
        "## 辅助信息（仅供参考）",
        f"规则分析提示: {l1_hint or '无强规则匹配'}",
        f"元数据分析: {l2_summary}",
        "",
        "请判断该文献的学术角色。",
    ]
    return "\n".join(parts)


def layer3_llm_classify(ref: ClassificationInput,
                         client,
                         model: str,
                         l1_hint: str | None,
                         l2_summary: str) -> ClassificationResult:
    """LLM-based deep semantic classification. Only called when L1+L2 are
    insufficient or conflicting.
    """
    try:
        message = client.messages.create(
            model=model,
            max_tokens=1024,
            system=_LLM_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": _build_l3_user_message(ref, l1_hint, l2_summary),
            }],
        )
        raw = ""
        for block in message.content:
            if hasattr(block, "text"):
                raw += block.text

        data = _parse_json_response(raw)
        role = data.get("role", "Unknown")
        confidence = float(data.get("confidence", 0.0))
        reasons = data.get("reasons", [])

        if role not in ROLES:
            role = "Unknown"
            confidence = max(confidence, 0.0)

        return ClassificationResult(
            role=role,
            confidence=confidence,
            reasons=reasons,
            source="rule+metadata+llm",
        )
    except Exception:
        return ClassificationResult(
            role="Unknown",
            confidence=0.0,
            reasons=["LLM分类调用失败"],
            source="rule+metadata+llm",
        )


# ======================================================================
# Fusion engine
# ======================================================================

def classify_reference(ref: ClassificationInput,
                       client=None,
                       model: str = "deepseek-v4-pro") -> ClassificationResult:
    """Three-layer fusion classification.

    Guarantees:
    - Always returns role + confidence + reasons
    - confidence < 0.60 → role = "Unknown"
    - Every reason traceable to a specific signal
    """
    all_reasons: list[str] = []

    # ── Layer 1 ──
    l1 = layer1_rule_classify(ref.title, ref.abstract, ref.keywords)
    all_reasons.extend(l1.reasons)

    # ── Layer 2 ──
    l2 = layer2_metadata_classify(ref.year, ref.cited_by_count)
    all_reasons.extend(l2["reasons"])

    l2_scores: dict[str, float] = l2.get("scores", {})
    l2_top_role = max(l2_scores, key=l2_scores.get) if l2_scores else None
    l2_top_score = l2_scores.get(l2_top_role, 0) if l2_top_role else 0

    # ── Decide whether to trigger L3 ──
    need_llm = False
    need_llm_reasons: list[str] = []

    if l1.confidence == 0.0:
        need_llm = True
        need_llm_reasons.append("规则层未匹配")
    elif l1.confidence < 0.78:
        need_llm = True
        need_llm_reasons.append(f"规则层置信度不足({l1.confidence:.2f})")
    elif (l2_top_role and l1.role != l2_top_role
          and l2_top_score >= 0.30):
        need_llm = True
        need_llm_reasons.append(
            f"规则判断({l1.role})与元数据证据({l2_top_role})不一致"
        )

    if not need_llm and l2_top_score < 0.15 and l1.confidence < 0.85:
        need_llm = True
        need_llm_reasons.append("规则层和元数据层均无强信号")

    # ── Return without L3 if consensus is strong ──
    if not need_llm:
        boosted_conf = min(l1.confidence + l2_top_score * 0.1, 0.95)
        return ClassificationResult(
            role=l1.role,
            confidence=boosted_conf,
            reasons=all_reasons,
            source="rule+metadata",
        )

    # ── Trigger L3 ──
    l2_summary = _build_l2_summary_for_llm(l2)
    if client is None:
        # No LLM client available — return best guess from L1+L2
        best_role = l1.role if l1.confidence > 0 else l2_top_role or "Unknown"
        best_conf = min(l1.confidence + l2_top_score * 0.05, 0.55)
        all_reasons.append("无LLM客户端，仅基于规则+元数据推断")
        return ClassificationResult(
            role="Unknown" if best_conf < 0.60 else best_role,
            confidence=best_conf,
            reasons=all_reasons,
            source="rule+metadata",
        )

    l3 = layer3_llm_classify(ref, client, model, l1.role, l2_summary)
    all_reasons.extend(l3.reasons)
    if need_llm_reasons:
        all_reasons.append(f"触发LLM分类原因: {'; '.join(need_llm_reasons)}")

    # ── Confidence gate ──
    if l3.confidence < 0.60:
        all_reasons.append(
            f"LLM分类置信度({l3.confidence:.2f})低于阈值0.60，归为Unknown"
        )
        return ClassificationResult(
            role="Unknown",
            confidence=l3.confidence,
            reasons=all_reasons,
            source="rule+metadata+llm",
        )

    return l3


def classify_references_batch(refs: list[ClassificationInput],
                               client=None,
                               model: str = "deepseek-v4-pro",
                               verbose: bool = True) -> list[ClassificationResult]:
    """Batch classify multiple references. Prints progress."""
    results: list[ClassificationResult] = []
    total = len(refs)
    for i, ref in enumerate(refs, 1):
        result = classify_reference(ref, client=client, model=model)
        results.append(result)
        if verbose:
            skip_note = " (跳过LLM)" if result.source == "rule+metadata" else ""
            print(
                f"  [{i}/{total}] {result.role} "
                f"(conf={result.confidence:.2f}){skip_note}"
                f" — {ref.title[:50]}"
            )
    return results


# ======================================================================
# Helpers
# ======================================================================

def _parse_json_response(raw: str) -> dict:
    """Robust JSON extraction from LLM response."""
    import json as _json
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        try:
            return _json.loads(m.group(1))
        except _json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return _json.loads(m.group(0))
        except _json.JSONDecodeError:
            pass
    m = re.search(r"\[[\s\S]*\]", raw)
    if m:
        try:
            return _json.loads(m.group(0))
        except _json.JSONDecodeError:
            pass
    return {}


# ======================================================================
# Role → downstream module mappings
# ======================================================================

def role_to_requirement_modifier(classification: ClassificationResult) -> int:
    """Role-based baseline correction for Requirement Level assessment.

    Role indicates PDF benefit, NOT whether LLM knows the content.
    Theory/Review don't reduce PDF need — LLM may not know a Chinese
    theory monograph even if it's classified as Theory Foundation.
    """
    base = {
        "Theory Foundation": 0,
        "Review": 0,
        "Methodology": 0,
        "Empirical Evidence": +1,
        "Frontier": +2,
        "Unknown": +2,
    }
    modifier = base.get(classification.role, 0)
    if classification.confidence < 0.75:
        modifier += 1
    return modifier


_ROLE_TO_CITATION_TYPE: dict[str, str] = {
    "Theory Foundation": "foundational",
    "Methodology": "methodological",
    "Empirical Evidence": "supporting",
    "Review": "supporting",
    "Frontier": "contrasting",
    "Unknown": "supporting",
}


_ROLE_TO_CITATION_DEPTH: dict[str, str] = {
    "Theory Foundation": "deep",
    "Methodology": "normal",
    "Empirical Evidence": "normal",
    "Review": "normal",
    "Frontier": "normal",
    "Unknown": "shallow",
}


_ROLE_TO_MATCH_STRATEGY: dict[str, str] = {
    "Theory Foundation": "theory_priority",
    "Review": "theory_priority",
    "Methodology": "analysis_priority",
    "Empirical Evidence": "analysis_priority",
    "Frontier": "analysis_priority",
    "Unknown": "relevance_only",
}


def get_citation_type(role: str) -> str:
    return _ROLE_TO_CITATION_TYPE.get(role, "supporting")


def get_citation_depth(role: str) -> str:
    return _ROLE_TO_CITATION_DEPTH.get(role, "normal")


def get_match_strategy(role: str) -> str:
    return _ROLE_TO_MATCH_STRATEGY.get(role, "relevance_only")
