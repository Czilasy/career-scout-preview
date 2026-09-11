# 测试样本索引

本目录存放测试与实现门禁使用的无敏感数据样本。所有样本均为虚构，不含真实个人身份信息、真实 API Key 或真实岗位标识。

## 智联平台核验清单

- `zhilian/fixture_manifest.json` — 智联平台外部事实核验清单（2026-08-04 冻结）：记录已核验事实、阻断项与启用决策，供实施门禁参考，测试代码不直接加载。

## 简历样本

简历文本与 PDF/DOCX 字节由 `tests/test_workbench_fixtures.py` 中的 `sample_resume_text()`、`sample_pdf_bytes()`、`sample_docx_bytes()` 在运行时动态生成，不落盘真实简历文件。动态生成的简历内容为虚构的"张三 / Python 后端 / 上海"文本，不含任何真实个人信息。

## 安全约束

- 不得在此目录放置真实简历、真实 API Key 或真实 BOSS 抓取结果。
- 智联核验清单只含公开字段与冻结设计决策，不含敏感数据。
