"""Template analysis, requirements parsing, and auto-detection for reference formats."""

from __future__ import annotations

import json
import re
from docx import Document
from docx.shared import Pt, Emu
from docx.oxml.ns import qn

from .style_config import ReferenceStyleConfig
from .docx_parser import is_post_reference_heading

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_REF_HEADING_PATTERNS = [
    re.compile(r"^参考文献$"),
    re.compile(r"^References$", re.IGNORECASE),
    re.compile(r"^Bibliography$", re.IGNORECASE),
    re.compile(r"^引用文献$"),
    re.compile(r"^参考书目$"),
    re.compile(r"^Works\s+Cited$", re.IGNORECASE),
]

_POST_REF_MARKERS = [
    re.compile(r"^致谢"),
    re.compile(r"^Acknowledgements?", re.IGNORECASE),
    re.compile(r"^附录"),
    re.compile(r"^Appendix", re.IGNORECASE),
    re.compile(r"^声明"),
    re.compile(r"^Declaration", re.IGNORECASE),
    re.compile(r"^作者简介"),
    re.compile(r"^研究成果"),
    re.compile(r"^在读期间"),
    re.compile(r"^攻读.*期间"),
    re.compile(r"^已发表"),
    re.compile(r"^发表论文"),
]

_CJK_RANGE = re.compile(r"[一-鿿㐀-䶿　-〿＀-￯]")


def _has_cjk(text: str) -> bool:
    """True if text contains CJK characters."""
    return bool(_CJK_RANGE.search(text))


def _find_ref_section(doc: Document) -> tuple[int, int] | None:
    """Find the start and end paragraph indices of the references section.

    Returns (start_idx, end_idx) or None if not found.
    """
    ref_start = None
    for i, para in enumerate(doc.paragraphs):
        text = para.text.strip()
        for pat in _REF_HEADING_PATTERNS:
            if pat.match(text):
                ref_start = i
                break
        if ref_start is not None:
            break

    if ref_start is None:
        return None

    ref_end = len(doc.paragraphs)
    for i in range(ref_start + 1, len(doc.paragraphs)):
        text = doc.paragraphs[i].text.strip()
        if is_post_reference_heading(text):
            ref_end = i
            break
        for pat in _POST_REF_MARKERS:
            if pat.match(text):
                ref_end = i
                break
        if ref_end < len(doc.paragraphs):
            break

    return ref_start, ref_end


def _extract_paragraph_formatting(para) -> dict:
    """Extract font, size, spacing, and indent from a paragraph's first run."""
    fmt = {
        "font_cn": "宋体",
        "font_en": "Times New Roman",
        "size": 12,
        "line_spacing": 1.5,
        "space_before": 0,
        "space_after": 0,
        "indent_type": "hanging",
        "indent_value": "2char",
    }

    # Font from first run
    if para.runs:
        run = para.runs[0]
        if run.font.name:
            fmt["font_en"] = run.font.name
        if run.font.size:
            fmt["size"] = run.font.size.pt
        # East Asian font
        try:
            rpr = run._element.rPr
            if rpr is not None:
                rfonts = rpr.find(qn("w:rFonts"))
                if rfonts is not None:
                    ea = rfonts.get(qn("w:eastAsia"))
                    if ea:
                        fmt["font_cn"] = ea
        except Exception:
            pass

    # Paragraph formatting
    pf = para.paragraph_format
    if pf.line_spacing is not None:
        fmt["line_spacing"] = pf.line_spacing
    if pf.space_before is not None:
        fmt["space_before"] = pf.space_before.pt if hasattr(pf.space_before, "pt") else 0
    if pf.space_after is not None:
        fmt["space_after"] = pf.space_after.pt if hasattr(pf.space_after, "pt") else 0

    # Indentation
    first_line = pf.first_line_indent
    left_indent = pf.left_indent
    if first_line is not None and first_line < 0:
        fmt["indent_type"] = "hanging"
        # Convert points to approximate character widths at the detected font size.
        char_val = abs(first_line.pt) / fmt["size"]
        fmt["indent_value"] = f"{round(char_val)}char"
    elif first_line is not None and first_line > 0:
        fmt["indent_type"] = "first_line"
        char_val = first_line.pt / fmt["size"]
        fmt["indent_value"] = f"{round(char_val)}char"
    elif left_indent is not None and left_indent > 0:
        fmt["indent_type"] = "hanging"
        char_val = left_indent.pt / fmt["size"]
        fmt["indent_value"] = f"{round(char_val)}char"

    return fmt


def _detect_numbering_rule(paragraphs: list) -> str:
    """Detect numbering style from reference paragraphs.

    Returns one of: bracket_numeric, author_year_paren, superscript.
    """
    bracket_count = 0
    auth_year_count = 0
    total = min(len(paragraphs), 5)

    for i in range(total):
        text = paragraphs[i].text.strip()
        if re.match(r"^\[\d+\]", text):
            bracket_count += 1
        elif re.match(r"^[A-Z][a-z]+.*\(\d{4}[a-z]?\)", text):
            auth_year_count += 1
        elif re.match(r"^[A-Z][a-z]+.*\d{4}", text):
            auth_year_count += 0.5

    if bracket_count >= total * 0.5:
        return "bracket_numeric"
    if auth_year_count >= total * 0.5:
        return "author_year_paren"
    return "bracket_numeric"


