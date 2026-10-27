---
name: polishing
description: Academic text polishing — enhance clarity, academic register, and publication quality. Detects and fixes common AI writing traces, weak argument patterns, and register issues. Use when the user wants to polish/improve/enhance/edit academic text. Triggers on 润色/修改/改进/提升/优化/polish/revise/improve/enhance.
version: 0.1.0
---

# Academic Polishing — Router

This skill is split into two layers:
- A **static layer** under `static/` with reusable polishing fragments
- A **dynamic layer** (this file + `manifest.yaml`) for axis-based routing

## Routing protocol

1. Load manifest + always_load files
2. Detect section, language, paper_type
3. Load only matched fragments
4. Polish using loaded material: paper type → section job → failure modes → language rules
5. Output polished text + change log

## Related skills
- `writing` — for drafting new content from scratch
- `citation` — for reference management and insertion
- `humanizing` — read-only AI-trace scan; invoke explicitly, do not auto-run on every polishing request

## 默认工作台执行

读取 [统一调用约定](../../USAGE.md)。当前 Agent 自己执行以上阅读、写作和审查步骤，无需调用 `handler.py` 或配置项目模型 Key。

现有正文先通过 `main.py parse --json` 读取完整目标材料和定位信息。润色后按约定提交段落替换清单，先预检，再生成新尝试；不能用自己的临时脚本绕过文件保护直接覆盖论文。新章节起草输出草稿和证据说明，首版不自动整篇重构 Word。

多能力组合由当前工作台管理：每次使用上一步的实际产物，重新解析新版，不沿用过期段落指纹。最终输出状态是待用户验收，不能替用户确认。
