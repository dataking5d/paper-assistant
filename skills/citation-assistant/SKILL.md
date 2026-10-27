---
name: citation-assistant
description: Use the current host Agent to verify sources and prepare citation edits, then invoke Paper Assistant offline tools to validate and safely edit a DOCX. Triggers on 引用、参考文献、文献核对、citation、bibliography.
---

# 引用处理

先读取 [统一调用约定](../../USAGE.md)，再读取所需的质量规则。统一调用约定优先于旧规则中关于“一键 API”或“100% 覆盖”的说法。

1. 检查订单要求、正文、文献和支持边界。展示处理范围与缺失材料；明确要求无需重复询问。
2. 调用 `main.py parse --json` 获取正文和段落锚点；调用 `main.py evidence` 获取实际来源和页码。
3. 当前 Agent 阅读原文、判断关联，核对书目身份并写出引用；不能凭标题或模型记忆声称已核实。
4. 按统一约定生成 `edits.json`，每条引用保留摘录和支持理由。不要强制用完所有文献，不自行分配最终号码。
5. 先 `edit --validate-only`，再应用到新尝试目录。预检失败先修正本次材料或清单。
6. 读取报告和成品，核对内容、位置、旧引用与致谢/附录；将成果及待确认事项交用户验收。

默认不需要模型 API Key、LanceDB 或项目内部 Agent。旧 API 入口仅在用户明确选择时使用，不能作为无声降级。
