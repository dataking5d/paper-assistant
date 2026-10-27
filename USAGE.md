# Paper Assistant 使用说明

Paper Assistant 是供本地 Agent 调用的论文文件工具。宿主 Agent 负责理解需求、读取证据、写作和语义检查；本项目负责解析、校验、编辑和保存成果。默认流程无需额外模型 API Key。

## 先运行合成演示

在仓库根目录运行：

```powershell
python -m pip install -r requirements-base.txt
python -B -X utf8 examples/synthetic/demo.py
```

脚本只创建合成 DOCX，不读取实际订单、不联网、不调用模型。它运行 `parse`、`humanize-scan`、修改清单预检与实际编辑，并验证原件指纹不变、未修改内容保留及交付文件齐全。默认使用新的系统临时目录，运行结束后保留文件并打印路径。

可用 `--output-dir workspaces/demo-first-run` 指定一个尚不存在的目录。脚本拒绝已有目录，避免覆盖此前结果。成功输出 `status: passed`，表示本轮文件检查通过；语义判断与视觉验收仍需另外进行。

此独立演示无需安装 `paper-platform`。该平台整合文件工具与初稿工作台，通过平台根 CLI 调度；后续集成实现见对应项目。

## 开始一单

1. 在订单目录保留客户原件、文献和需求，输出使用独立 `attempts/` 目录。
2. Agent 读取本说明及对应的 `capabilities/writing/SKILL.md`、`capabilities/polishing/SKILL.md` 或 `skills/citation-assistant/SKILL.md`。
3. 检查可处理范围，展示处理方案；材料和要求已明确时不用反复询问。
4. Agent 生成修改清单，先校验，再应用，再查看结果；用户最终验收。
5. 交付前可运行 `humanize-scan` 做只读语言体检；如需改写，仍走上面的修改清单流程。

在项目根目录运行。`python` 表示选定的 Python 3.10+ 环境（已在 3.12 测试），Windows 使用 PowerShell。

```powershell
python -m pip install -r requirements-base.txt
python main.py parse "订单/input/paper.docx" --json
python main.py evidence "订单/input/reference.pdf"
python main.py edit --paper "订单/input/paper.docx" --edits "订单/edits.json" --attempt "订单/attempts/润色引用" --validate-only
python main.py edit --paper "订单/input/paper.docx" --edits "订单/edits.json" --attempt "订单/attempts/润色引用"
```

以上命令输出 JSON。成功退出码为 0；失败为非零，并返回 `needs_input` 或 `failed`。参数语法错误由命令行解析器输出到 stderr。不要从终端展示文本猜测成果路径。

`parse --json` 提供章节、原书目、文件 SHA-256 和段落列表。段落的 `index`、`text_sha256` 必须原样带入编辑清单。`plain` 只说明段落结构简单，不代表能自动润色；混合直接文字格式、标题和保护区域还有额外校验。

`evidence` 支持 PDF、UTF-8 txt/md。PDF 页码为从 1 开始的物理页序，不是印刷页码；空白页不会省略。不含 OCR，扫描件读不到文字就补充可读材料。保留完整提取结果供 Agent 阅读，不靠题名或模型记忆补造证据。

## 交付前语言体检（humanize-scan，只读）

```powershell
python main.py humanize-scan "订单/input/paper.docx" --json
```

扫描 AI 写作痕迹并输出段落锚点、findings 和保护快照。只读：不创建 attempt、不修改 DOCX、
不联网、不调用模型。`paragraph_index` 和 `text_sha256` 与 `parse --json` 的段落快照一致；如需改写，
按修改清单 v1 生成 `replace`，先 `--validate-only`，再写新 attempt。

- `--language zh` / `en` 可强制语言；默认自动检测。
- 只报候选和理由，不给替换文本；`auto_fixable` 恒为 false。
- 不扫标题、表格、图题、参考文献、代码、公式；数字/引用/否定/情态/归因被保护。
- 命中数字/引用的 finding 会升为 `error`，必须人工复核。
- 不把扫描结果当 AI 检测结论，也不保证通过任何检测器。
- 许可与归属见 `THIRD_PARTY_NOTICES.md`。

## 修改清单 v1

下面是结构示例。哈希、段落编号、原文、书目条目及引文均必须用订单的真实数据替换。

```json
{
  "schema_version": 1,
  "source_sha256": "来自 parse 的文件指纹",
  "operations": [
    {
      "op": "replace",
      "paragraph_index": 12,
      "text_sha256": "该段原文指纹",
      "text": "Agent 已审查的新版段落，保留原有数字和引用标记。"
    },
    {
      "op": "insert_citation",
      "paragraph_index": 15,
      "text_sha256": "该段原文指纹",
      "text": "有证据支持的引用表述，不含数字引用标记。",
      "ref_id": "r1"
    },
    {
      "op": "format",
      "paragraph_index": 12,
      "text_sha256": "该段原文指纹",
      "formatting": {"font_cn": "宋体", "font_en": "Times New Roman", "size_pt": 12, "line_spacing": 1.5}
    }
  ],
  "references": {
    "r1": {
      "entry": "经 Agent 与来源核对的完整书目条目，不带编号",
      "source": "input/reference.pdf",
      "source_sha256": "来自 evidence 的源文件指纹",
      "page": 3,
      "quote": "该页中可核对的原文摘录",
      "support_note": "Agent 解释这段证据具体支持哪句话，并说明适用范围"
    }
  }
}
```

