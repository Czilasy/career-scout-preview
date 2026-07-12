# AI 求职工作台：开发任务

功能：AI 求职工作台  
输入：spec.md、plan.md、research.md、data-model.md、contracts/http-api.md、quickstart.md  
测试策略：功能规格明确要求自动化与浏览器验证，因此每个用户故事均包含测试任务。  
任务格式：所有任务均遵循复选框、ID、并行标记、用户故事标记和精确文件路径规则。

## 依赖关系

基础设施 → US1 画像、简历与 AI 设置 → US2 后台搜索与岗位卡片流 → US3 兴趣反馈与学习、US4 历史与多画像、US5 响应式工作台体验 → 收尾验证。

US3、US4 与 US5 在 US2 完成后可并行推进；US3 的 AI 偏好更新依赖 US1 的 AI 适配器。

## 第一阶段：准备

- [ ] T001 记录工作台新增依赖、状态目录和测试命令到 requirements.txt、pyproject.toml、README.md
- [ ] T002 [P] 建立工作台测试夹具和临时状态目录辅助函数到 tests/test_workbench_fixtures.py
- [ ] T003 [P] 添加简历、AI 响应、岗位列表与 JD 的无敏感测试样本索引到 tests/fixtures/README.md

## 第二阶段：基础设施

- [ ] T004 为版本化 SQLite 迁移编写失败测试到 tests/test_webui_store.py
- [ ] T005 在 webui/store.py 实现 schema_migrations 和旧 profiles、tasks、task_logs 的保留迁移
- [ ] T006 为任务状态与受控产物路径编写测试到 tests/test_webui_runner.py
- [ ] T007 在 webui/app.py 和 webui/store.py 定义父搜索运行、子查询与完整状态转换
- [ ] T008 [P] 为安全 BOSS 链接规范化编写测试到 tests/test_workbench.py
- [ ] T009 在 webui/workbench.py 实现 HTTPS、预期域名、规范链接与岗位身份校验
- [ ] T010 在 webui/app.py 扩展本地会话保护，使简历读取与 AI 设置读取也要求 X-Boss-Token
- [ ] T011 运行基础设施测试并修复范围内失败：tests/test_webui_store.py、tests/test_webui_runner.py、tests/test_workbench.py

## 第三阶段：US1 画像、简历与 AI 设置

目标：用户可创建独立画像、上传并审核简历、保存 URL 与 Key、获得可编辑 AI 建议。

独立验收：用户在不泄露 Key 或简历正文的前提下创建画像、上传 TXT 简历、保存可测试 AI 设置，并能审核 AI 返回的城市、岗位和技能。

- [ ] T012 [P] [US1] 为多画像创建、复制人工条件和隔离反馈编写测试到 tests/test_webui_store.py
- [ ] T013 [P] [US1] 为 TXT、PDF、DOCX 的文件类型、大小、文本限制与删除行为编写测试到 tests/test_resume.py
- [ ] T014 [P] [US1] 为 AI JSON 解析、超时、拒绝和错误脱敏编写测试到 tests/test_ai.py
- [ ] T015 [US1] 在 webui/store.py 实现 candidate_profiles、resumes、ai_settings 与 preference_versions 仓储
- [ ] T016 [US1] 在 webui/resume.py 实现受限简历存储、提取、路径校验和原子删除
- [ ] T017 [US1] 在 webui/ai.py 实现 URL 连接测试、凭据库引用、请求超时、JSON 合同校验和安全错误分类
- [ ] T018 [US1] 在 webui/workbench.py 实现画像确认字段、AI 简历建议和人工字段覆盖规则
- [ ] T019 [US1] 在 webui/app.py 实现 AI 设置、画像和简历的接口契约
- [ ] T020 [US1] 在 tests/test_webui_app.py 增加令牌保护、Key 不回显、简历不进日志和删除后不可读取的 API 集成测试
- [ ] T021 [US1] 运行 US1 测试并修复范围内失败：tests/test_resume.py、tests/test_ai.py、tests/test_webui_store.py、tests/test_webui_app.py

