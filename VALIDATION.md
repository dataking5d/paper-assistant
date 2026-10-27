# 候选版运行检查

检查日期：2026-10-27。此记录针对本次清理后的公开候选快照。

- 现有回归：39 项通过，无跳过用例。
- 合成演示：退出码 0；未使用客户材料、付费模型或外部文献。
- 新目录保护：重复输出路径返回非 0，已有演示成果逐文件哈希保持不变。
- Python 语法、YAML 解析、manifest 目标与 Markdown 本地链接检查通过。
- 原私有 Git 历史未打入此公开快照。

在仓库根目录按 README 安装依赖后，可复跑：

```powershell
python -m unittest discover -s tests -v
python -B -X utf8 examples/synthetic/demo.py
```

本次环境：Windows、Python 3.12.14、Node.js v24.15.0。
使用的 Python 库版本：

```text
numpy==2.5.4
pandas==3.0.1
matplotlib==3.11.2
PyYAML==6.0.3
python-docx==1.2.0
pdfplumber==0.11.9
openpyxl==3.1.5
xlrd==2.0.2
```

此验证使用本地 Python 基础库及单独安装的补充依赖；不是多操作系统兼容性认证。
测试与演示只证明相应文件行为，不等于真实论文的学术、语义、视觉或用户验收。
真实交付仍需重新检查，`user_accepted` 不由自动测试设置为 true。
