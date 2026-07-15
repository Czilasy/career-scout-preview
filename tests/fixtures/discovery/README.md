# Discovery test fixtures

脱敏样本仅供 feature 004 单元/集成/黄金样本评估使用。所有简历文本为虚构内容，
不含任何真实候选人信息；联系方式、证件号、住址等敏感字段已替换为虚构标记
`[REDACTED-PII-*]`。样本仅用于验证证据抽取、方向归并、搜索计划、评估分类、
隐私 redact 与删除级联行为。

## 简历样本（7 类）

| 文件 | 类别 | 覆盖场景 |
|---|---|---|
| `resume_single_path.txt` | single-path | 单一职业方向，证据充分，预期 1 个 core 方向 |
| `resume_cross_family.txt` | cross-family | 跨岗位族经历，预期 ≥2 方向（core + adjacent） |
| `resume_intent_unclear.txt` | intent-unclear | 经历散乱，存在 unknowns，预期 ≥1 unknown 项 |
| `resume_long_tenure_low_project.txt` | long-tenure-low-project | 长 tenure 但项目描述稀疏，证据少，验证证据门控 |
| `resume_junior.txt` | junior | 应届/初级，directions 含 growth 类型 |
| `resume_multi_industry_gap.txt` | multi-industry-gap | 多行业跳转，验证方向归并与去重 |
| `resume_no_salary_city.txt` | no-salary-city | 缺薪资/城市，验证硬约束不补造 |

## 人工标注

- `annotations.json`：每份简历对应 `directions`（含 name/type/evidence_terms/gaps）
  与 `jobs`（含 job_id/direction_id/expected_category）人工标注，用于黄金样本评估。

## 评估脚本

- `evaluate.py`：基于人工标注计算方向接受率、Precision@20、召回率、硬约束违规率、
  多方向覆盖率、解释忠实度。仅读取本目录样本，不调用远程 AI。

## 隐私约定

- 任何样本若意外包含类似真实 PII 的字符串，必须在 `evaluate.py` 启动时被
  `SENSITIVE_PATTERNS` 命中并标记为 `sensitive=true`，禁止进入 evidence。