## 第四阶段：US2 后台搜索与岗位大卡片流

目标：用户点击搜索后，后台自动执行最多 3 组关键词，最多获取 60 条完整 JD，并以无评分的大卡片流持续显示。

独立验收：在模拟抓取和模拟 AI 下，任务可恢复，跨关键词岗位去重，卡片只在 JD 完成后出现，父运行不会超出 60 条详情预算。

- [ ] T022 [P] [US2] 为关键词生成、城市缺失、人工关键词回退和最多三查询编写测试到 tests/test_workbench.py
- [ ] T023 [P] [US2] 为父子运行成功、部分成功、失败、取消、空结果和重试快照编写测试到 tests/test_webui_runner.py
- [ ] T024 [P] [US2] 为 AI 分批 JD 排序、未输入 job_id 拒绝和不向卡片暴露理由编写测试到 tests/test_ai.py
- [ ] T025 [US2] 在 webui/workbench.py 实现关键词选择、跨查询去重、60 条详情预算和卡片投影
- [ ] T026 [US2] 在 webui/ai.py 实现 JD 每批最多 10 条的排序合同与应用端校验
- [ ] T027 [US2] 在 webui/app.py 扩展 TaskRunner 以执行父搜索运行、独立子查询输出、max-details 预算、增量事件和任务产物校验
- [ ] T028 [US2] 在 webui/store.py 实现 search_runs、run_queries、jobs 和 profile_jobs 的持久化与流式查询
- [ ] T029 [US2] 在 webui/app.py 实现搜索运行、取消、运行事件和增量岗位卡片接口
- [ ] T030 [US2] 在 tests/test_webui_app.py 编写搜索接口、无 AI 人工回退、隐藏 AI 分数和安全链接的集成测试
- [ ] T031 [US2] 运行 US2 测试并修复范围内失败：tests/test_webui_runner.py、tests/test_workbench.py、tests/test_ai.py、tests/test_webui_app.py

## 第五阶段：US3 兴趣反馈与偏好学习

目标：用户可在不跳转卡片的情况下标记兴趣或不感兴趣、撤销错误反馈，并每五条反馈更新当前画像偏好。

独立验收：不感兴趣只影响当前画像；撤销完全恢复；五条有效反馈后只影响未来结果，不能让已展示卡片重排。

- [ ] T032 [P] [US3] 为感兴趣、不感兴趣、原因枚举、撤销和画像隔离编写测试到 tests/test_workbench.py
- [ ] T033 [P] [US3] 为五条反馈触发偏好更新、人工条件不被 AI 覆盖编写测试到 tests/test_ai.py
- [ ] T034 [US3] 在 webui/store.py 实现 feedback_events、反馈撤销和按画像读取有效反馈
- [ ] T035 [US3] 在 webui/workbench.py 实现兴趣状态、反馈聚合和未来岗位排序规则
- [ ] T036 [US3] 在 webui/ai.py 实现偏好更新提示词合同和结果校验
- [ ] T037 [US3] 在 webui/app.py 实现反馈、撤销和偏好更新接口
- [ ] T038 [US3] 在 tests/test_webui_app.py 编写反馈接口、撤销、无跨画像影响和无外部投递路由测试
- [ ] T039 [US3] 运行 US3 测试并修复范围内失败：tests/test_workbench.py、tests/test_ai.py、tests/test_webui_app.py

## 第六阶段：US4 历史、收藏、多画像与清理

目标：用户可查看本次结果、收藏、已投递和历史岗位；换简历创建独立画像；普通结果在 30 天后清理。

独立验收：收藏和已投递岗位不被清理；新画像不继承旧画像负向偏好；清理不触及简历目录或不受控路径。

