# 数据模型

所有时间使用 UTC ISO 8601 字符串。现有 profiles、tasks 与 task_logs 表保留，迁移不得删除或改写其历史记录。

## 迁移

schema_migrations 保存递增版本、应用时间和说明。首个工作台迁移复制旧 default profile 为命名候选画像，保留旧任务和日志，不把它们伪装为新搜索运行。

## 实体

### candidate_profiles

字段：id、name、confirmed_fields_json、ai_preference_json、resume_id、created_at、updated_at。名称长度为 1 至 80；confirmed_fields 是用户确认的城市、岗位、技能与人工条件。

### resumes

字段：id、profile_id、storage_path、format、extracted_text、content_hash、created_at、deleted_at。storage_path 必须是简历专用目录内相对路径；格式仅 txt、pdf、docx；提取文本受长度限制且不能写入日志。

### ai_settings

字段：id、endpoint_url、credential_ref、status、last_error_code、updated_at。credential_ref 是系统凭据库引用而不是 Key；状态为 unconfigured、testing、ready、failed。

### search_runs

字段：id、profile_snapshot_json、mode、status、total_detail_budget、discovered_count、completed_jd_count、created_at、updated_at、error_code。预算固定 60；状态为 queued、running、succeeded、partial、failed、interrupted。

### run_queries

字段：id、run_id、ordinal、frozen_query_json、list_output_path、detail_output_path、status、detail_budget、counts、error_code。ordinal 为 0 至 2；所有 detail_budget 之和不得超过 60；产物路径必须在结果目录内并含运行和查询 ID。

### jobs

字段：id、canonical_url、source_url、title、company、salary、location、jd、first_seen_at、last_seen_at、expires_at。规范链接只允许 HTTPS 与预期 BOSS 域名；普通结果默认 30 天过期。

### profile_jobs

字段：profile_id、job_id、first_run_id、last_run_id、ai_rank、shown_at、status、note、applied_at。状态为 new、interested、applied、deleted。该表实现同一岗位在不同画像中的独立状态。

### feedback_events

字段：id、profile_id、job_id、run_id、action、reason、revoked_at、created_at。action 为 interested 或 not_interested；reason 可空或为 role、salary、location、company。

### preference_versions

字段：id、profile_id、source_feedback_count、preference_json、created_at。只在至少 5 条有效反馈后创建。

## 删除与保留

删除简历时删除源文件、文本、哈希、文件名和未确认建议。历史运行只保留用户确认字段与不标识用户的已删除时间标记。普通结果清理前必须解析路径并确认在结果目录内；不得触及简历目录。感兴趣和已投递记录不受 30 天清理影响。

