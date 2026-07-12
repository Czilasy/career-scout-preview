# 本地 HTTP 接口契约

所有接口只绑定本机。沿用 001 的会话保护：读取简历、写入、删除和状态变更都要求 X-Boss-Token；变更请求保留本地 Host 与 Origin 检查。错误对象仅包含 error_code 与 user_message，不得包含 Key、简历正文、完整模型请求、模型原始响应或未校验 URL。

## 简历与自动填筛

- GET /api/screening/filter-options：返回 BOSS 搜索 API 支持的筛选选项枚举（薪资段、经验、学历、公司规模、融资阶段、行业、城市），供前端渲染筛选栏。
- POST /api/screening/resume：上传简历文件，返回 resume_id 与隐私告知状态，不返回简历正文。AI 不可用时此步可跳过。
- POST /api/screening/resume/suggest：读取简历并调用 AI 给出筛选项建议值；返回每个字段的建议值，不返回简历正文。AI 不可用时返回 ai_unavailable 状态，前端退化为人工填写。

## 筛选执行

- POST /api/screening/runs：接收用户确认后的筛选条件（filters 对象，字段均可空）；冻结条件快照，创建筛选执行运行，触发第一层搜索。返回 run_id 与初始状态。
- GET /api/screening/runs/{run_id}：返回运行状态（queued/running/succeeded/partial/failed/interrupted）、source_count、match_count、mismatch_count 与 error_code。
- POST /api/screening/runs/{run_id}/cancel：请求取消，不删除已分流结果。

## 两区查询

- GET /api/screening/runs/{run_id}/matches：返回本次执行符合区岗位列表，按抓回顺序排列。每条返回 job_id、title、company、salary、location、jd_excerpt、canonical_url、interest_state。垃圾桶里的具体岗位在此不展示（展示阶段排除）。
- GET /api/screening/runs/{run_id}/mismatches：返回本次执行不符合区岗位列表，混在一起，**不返回排除原因、不区分硬规则或 AI 排除**。字段同符合区。

## 反馈

- POST /api/screening/jobs/{job_id}/interest：标记感兴趣，写入持久感兴趣区。返回 interest_state。
- POST /api/screening/jobs/{job_id}/reject：标记不感兴趣，写入持久垃圾桶区，并记录用于后续展示排除。返回 reject_state。

符合区与不符合区的岗位都可被标记感兴趣或不感兴趣。

## 持久区

- GET /api/screening/interested：返回持久感兴趣区岗位列表，可长期回看。每条含 canonical_url，可点击跳转 BOSS 原始页面（仅 HTTPS 且预期 BOSS 域名）。
- GET /api/screening/trash：返回持久垃圾桶区岗位列表，可查看。

## AI 不可用降级

- AI 不可用时，GET /api/screening/resume/suggest 返回 ai_unavailable。
- POST /api/screening/runs 接受纯人工填写的 filters，不依赖 AI；运行只执行第一层搜索与第二层硬规则核验，不执行 AI 语义相似度。
- 前端根据 ai_unavailable 状态提示用户并可跳过上传简历。

## AI 语义相似度接口（占位，本次不实现）

契约预留：输入简历原文与职位 JD 全文，输出结构化结果（至少含 verdict: match/mismatch；若设计时给 match_score 则可用于未来排序）。占位实现恒返回 verdict: match，不调用 AI，不产生中间数据。框架设计另做，接入时替换占位，不改动分流与区域接口契约。