def _detect_ordering_rule(paragraphs: list, numbering: str) -> str:
    """Detect ordering rule.

    For numeric styles: check if numbers increase sequentially → first_appearance.
    For author-year: check if English entries are alphabetical → author_alpha.
    """
    if numbering == "bracket_numeric":
        return "first_appearance"

    # Check if English refs are alphabetically sorted by author
    en_authors = []
    for para in paragraphs:
        text = para.text.strip()
        if not _has_cjk(text):
            match = re.match(r"^([A-Z][a-z]+)", text)
            if match:
                en_authors.append(match.group(1).lower())

    if len(en_authors) >= 3 and en_authors == sorted(en_authors):
        return "author_alpha"
    return "first_appearance"


def _detect_language_split(paragraphs: list) -> tuple[str, str, str]:
    """Detect CN/EN split and sort rules.

    Returns (language_split_rule, chinese_sort_rule, english_sort_rule).
    """
    cn_positions = []
    en_positions = []

    for i, para in enumerate(paragraphs):
        text = para.text.strip()
        if not text:
            continue
        if _has_cjk(text):
            cn_positions.append(i)
        else:
            en_positions.append(i)

    if not cn_positions or not en_positions:
        return "mixed", "first_appearance", "first_appearance"

    all_cn_first = all(c < e for c in cn_positions for e in en_positions)
    all_en_first = all(e < c for c in cn_positions for e in en_positions)

    split_rule = "mixed"
    if all_cn_first:
        split_rule = "cn_first"
    elif all_en_first:
        split_rule = "en_first"

    # English sort detection
    en_authors = []
    for pos in en_positions:
        text = paragraphs[pos].text.strip()
        match = re.match(r"^([A-Z][a-z]+)", text)
        if match:
            en_authors.append(match.group(1).lower())

    eng_sort = "alpha" if len(en_authors) >= 3 and en_authors == sorted(en_authors) else "first_appearance"

    return split_rule, "first_appearance", eng_sort


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_template_docx(template_path: str) -> ReferenceStyleConfig:
    """Analyze a .docx template to extract reference formatting rules.

    Extracts: numbering style, CN/EN split, ordering, font, spacing, indentation.
    No AI involved — pure python-docx formatting extraction.
    """
    doc = Document(template_path)

    ref_section = _find_ref_section(doc)
    if ref_section is None:
        # No ref section found — return a minimal config with just body formatting
        fmt = {"font_cn": "宋体", "font_en": "Times New Roman", "size": 12,
               "line_spacing": 1.5, "space_before": 0, "space_after": 0,
               "indent_type": "hanging", "indent_value": "2char"}
        if doc.paragraphs:
            fmt = _extract_paragraph_formatting(doc.paragraphs[0])
        return ReferenceStyleConfig(
            source_type="template",
            style_name="模板分析",
            font_rule={"cn": {"name": fmt["font_cn"], "size": fmt["size"]},
                       "en": {"name": fmt["font_en"], "size": fmt["size"]}},
            spacing_rule={"line_spacing": fmt["line_spacing"],
                          "space_before": fmt["space_before"],
                          "space_after": fmt["space_after"]},
            indent_rule={"type": fmt["indent_type"], "value": fmt["indent_value"]},
        )

    ref_start, ref_end = ref_section
    ref_paras = [doc.paragraphs[i] for i in range(ref_start + 1, ref_end)]
    ref_paras = [p for p in ref_paras if p.text.strip()]

    if not ref_paras:
        fmt = _extract_paragraph_formatting(doc.paragraphs[ref_start])
        return ReferenceStyleConfig(
            source_type="template",
            style_name="模板分析",
            font_rule={"cn": {"name": fmt["font_cn"], "size": fmt["size"]},
                       "en": {"name": fmt["font_en"], "size": fmt["size"]}},
            spacing_rule={"line_spacing": fmt["line_spacing"],
                          "space_before": fmt["space_before"],
                          "space_after": fmt["space_after"]},
            indent_rule={"type": fmt["indent_type"], "value": fmt["indent_value"]},
        )

    numbering = _detect_numbering_rule(ref_paras)
    ordering = _detect_ordering_rule(ref_paras, numbering)
    split, cn_sort, en_sort = _detect_language_split(ref_paras)

    # Extract formatting from first reference paragraph
    fmt = _extract_paragraph_formatting(ref_paras[0])

    return ReferenceStyleConfig(
        source_type="template",
        style_name="模板分析",
        ordering_rule=ordering,
        numbering_rule=numbering,
        language_split_rule=split,
        english_sort_rule=en_sort,
        chinese_sort_rule=cn_sort,
        font_rule={"cn": {"name": fmt["font_cn"], "size": fmt["size"]},
                   "en": {"name": fmt["font_en"], "size": fmt["size"]}},
        spacing_rule={"line_spacing": fmt["line_spacing"],
                      "space_before": fmt["space_before"],
                      "space_after": fmt["space_after"]},
        indent_rule={"type": fmt["indent_type"], "value": fmt["indent_value"]},
    )


