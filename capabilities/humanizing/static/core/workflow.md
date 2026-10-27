# Workflow

1. **扫描**：`python main.py humanize-scan <docx> --json`，得到段落锚点和 findings。
2. **分级**：先处理 `high` / `error`，再处理 `medium`；`low` 只在上下文确实空泛时改。
3. **对照原文**：读 `preservation_snapshot`，确认数字、引用、否定、情态、归因。
4. **改写**：只改被标记的句子；不新增事实；不改标题、表格、图题、参考文献。
5. **复核**：重新扫描；`error` 必须人工复核；对比原文，确认没有增删独立信息。
6. **落盘**：按 `USAGE.md` 的 edits.json v1 生成 `replace` 操作；先 `--validate-only`，再写新 attempt。
7. **验收**：用户确认后才交付；`ready_for_review` 不等于客户已接受。

## 什么时候停

- findings 与原文语义冲突，无法判断是否该改。
- 改写需要新增原文没有的数据、引用或事实。
- 命中数字/引用/归因且无法确认保护范围。
- 作者原文本身是引语、法律条款、标准定义或专有术语。
