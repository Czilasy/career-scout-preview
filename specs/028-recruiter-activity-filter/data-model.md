# Data Model: 028 招聘者活跃时间筛选

## 实体 1：招聘者活跃事实（recruiter_activity）

一个岗位的招聘者上次活跃观测，归一化后的字典形态（存于 `job["extra"]["recruiter_activity"]`，持久化为 `jobs.extra_json` / `screening_results.extra_json` 内嵌字段）。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `source` | `"boss" \| "zhilian"` | 数据来源平台 |
| `text` | `str` | 原始展示文本；Boss=名片活跃行（如「刚刚活跃」），智联=状态文本（如「今日活跃」，仅展示不作判定） |
| `last_online_ms` | `int \| null` | 智联 `staff.lastOnlineTime` 毫秒时间戳；Boss 恒为 null |
| `age_lower_days` | `float` | 活跃距今天数区间下界（确定至少这么久） |
| `age_upper_days` | `float \| null` | 区间上界；null 表示无上界（如「半年前活跃」） |
| `known` | `bool` | 是否可判定；false = 未知（无名片/无活跃行/映射表外/无时间戳） |

- Boss 映射：`在线→[0,0]`、`刚刚活跃→[0,0]`、`今日活跃→[0,1]`、`昨日活跃→[1,2]`、`N日内活跃→[0,N]`、`N周内活跃→[0,7N]`、`N月内活跃→[0,30N]`、`半年前活跃→[180,null]`、`N月前活跃→[30N,null]`、`N年前活跃→[365N,null]`、其他→`known=false`。
- 智联：`age = (now_ms - last_online_ms) / 86400000`，区间退化为精确点 `[age, age]`。
- 校验规则：`known=true` 时必须有有效区间（lower ≤ upper 或 upper 为 null）；解析异常一律产出 `known=false`，绝不抛错中断筛选。

## 实体 2：第 7 类筛选条件（recruiter_activity 档位）

随筛选运行冻结的档位选择（`frozen_filters["recruiter_activity"]`）。

| 档位稳定码 | label | 天数阈值 |
| --- | --- | --- |
| `week` | 近一周 | 7 |
| `month` | 近一个月 | 30 |
| `quarter` | 近三个月 | 90 |
| `half_year` | 近半年 | 180 |

- 单选（`FilterField.multiple=False`，选新替换旧，可取消）；不出现该键 = 不限。
- schema 版本递增：`BOSS_FILTER_SCHEMA_VERSION 1→2`、`ZHILIAN_FILTER_SCHEMA_VERSION 2→3`。

## 判定规则（状态/输出迁移）

```
输入: activity(招聘者活跃事实) + threshold(所选档位天数)
known=false 或 activity 缺失  → 输出 None（不拦截）+ verdict 附 caveat「招聘者活跃时间未知，未按第 7 类拦截」
age_lower_days > threshold    → 输出 not_match + reason「负责人上次活跃{人话距离}，超过要求的{档位label}」
否则（含区间跨档位的不确定态） → 输出 None（不拦截），无 caveat
```

- 人话距离格式化（确定下界 d 天）：`d < 14 →「N 天前」`；`d < 60 →「N 周前」`；`d < 365 →「N 个月前」`；`d ≥ 365 →「N 年前」`；Boss 下界型文本优先用原始文本形态（「半年前活跃」→「半年前」）。
- 判定发生在 JD 详情抓取后、AI 精筛判定前（`match_jds` 硬规则层）；粗筛不使用。

## 实体关系

- `jobs`（1）—（0..1）`recruiter_activity`：随详情抓取写入 extra_json；存量岗位无此键 = 未知。
- `screening_runs.frozen_filters_json`（1）—（0..1）档位：提交时冻结，续跑复用按全字典比对自动纳入。
- `screening_results`：not_match 行 verdict_reason 承载判定说明；caveat 进 caveats_json / verdict 列内 dict。