_FONT_SIZE_MAP = {
    "小四": 12, "五号": 10.5, "小五": 9, "四号": 14, "小三": 15,
    "三号": 16, "小二": 18, "一号": 26, "小一": 24,
    "六号": 7.5, "小六": 6.5, "七号": 5.5, "八号": 5,
}


def _map_font_size(val) -> int | None:
    """Map Chinese font size names to pt values."""
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        val_lower = val.strip()
        if val_lower in _FONT_SIZE_MAP:
            return _FONT_SIZE_MAP[val_lower]
        try:
            return int(val_lower)
        except ValueError:
            pass
    return None


_REQUIREMENTS_PARSE_PROMPT = """你是一个学术论文格式解析器。用户会用中文或英文描述参考文献格式要求。
请将要求解析为以下JSON结构：

{
  "ordering_rule": "first_appearance" | "author_year" | "author_alpha",
  "numbering_rule": "bracket_numeric" | "author_year_paren" | "superscript",
  "language_split_rule": "cn_first" | "en_first" | "mixed",
  "english_sort_rule": "alpha" | "first_appearance",
  "chinese_sort_rule": "first_appearance" | "pinyin",
  "font_cn": "中文字体名（如宋体、仿宋、黑体）",
  "font_en": "英文字体名（如Times New Roman）",
  "font_size": 字号数字（12=小四, 10.5=五号, 14=四号, 9=小五）,
  "line_spacing": 行距倍数（如1.5）,
  "space_before": 段前间距pt,
  "space_after": 段后间距pt,
  "indent_type": "hanging" | "first_line" | "none",
  "indent_value": "缩进量（如2char、0.75cm）"
}

规则：
- "中文在前，英文在后" → language_split_rule: "cn_first"
- "英文按作者姓氏字母排序" → english_sort_rule: "alpha"
- "悬挂缩进" → indent_type: "hanging"
- "参考文献采用宋体小四" → font_cn: "宋体", font_size: 12
- 未提及的字段用 null，不要猜测
- 只输出JSON，不要其他内容"""


def parse_requirements_text(text: str, llm_client) -> ReferenceStyleConfig:
    """Parse user's format requirements text into a ReferenceStyleConfig via LLM.

    Args:
        text: User's requirements description (Chinese or English).
        llm_client: Anthropic client instance (messages.create).

    Returns:
        ReferenceStyleConfig with requirements merged over defaults.

    Raises:
        ValueError: If LLM response cannot be parsed.
    """
    from .style_config import get_builtin_style

    response = llm_client.messages.create(
        model="deepseek-v4-pro",
        max_tokens=1024,
        temperature=0,
        messages=[
            {"role": "user", "content": f"{_REQUIREMENTS_PARSE_PROMPT}\n\n用户要求：\n{text}"},
        ],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f"LLM 返回无法解析的 JSON: {raw[:200]}...")

    config = get_builtin_style("gbt7714-numeric")
    config.source_type = "requirements"
    config.style_name = "用户要求"

    # Merge ordering/numbering
    for key in ("ordering_rule", "numbering_rule", "language_split_rule",
                "english_sort_rule", "chinese_sort_rule"):
        if key in parsed and parsed[key] is not None:
            setattr(config, key, parsed[key])

    # Merge font
    cn_name = parsed.get("font_cn") or config.font_rule["cn"]["name"]
    en_name = parsed.get("font_en") or config.font_rule["en"]["name"]
    size = _map_font_size(parsed.get("font_size")) or config.font_rule["cn"]["size"]
    config.font_rule = {
        "cn": {"name": cn_name, "size": size},
        "en": {"name": en_name, "size": size},
    }

    # Merge spacing
    if parsed.get("line_spacing") is not None:
        config.spacing_rule["line_spacing"] = float(parsed["line_spacing"])
    if parsed.get("space_before") is not None:
        config.spacing_rule["space_before"] = float(parsed["space_before"])
    if parsed.get("space_after") is not None:
        config.spacing_rule["space_after"] = float(parsed["space_after"])

    # Merge indent
    if parsed.get("indent_type") is not None:
        config.indent_rule["type"] = parsed["indent_type"]
    if parsed.get("indent_value") is not None:
        config.indent_rule["value"] = parsed["indent_value"]

    return config


def auto_detect_from_paper(paper_path: str) -> ReferenceStyleConfig:
    """Scan the paper's own reference section to infer its formatting.

    Shares the same detection logic as analyze_template_docx but reads from
    the paper itself rather than a separate template.
    """
    return analyze_template_docx(paper_path)