- [ ] T040 [P] [US4] 为本次结果、收藏、已投递、历史和画像切换编写测试到 tests/test_webui_store.py
- [ ] T041 [P] [US4] 为 30 天清理、路径边界和保留收藏岗位编写测试到 tests/test_webui_store.py
- [ ] T042 [US4] 在 webui/store.py 实现 profile_jobs 查询、已投递状态、历史筛选和安全清理
- [ ] T043 [US4] 在 webui/app.py 实现收藏、已投递、历史和画像切换接口
- [ ] T044 [US4] 在 tests/test_webui_app.py 编写历史查询、状态更新和清理预览的集成测试
- [ ] T045 [US4] 运行 US4 测试并修复范围内失败：tests/test_webui_store.py、tests/test_webui_app.py

## 第七阶段：US5 前端求职工作台与响应式卡片流

目标：用户在桌面与窄屏上清晰地配置画像、观察搜索、阅读 JD 大卡片并提交反馈。

独立验收：卡片固定高度且 JD 省略；阅读区跳转 BOSS；反馈按钮不跳转；搜索进度恢复；不感兴趣动画与撤销可用。

- [ ] T046 [P] [US5] 为工作台 DOM 契约、状态栏、设置抽屉、历史入口和卡片字段编写测试到 tests/test_webui_app.py
- [ ] T047 [P] [US5] 为浏览器卡片跳转、按钮阻止跳转、JD 截断、动画和窄屏操作编写测试到 tests/test_webui_browser.py
- [ ] T048 [US5] 在 webui/index.html 实现深色工作台布局、画像和 AI 设置面板、搜索状态栏与历史入口
- [ ] T049 [US5] 在 webui/index.html 实现固定高度岗位大卡片、JD 截断、安全跳转、收藏和不感兴趣原因选择
- [ ] T050 [US5] 在 webui/index.html 实现增量运行轮询、卡片入场、不感兴趣退场、撤销和不重排已展示卡片
- [ ] T051 [US5] 在 webui/index.html 实现窄屏设置抽屉、单列阅读流和触控可达性
- [ ] T052 [US5] 在 tests/test_webui_app.py 和 tests/test_webui_browser.py 完成前端接口与交互测试
- [ ] T053 [US5] 运行 US5 测试并修复范围内失败：tests/test_webui_app.py、tests/test_webui_browser.py

## 第八阶段：收尾与端到端验证

- [ ] T054 [P] 更新 README.md，说明 AI URL 与 Key、隐私告知、画像隔离、卡片交互、反馈学习和不自动投递边界
- [ ] T055 [P] 更新 README.en.md，使英文说明与 README.md 的功能边界一致
- [ ] T056 运行完整自动化测试并将结果记录到 specs/001-ai-job-workbench/validation.md：python -m unittest discover -s tests -v
- [ ] T057 按 specs/001-ai-job-workbench/quickstart.md 执行本地浏览器验收并记录结果到 specs/001-ai-job-workbench/validation.md
- [ ] T058 在已登录专用 Chrome 上执行真实来源冒烟测试，记录成功或失败及可追踪日志到 specs/001-ai-job-workbench/validation.md
- [ ] T059 复查敏感数据、受控路径和无自动投递边界，更新 specs/001-ai-job-workbench/validation.md

## 并行机会

- T002 与 T003 可并行。
- T008 与 T006 可并行。
- US1 的 T012、T013、T014 可并行。
- US2 的 T022、T023、T024 可并行。
- US3 的 T032、T033 可并行。
- US4 的 T040、T041 可并行。
- US5 的 T046、T047 可并行。
- T054 与 T055 可并行。

## 实施策略

先完成基础设施、US1 和 US2，即可得到一个可独立验证的闭环：简历或人工关键词、后台搜索、JD 卡片流和安全链接打开。随后添加 US3 与 US4 的长期个性化和历史能力，最后完成 US5 的完整前端体验。任何阶段都不得把单元测试或模拟抓取当作真实来源成功；真实来源验证只在用户专用 Chrome 已登录后执行。
