# 本地 HTTP 接口契约

所有接口只绑定本机。会话初始化之外，读取简历、写入、删除和状态变更都要求 X-Boss-Token；变更请求保留本地 Host 与 Origin 检查。

## AI 设置

- GET /api/ai-settings：返回 endpoint_url、status、last_error_code、updated_at 与 is_configured；不得返回 API Key 或凭据引用。
- PUT /api/ai-settings：接收 endpoint_url 与 api_key；Key 写入系统凭据库，响应不回显。
- POST /api/ai-settings/test：测试保存端点；只返回安全错误分类。

## 画像与简历

- POST /api/profiles：创建画像，接收 name、confirmed_fields 和可选复制人工条件来源。
- GET /api/profiles：返回画像摘要，不含简历文本。
- GET 或 PATCH /api/profiles/{profile_id}：读取或更新名称与确认字段；人工字段覆盖 AI 建议。
- POST /api/profiles/{profile_id}/resume：上传文件，返回 resume_id、可审核建议与隐私告知状态，不自动开始搜索。
- DELETE /api/profiles/{profile_id}/resume：删除当前简历及受删除约束的数据。

## 搜索与卡片流

- POST /api/search-runs：接收 profile_id 与可选 manual_keyword、manual_filters；创建后台父运行。
- GET /api/search-runs/{run_id}：返回状态、当前关键词、计数、错误分类和自 after_event_id 起的增量事件。
- POST /api/search-runs/{run_id}/cancel：请求取消，不删除已持久化岗位。
- GET /api/search-runs/{run_id}/jobs：支持 after_job_id 和 sort=relevance、latest、salary；返回 job_id、title、company、salary、location、jd_excerpt、canonical_url、interest_state，不返回 AI 分数与理由。

## 反馈与历史

- POST /api/jobs/{job_id}/feedback：接收 profile_id、action 和可选 reason，返回 interest_state 与 feedback_id。
- POST /api/feedback/{feedback_id}/revoke：撤销可撤销反馈。
- PATCH /api/profile-jobs/{profile_id}/{job_id}：只允许 status、note、applied_at。
- GET /api/profile-jobs：按 profile_id、status、run_id 查询收藏、投递或历史岗位。

错误对象仅包含 error_code 与 user_message。不得包含 Key、简历正文、完整模型请求、模型原始响应或未校验 URL。