证据路径相对于 `edits.json` 所在目录解析，也可使用绝对路径。不要在 JSON 中直接写单反斜杠 Windows 路径；使用 `/` 或 JSON 正确转义。

- `replace`：只替换指定普通正文段落。数字序列和引用标记必须保持；改变数据或事实需要独立审核，不伪装成润色。
- `insert_citation`：在指定正文段落后插入。一条操作对应一个引用来源；Agent 不生成最终引用号码。
- `format`：只改指定普通段落的格式。字段：`font_cn`、`font_en`、`size_pt`、`line_spacing`（倍数）、`space_before_pt`、`space_after_pt`、`first_line_indent_pt`、`left_indent_pt`。
- 可在清单顶层提供 `reference_format`，使用同样格式字段，但不接受 `left_indent_pt`。`first_line_indent_pt` 为负数表示悬挂缩进。仅整理书目时允许 `operations: []` 加非空 `reference_format`。
- 已有文献按完整条目去掉空白差异后准确匹配；有歧义时用 `existing_number` 指定旧编号，同时仍须提供相同完整条目。不要用相似标题猜测合并；无法可靠匹配时人工核对。
- 正文有旧引用时必须已有对应书目。未引用的旧书目保留在已引用条目之后，用户可另外决定是否删除。

同一批先校验全部操作，再应用；任何一步失败不发布本次尝试目录。`--validate-only` 不写文件。前一批修改后，必须重新解析新版文件，不能复用旧指纹。

## 交付和返工

尝试目录必须不存在；已有目录、原件和旧成果不会覆盖。一次成功交付包含：

- `input.docx`：本次实际输入副本。
- `edits.json`：本次 Agent 修改清单。
- `result.docx`：生成成果。
- `report.json`：准确产物路径、实际修改、出处核验和待人工验收状态。

`ready_for_review` 表示文件已生成待验收；`validated` 表示预检通过而未写文件。项目不会自动声称用户已验收。检查报告包含客户原文和摘录，应与订单材料一样保护，不上传公共仓库或跨订单复用。

Agent 必须对照 DOCX/可用预览检查新增文字、原有文字、数字、引用与外观；仅能读取文字时，明确标为尚未视觉验收。程序只能核对摘录出现位置和文件结构，不能证明观点、作者、书目元数据或学术判断都正确。

返工先区分材料不足、要求不明确、模型表达问题、程序错误、外部服务错误。可复现的程序缺陷应先补失败用例，再最小修复，回归通过后重跑新尝试。不要直接覆盖已交付文件。

## 支持边界

- 自动书目处理支持方括号数字引用、按最终首次出现顺序编号，包含组合与范围标记；不代表覆盖某标准全部条目细则。
- 现有 APA、作者年份制等配置仍属于旧能力，默认自动编辑明确拒绝，不能宣传全部格式已验收。
- 普通纯文本段落可润色；混合直接文字格式的段落先提供建议，不能扁平化覆盖。
- 自动引用处理遇到域、脚注/尾注、修订、文本框、无括号上标等不支持结构时会拒绝。普通表格中的方括号引用可同步重编号；正文表格编辑不在首版内。
- 致谢、附录等保护区域不作为自动正文编辑目标。参考文献条目需为独立编号段落；不明确边界、多段条目、复杂书目需人工处理。
- 模板分析主要针对参考文献。正文仅支持明确格式参数，不提供任意论文全文套版或 Word 原生修订模式。
- KB 新保存的索引包含全文；旧索引缺全文时标明证据缺口。引用应回到实际来源，不把摘要或模型归纳冒充全文。
- 不同工作台必须具备本地文件读取和命令执行能力；尚未逐个平台验收。

## 旧 API 入口

默认工作流不创建项目模型客户端、不自动回落外部 API。旧路径需明确启用，例如：

```powershell
python main.py --legacy-api agent --paper "论文.docx" --kb "kb"
python paper_assistant.py --legacy-api "润色论文" --paper "论文.docx"
```

这些路径需要独立模型配置与额外依赖，尚未完整验收。旧 Router 暂时拒绝多能力组合和未实现任务；不会将“审稿”自动改成“引用”。旧 XLSX 编辑入口保留，但也使用严格定位和数字引用安全检查，不允许覆盖原件/旧成果。

旧 PDF 知识库路径可选用 MinerU；如需它，将 `MINERU_EXE` 设为自己安装的可执行文件完整路径。本项目不附带 MinerU，也不查找其他人的本机安装目录；未找到时沿用 PDF 文本提取的回退逻辑。默认 `evidence` 命令不依赖 MinerU。

尚待单独处理：旧 match/generate 表格协议、全流程语义质量、向量检索、自动下载、跨订单缓存，以及其他格式体系。不要把这些路径当成默认生产入口。

## 开发验证

```powershell
python -m unittest discover -s tests -v
```

测试使用临时合成文档与桩响应，不需要客户资料、项目 API Key、网络、LanceDB 或嵌入模型。真实订单需要另外进行语义和视觉验收。
