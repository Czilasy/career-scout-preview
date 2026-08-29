# Contract: 筛选 schema 与 API 增量（028）

## /api/filter-labels 响应增量

两平台 schema 均新增第 7 类字段（既有六类不变，字段顺序追加在最后）：

```json
{
  "key": "recruiter_activity",
  "label": "招聘者上次活跃",
  "multiple": false,
  "options": [
    {"value": "week", "label": "近一周"},
    {"value": "month", "label": "近一个月"},
    {"value": "quarter", "label": "近三个月"},
    {"value": "half_year", "label": "近半年"}
  ]
}
```

## schema 版本

- `BOSS_FILTER_SCHEMA_VERSION`: 1 → 2
- `ZHILIAN_FILTER_SCHEMA_VERSION`: 2 → 3
- 版本不匹配的续跑/重提请求按既有 `filter_schema_version_mismatch`（422）处理，不静默兼容。

## 取值校验（POST /api/ai-screen 的 screening_fields）

- `recruiter_activity` 值必须为四档稳定码之一或键缺省（缺省 = 不限）。
- 单选强制：数组长度 > 1 → `filter_validation_failed`（422）。
- 跨平台校验无差异：该字段属两平台公共字段。

## AI prompt 契约（负向约束）

- `recruiter_activity` MUST NOT 出现在粗筛/精筛任何 AI prompt 的条件描述中（`_build_criteria_description` 排除该键）。
- 精筛 system prompt 的「六类字段」文案保持不变。

## 续跑复用契约

- `find_resumable_screen_run` 按 frozen_filters 全字典相等比对：选中第 7 类的提交与旧 run 必然失配（不复用）；未选中时 frozen_filters 与旧形态一致，既有复用行为不变。无需修改该函数。
