# Output Contract

`humanize-scan --json` 输出：

- `schema_version`：1。
- `language`：`zh` / `en` / `auto`。
- `ruleset_version`：规则包版本。
- `document_sha256`：输入 DOCX 的 SHA-256。
- `summary`：`paragraphs_scanned`、`paragraphs_with_findings`、`findings`、`by_severity`、`by_category`。
- `paragraphs[]`：`paragraph_index`、`section`、`text_sha256`、`findings[]`、`preservation_snapshot`。
- `skipped[]`：未扫描区域说明。

`findings[]` 字段：

- `rule_id`：规则 ID，例如 `zh.pattern.01.not_x_but_y`。
- `category`：contrast / rhythm / filler / punctuation / attribution / inflation / closure / wordiness。
- `severity`：low / medium / high / error。
- `confidence`：0–1，仅供排序，不是概率保证。
- `span`：`[start, end]`，基于该段文本。
- `matched_text`：命中片段。
- `reason`：为什么可能是问题，以及保留条件。
- `suggested_action`：`keep` / `delete_filler` / `merge` / `split` / `rewrite_clause` / `ask_author` / `verify_citation`。
- `auto_fixable`：恒为 `false`。
- `requires_agent`：恒为 `true`。
- `protected`：命中数字/引用/限定/归因时为 `true`。

扫描器不输出替换文本、不创建 attempt、不写 DOCX。
