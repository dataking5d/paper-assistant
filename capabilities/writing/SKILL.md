---
name: writing
description: Academic writing assistance — draft, restructure, or plan thesis/journal sections. Use when the user wants to write or rebuild an abstract, introduction, literature review, method, results, discussion, conclusion, or outline. Triggers on 写作/写论文/写引言/写摘要/写方法/写结论/大纲/框架/outline/draft/write.
version: 0.1.0
---

# Academic Writing — Router

This skill is split into two layers:

- A **static layer** under `static/` that holds reusable prompt fragments (core stance + workflow, per-section drafting rules, language-specific rules, paper-type rules).
- A **dynamic layer** (this file + `manifest.yaml`) that detects the request's axes and loads only the fragments needed.

## Routing protocol

### 1. Load the manifest and core layer

Read [manifest.yaml](manifest.yaml). Also read every file under `always_load`.

### 2. Detect axis values

- `section` — abstract / intro / lit-review / method / results / discussion / conclusion / outline
- `language` — zh / en / zh-to-en
- `paper_type` — thesis / journal

### 3. Load matching fragments

Load only the fragments for the detected axis values.

### 4. Draft using loaded material

Priority order:
1. Core stance → surface missing claim/evidence before drafting
2. Terminology Ledger → lock canonical terms
3. Paper-type rules → structure expectations
4. Section-specific drafting rules
5. Language-specific rules (apply last)

### 5. Reach for references only when needed

Files under `references/` are deep references, not defaults. Open on demand per the manifest's `references.on_demand` table.

## 默认工作台执行

读取 [统一调用约定](../../USAGE.md)。当前 Agent 自己执行以上阅读、写作和审查步骤，无需调用 `handler.py` 或配置项目模型 Key。

现有正文先通过 `main.py parse --json` 读取完整目标材料和定位信息。润色后按约定提交段落替换清单，先预检，再生成新尝试；不能用自己的临时脚本绕过文件保护直接覆盖论文。新章节起草输出草稿和证据说明，首版不自动整篇重构 Word。

多能力组合由当前工作台管理：每次使用上一步的实际产物，重新解析新版，不沿用过期段落指纹。最终输出状态是待用户验收，不能替用户确认。
