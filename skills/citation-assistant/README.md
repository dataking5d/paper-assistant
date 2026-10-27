# 引用处理技能

本目录提供宿主 Agent 使用的引用处理规则。Agent 读取实际文献、判断来源是否支持观点并编写修改清单；Paper Assistant 负责检查文件与段落位置、核对摘录、编辑 DOCX 和保存新版本。

先读 [统一调用约定](../../USAGE.md)，再读 [SKILL.md](SKILL.md) 和 [质量规则](references/quality-rules.md)。命令格式和支持边界以统一调用约定为准。

## 默认离线流程

以下命令均在**仓库根目录**执行，不是在本技能目录。文件路径是示例，需替换为自己的本地材料；引用内容和修改清单须由 Agent 对照实际来源生成。

```powershell
python main.py parse input/paper.docx --json
python main.py evidence input/reference.pdf
python main.py edit --paper input/paper.docx --edits edits.json --attempt workspaces/citation-first-run --validate-only
python main.py edit --paper input/paper.docx --edits edits.json --attempt workspaces/citation-first-run
```

1. 解析正文，取得文件指纹、段落编号和段落指纹。
2. 读取来源原文，核对书目身份、摘录、物理页序和观点支持关系。
3. 按统一调用约定准备 `edits.json`，保留来源证据，不凭题名或模型记忆补造引用。
4. 整批预检通过后，应用到一个尚不存在的尝试目录。
5. 查看检查报告和新文档，人工验收语义、数字、引用与版式。

默认流程不需要项目模型 API Key、LanceDB 或嵌入模型。基础依赖和只用合成 DOCX 的可运行演示见 [项目 README](../../README.md)。

## 支持边界

默认自动编辑面向方括号数字引用。复杂 Word 结构、作者年份制等其他格式的限制见统一调用约定。程序确认摘录存在于来源，不代表该摘录必然支持正文观点；`ready_for_review` 仍表示等待人工验收。

仓库保留 `match`、`generate`、`full` 等旧 API 实现，需要额外依赖、独立配置和显式启用，尚未完成全流程验收。它们不属于这里的默认流程，也不能作为静默回退路径。不要把旧实现描述成已经可用的一键自动交付。