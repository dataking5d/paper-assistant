---
name: humanizing
description: 只读扫描论文中的 AI 写作痕迹，输出段落锚点、findings 和保护快照；Agent 决定是否改写，不自动改事实，不生成替换文本。
version: 0.1.0
---

# Humanizing — Router

本能力只做“诊断”，不做自动改写。它把可确定检测的模式变成可审计的 JSON，交给 Agent
结合上下文判断；真正的修改仍走 `edits.json → validate-only → attempt`。

## 何时使用

- 用户说“去 AI 味 / 降 AIGC / 润色得像人写的 / 检查模板化表达”。
- 论文润色完成后，作为交付前的最后一道语言体检。
- 不用于判断作者身份，不保证通过任何 AI 检测器。

## 命令

```powershell
python main.py humanize-scan "论文.docx" --json
```

只读：不创建 attempt、不改 DOCX、不联网、不调用模型。

## 输出

```json
{
  "schema_version": 1,
  "language": "zh",
  "ruleset_version": "humanizer-zh-31+humanizer-en-26@1",
  "document_sha256": "...",
  "summary": {"paragraphs_scanned": 0, "findings": 0, "by_severity": {}},
  "paragraphs": [
    {
      "paragraph_index": 12,
      "section": "四、结果与分析",
      "text_sha256": "...",
      "findings": [
        {"rule_id": "zh.pattern.01.not_x_but_y", "category": "contrast",
         "severity": "low", "confidence": 0.7, "span": [0, 12],
         "matched_text": "不是……而是……", "reason": "...",
         "suggested_action": "rewrite_clause", "auto_fixable": false,
         "requires_agent": true, "protected": false}
      ],
      "preservation_snapshot": {"numbers": [], "citations": [], "negations": [], "hedges": [], "attributions": []}
    }
  ],
  "skipped": ["heading", "caption", "figure", "table", "references_section"]
}
```

`paragraph_index` 和 `text_sha256` 与 `python main.py parse --json` 的段落快照一致；Agent 可直接
用它们生成 `edits.json` 的 `replace` 操作。

## 改写协议

1. 先读 `preservation_snapshot`，确认数字、引用、否定、情态、归因。
2. 只改被标记的句子；没有问题的段落保持原样。
3. 不新增事实、案例、数据、引用、页码或机构名。
4. 不把“可能/相关/未发现/不能排除”改成“确定/导致/证明”。
5. 三项并列只在确实空泛或重复时合并；有独立信息就保留。
6. 改写后重新扫描；`error` 级 finding 必须人工复核。
7. 用 `edit --validate-only` 预检，再生成新 attempt；不直接覆盖原 DOCX。

## 不扫 / 不误报

- 不扫标题、表格、图题、参考文献、代码、公式、专名。
- 技术义（闭环控制、鲁棒、robust standard error）不因词表命中而改。
- 中文全角/弯引号不是错误；被动、四字格、“首先/其次/综上”不是错误。
- 单个弱词只报 `low`；没有作者样本不套用“破折号一律禁用”。

## 相关文件

- [stance](static/core/stance.md)
- [protection](static/core/protection.md)
- [workflow](static/core/workflow.md)
- [severity](static/core/severity.md)
- [output-contract](static/core/output-contract.md)
- [patterns-zh-31](references/patterns-zh-31.md)
- [patterns-en-26](references/patterns-en-26.md)
- [boundaries](references/boundaries.md)
- [provenance](references/provenance.md)

## 许可

规则参考并改写自 `blader/humanizer`（MIT）与 `op7418/Humanizer-zh`（MIT）。归属与 MIT 全文见
项目根目录 `THIRD_PARTY_NOTICES.md`。
