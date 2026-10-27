# Paper Assistant

把 DOCX / PDF 处理拆成可检查、可追溯的本地步骤：Agent 理解任务、阅读来源并提出修改，本工具负责解析、校验、编辑和保存新版本。

## 解决的问题

给 Agent 一份文件并让它“改好”，很难核对改了哪里、引用来自哪一页、是否损坏了原文。Paper Assistant 使用文件指纹、准确段落锚点和修改清单，让一次修改留下可复查的输入、方案、结果与报告。

- **保留原件**：每次使用新的尝试目录，拒绝覆盖已有成果。
- **准确定位**：用文件 SHA-256、段落编号和段落指纹检查输入是否发生变化。
- **先检查再写入**：整批校验修改清单，预检失败时不发布本次成果。
- **回到来源**：导出 PDF / txt / md 的原文、物理页序和指纹，核对引用摘录是否在来源中出现。
- **保留人工判断**：程序核对文件和位置；引用是否支持观点、语义是否改变、版式是否合适仍需人工验收。

## 快速体验：只用合成资料

需要 Python 3.10+。在仓库根目录运行：

```powershell
python -m pip install -r requirements-base.txt
python -B -X utf8 examples/synthetic/demo.py
```

演示脚本自行创建合成 DOCX，依次调用解析、只读语言扫描、修改预检和实际编辑。它不调用模型或网络，不读取客户材料。默认保留结果到新的系统临时目录，并打印准确路径。

也可指定一个**尚不存在**的输出目录；已有目录会被拒绝：

```powershell
python -B -X utf8 examples/synthetic/demo.py --output-dir workspaces/demo-first-run
```

脚本检查原件指纹不变、预检不创建交付目录、指定正文替换成功、未修改段落保留，以及四份交付文件齐全。成功输出 `status: passed`。这是文件操作演示，不能代替真实文档的语义与视觉验收。

| 演示文件 | 用途 |
| --- | --- |
| `synthetic.docx` | 脚本生成的原件，无真实论文或客户数据。 |
| `parse.json` | 段落、指纹和结构快照。 |
| `humanize_scan.json` | 原件的语言模式候选，扫描不改写文档。 |
| `edits.json` / `validation.json` | 修改方案及预检结果。 |
| `attempt/` | 输入副本、修改清单、新 DOCX 和检查报告。 |
| `demo_summary.json` | 本轮文件检查及准确输出路径。 |

## 默认工作流程

```text
Agent 阅读 USAGE.md 和对应任务规则
  → parse --json：获取文件指纹、段落编号和原文指纹
  → evidence：读取来源原文和物理页序（有引用任务时）
  → Agent 编写 edits.json：提出修改并判断证据是否支持观点
  → edit --validate-only：整批预检，不生成交付目录
  → edit --attempt：向新的目录保存本轮成果
  → 人工检查内容、引用与 Word / WPS 外观
```

交付前可运行 `humanize-scan`，只报告语言模式候选、理由与保护信息，不自动改写，不构成 AI 检测结论，也不保证通过任何检测器。

一次成功编辑的交付目录包含：

| 文件 | 内容 |
| --- | --- |
| `input.docx` | 本轮实际输入副本。 |
| `edits.json` | 采用的修改清单。 |
| `result.docx` | 生成的新版文档。 |
| `report.json` | 修改记录、检查状态、产物路径与待人工验收状态。 |

完整命令、修改清单格式、输入要求和错误处理见 [USAGE.md](USAGE.md)。给 Agent 的入口说明见 [CLAUDE.md](CLAUDE.md)。

## 支持范围与限制

默认文件流程无需单独配置模型 API Key、嵌入模型或向量数据库。

- 支持 DOCX 结构解析、普通正文替换、限定段落格式调整、方括号数字引用的核对与重编号。
- `evidence` 支持 PDF、UTF-8 txt / md。PDF 使用从 1 开始的物理页序，不含 OCR；扫描件需另提供可读原文。
- `humanize-scan` 只读，不替用户判断作者身份或论文真实性。
- 脚注、尾注、域、修订、文本框和混合直接文字格式等复杂结构会限制或阻断自动编辑。正文表格编辑、任意学校模板全文套版、Word 原生修订不在当前默认范围。
- APA、作者年份制等格式的自动编辑尚未全面验收。
- 数字相同不代表含义相同，摘录存在不代表能支持论断。结果状态 `ready_for_review` 表示等待验收。

`paper_assistant.py`、`router/` 和部分命令保留旧 API 实现；需要额外依赖和显式配置，未完成全流程验收。多能力自动交接、向量检索、自动下载和跨订单缓存不能按默认离线功能宣传。默认流程不会自动回落旧 API。

## 目录与验证

```text
main.py                     文件命令入口
core/                       DOCX 解析、证据、清单校验、编辑及语言扫描
capabilities/               写作、润色、引用、语言体检的规则与实现
skills/citation-assistant/  面向 Agent 的引用流程说明
router/ / prompts/          保留的旧能力路由和提示片段
examples/synthetic/demo.py  无真实资料的独立演示
USAGE.md                    命令与支持边界的详细约定
tests/                      临时合成资料和桩响应的回归用例
```

在安装基础依赖后运行：

```powershell
python -B -X utf8 main.py --help
python -B -X utf8 -m unittest discover -s tests -v
python -B -X utf8 examples/synthetic/demo.py
```

测试不需要客户文件、付费模型 API 或网络。自动测试与合成演示只覆盖相应文件行为，真实交付仍需检查语义与视觉效果。

## 与 paper-platform 的关系

Paper Assistant 是独立的本地文件工具，也作为后续 `paper-platform` 集成版本的组成部分。平台整合文件工具与初稿工作台，通过平台根 CLI 调度。此仓库的独立演示无需安装平台；后续集成实现见 `paper-platform` 项目。

平台集成代码包含后续修复，两个版本的验证结果应分别记录，不能直接相互替代。

## 数据与许可

公开仓库提供代码、规则、文档和合成演示，不包含客户材料、原始文献、私有知识索引、本机配置或个人工作记忆。真实文件放在本地 `workspaces/` 等私有目录，公开前还应检查本次实际提交内容。

本项目采用 [MIT License](LICENSE)，Copyright (c) 2026 dataking5d。改写使用的第三方规则、原始署名与许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。第三方作品不因引用而自动适用本项目的许可。
本次公开候选版的实际运行环境与检查结果见 [VALIDATION.md](VALIDATION.md)。
