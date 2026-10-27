"""Reference style configuration dataclass and built-in standard format definitions."""

from dataclasses import dataclass, field
from copy import deepcopy


@dataclass
class ReferenceStyleConfig:
    """Unified reference formatting configuration.

    Used throughout the pipeline: agent → matcher → generator → editor.
    Priority: template > requirements > standard > auto_detect.
    """

    source_type: str = "standard"
    style_name: str = "gbt7714-numeric"

    # Ordering & numbering
    ordering_rule: str = "first_appearance"  # first_appearance | author_year | author_alpha
    numbering_rule: str = "bracket_numeric"  # bracket_numeric | author_year_paren | superscript

    # Language split
    language_split_rule: str = "mixed"          # cn_first | en_first | mixed
    english_sort_rule: str = "alpha"            # alpha | first_appearance
    chinese_sort_rule: str = "first_appearance"  # first_appearance | pinyin

    # Formatting
    font_rule: dict = field(default_factory=lambda: {
        "cn": {"name": "宋体", "size": 12},
        "en": {"name": "Times New Roman", "size": 12},
    })
    spacing_rule: dict = field(default_factory=lambda: {
        "line_spacing": 1.5,
        "space_before": 0,
        "space_after": 0,
    })
    indent_rule: dict = field(default_factory=lambda: {
        "type": "hanging",  # hanging | first_line | none
        "value": "2char",
    })

    # Extra overrides from template or requirements
    custom_rules: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Built-in standard format definitions
# ---------------------------------------------------------------------------

BUILTIN_STYLES: dict[str, ReferenceStyleConfig] = {
    "gbt7714-numeric": ReferenceStyleConfig(
        source_type="standard",
        style_name="GB/T 7714 顺序编码制",
        ordering_rule="first_appearance",
        numbering_rule="bracket_numeric",
        language_split_rule="mixed",
        english_sort_rule="first_appearance",
        chinese_sort_rule="first_appearance",
    ),
    "gbt7714-author-year": ReferenceStyleConfig(
        source_type="standard",
        style_name="GB/T 7714 著者-出版年制",
        ordering_rule="author_year",
        numbering_rule="author_year_paren",
        language_split_rule="mixed",
        english_sort_rule="alpha",
        chinese_sort_rule="first_appearance",
    ),
    "apa7": ReferenceStyleConfig(
        source_type="standard",
        style_name="APA 7",
        ordering_rule="author_alpha",
        numbering_rule="author_year_paren",
        language_split_rule="mixed",
        english_sort_rule="alpha",
        chinese_sort_rule="pinyin",
    ),
    "ieee": ReferenceStyleConfig(
        source_type="standard",
        style_name="IEEE",
        ordering_rule="first_appearance",
        numbering_rule="bracket_numeric",
        language_split_rule="mixed",
        english_sort_rule="first_appearance",
        chinese_sort_rule="first_appearance",
    ),
    "harvard": ReferenceStyleConfig(
        source_type="standard",
        style_name="Harvard",
        ordering_rule="author_alpha",
        numbering_rule="author_year_paren",
        language_split_rule="mixed",
        english_sort_rule="alpha",
        chinese_sort_rule="pinyin",
    ),
    "mla": ReferenceStyleConfig(
        source_type="standard",
        style_name="MLA",
        ordering_rule="author_alpha",
        numbering_rule="author_year_paren",
        language_split_rule="mixed",
        english_sort_rule="alpha",
        chinese_sort_rule="pinyin",
    ),
    "chicago": ReferenceStyleConfig(
        source_type="standard",
        style_name="Chicago",
        ordering_rule="author_alpha",
        numbering_rule="author_year_paren",
        language_split_rule="mixed",
        english_sort_rule="alpha",
        chinese_sort_rule="pinyin",
    ),
    "custom": ReferenceStyleConfig(
        source_type="standard",
        style_name="自定义规则",
    ),
}

# Human-readable names and descriptions for interactive menu
STYLE_MENU: dict[str, dict] = {
    "1": {
        "id": "gbt7714-numeric",
        "name": "GB/T 7714 顺序编码制",
        "desc": "正文[1][2][3]，参考文献按首次引用顺序排列（国内本科/硕士最常用）",
    },
    "2": {
        "id": "gbt7714-author-year",
        "name": "GB/T 7714 著者-出版年制",
        "desc": "正文(张三,2023)，参考文献按作者和年份组织",
    },
    "3": {
        "id": "apa7",
        "name": "APA 7",
        "desc": "正文(Smith, 2023)，参考文献按作者字母排序（心理学/教育学/社科常见）",
    },
    "4": {
        "id": "ieee",
        "name": "IEEE",
        "desc": "正文[1]，参考文献按引用顺序排列（计算机/工程常见）",
    },
    "5": {
        "id": "harvard",
        "name": "Harvard",
        "desc": "正文(Smith, 2023)，参考文献按作者字母排序（商科/管理学常见）",
    },
    "6": {
        "id": "mla",
        "name": "MLA",
        "desc": "正文(Smith 2023)，参考文献按作者字母排序（英语/文学常见）",
    },
    "7": {
        "id": "chicago",
        "name": "Chicago",
        "desc": "脚注或作者-日期制（历史/哲学常见）",
    },
    "8": {
        "id": "custom",
        "name": "自定义规则",
        "desc": "中文在前/英文在前、中英文分别排序、保持原编号或重新编号",
    },
}


def get_builtin_style(style_id: str) -> ReferenceStyleConfig:
    """Return a copy of a built-in style config by ID.

    Returns a deep copy so callers can mutate without affecting the global.
    Falls back to gbt7714-numeric for unknown IDs.
    """
    config = BUILTIN_STYLES.get(style_id)
    if config is None:
        config = BUILTIN_STYLES["gbt7714-numeric"]
    return deepcopy(config)
