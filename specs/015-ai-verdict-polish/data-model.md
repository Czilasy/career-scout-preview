# Data Model: Spec 015

## profile_facts

存储位置：`screening_runs` 的 JSON 快照；本功能不新增表或列。

- `core_skills`: 明确技能字符串数组，最多 10 个。
- `projects`: 对象数组；`name` 必填，`role`、`stack`、`summary` 可选。
- `job_type`: `全职`、`实习`、`兼职`、`未体现`。匹配未体现按不限雇佣形式处理。
- `degree`: 简历明确写出的最高学历层次，可缺省。
- `degree_type`: `统招` 或 `非统招`；仅明确非统招标志时为后者，匹配缺省按统招处理。
- `languages`: 明确外语等级/证书字符串数组，最多 10 个。
- `week_off`、`overtime`: 简历明确的主观偏好，可缺省；匹配缺省分别按单休、能够加班处理并提示确认。

## AI 调用日志

本地轮转日志记录 `correlation_id`、`attempt_index`、`operation`、`original_length`、`body` 和截断标记，不写入数据库。
