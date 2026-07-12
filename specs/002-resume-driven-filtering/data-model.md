# 数据模型

所有时间使用 UTC ISO 8601 字符串。001 已有的 profiles、candidate_profiles、resumes、ai_settings、search_runs、run_queries、jobs、profile_jobs、feedback_events、preference_versions 表全部保留，迁移不得删除或改写其历史记录。本功能新增 screening_runs、screening_results 两表，并复用 jobs、profile_jobs、feedback_events 实现区域生命周期。

## 迁移

schema_migrations 保存递增版本、应用时间和说明。本功能迁移新增 screening_runs 与 screening_results，保留 001 已有全部表与历史数据，不把旧搜索运行伪装为筛选执行运行。

## 实体

### screening_runs

字段：id、frozen_filters_json、status、source_count、match_count、mismatch_count、created_at、updated_at、error_code。

frozen_filters_json 是用户确认后冻结的筛选条件快照，核验时以此为准。status 为 queued、running、succeeded、partial、failed、interrupted。source_count 为第一层抓回进入第二层的职位数；match_count 与 mismatch_count 为分流后符合区与不符合区计数。一次筛选执行对应一个 screening_runs 记录。

### screening_results

字段：id、run_id、job_id、verdict、created_at。

verdict 为 match 或 mismatch，表示该职位在本次执行中进符合区还是不符合区。**不存储核验明细或排除原因**——符合 spec 要求"不符合区不区分是被硬规则还是 AI 排除、不标注"。同一 run_id 下每个 job_id 只有一条结果。AI 语义相似度的中间判定（维度分数、原始输出）本次不存储，待框架设计时再定是否落库。

### jobs（复用 001）

第一层抓回的职位写入 001 已有的 jobs 表，沿用 canonical_url、source_url、title、company、salary、location、jd 等字段与安全链接约束。不新增职位表。

### profile_jobs（复用 001，扩展状态语义）

复用 001 的 profile_jobs 表实现持久化区域：

- 感兴趣区：status = interested。
- 垃圾桶区：status = deleted。

状态字段沿用 001 的 new、interested、applied、deleted 枚举，不新增枚举值。interested 与 deleted 在本功能中分别承载"持久感兴趣区"与"持久垃圾桶区"语义。

**待定项**：spec 假设换简历时感兴趣与垃圾桶跨简历持久保留。001 的 profile_jobs 绑 profile_id。两种方案待定：感兴趣与垃圾桶改为用户级（不绑画像），或所有画像共享。此方案暂定，可在实现时定，不阻塞数据模型其余部分。

### feedback_events（复用 001）

不感兴趣标记同时写入 001 的 feedback_events，action = not_interested，用于展示阶段排除的具体岗位识别。感兴趣标记可写入 action = interested 的反馈记录。复用 001 的撤销机制（revoked_at）。

## AI 语义相似度中间数据

本次不实现框架，不存储 AI 语义相似度的中间判定数据（维度分数、原始输出、留删线判定）。占位实现恒返回"过"，不产生可持久化的中间数据。框架设计另做，届时再定是否新增表或字段存储 AI 结构化输出与匹配分。

## 删除与保留

- 符合区、不符合区：绑定 screening_runs，为本次执行临时区域。开始下一次执行时不删除旧 screening_results 记录（保留为历史），但当前视图只展示最新 run 的结果；旧 run 可作为历史回看，不阻塞新 run。具体清理策略（如保留最近 N 次执行）可在实现时定。
- 感兴趣区、垃圾桶区：持久保留，不受新执行影响。沿用 001 的 30 天清理边界——感兴趣与垃圾桶记录不受普通结果清理影响。
- 简历删除：沿用 001 的原子删除（源文件、文本、哈希、文件名、未确认建议），不影响已持久化的感兴趣与垃圾桶记录。
