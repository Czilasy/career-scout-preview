# 简历驱动的两层筛选：开发任务

功能：简历驱动的两层筛选
输入：spec.md、plan.md
测试策略：spec 要求自动化与浏览器验证，每个用户故事均含测试任务。
任务格式：所有任务遵循复选框、ID、并行标记、用户故事标记和精确文件路径规则。

## 依赖关系

基础设施 → US1 简历读取与自动填筛 → US2 第一层 API 搜索 → US3 第二层核验与分区 → US4 感兴趣与垃圾桶持久化 → US5 AI 不可用降级 → US6 前端两区切换 → 收尾验证。

US3 的 AI 语义相似度只留接口与占位，不依赖框架设计。US5 降级路径依赖 US1 的 AI 填筛与 US3 的核验就绪。US6 前端依赖 US1-US5 的后端接口。

## 第一阶段：准备

- [x] T001 记录筛选功能新增依赖、状态目录与测试命令到 requirements.txt、pyproject.toml、README.md
- [x] T002 [P] 建立筛选功能测试夹具与临时状态目录辅助函数到 tests/test_screening_fixtures.py
- [x] T003 [P] 添加简历、AI 填筛响应、岗位列表与 JD 的无敏感测试样本索引到 tests/fixtures/README.md

## 第二阶段：基础设施

目标：为筛选执行运行、结果分流与持久化区域建立数据基础。

独立验收：版本化迁移可跑，旧表与 001 已有数据保留，筛选运行与结果表可读写，安全链接与令牌保护沿用 001。

- [x] T004 为版本化 SQLite 迁移新增 screening_runs、screening_results 表编写失败测试到 tests/test_webui_store.py
- [x] T005 在 webui/store.py 实现 schema_migrations 新增 screening_runs、screening_results，保留 001 已有表与历史
- [x] T006 [P] 为筛选执行运行状态机（queued/running/succeeded/partial/failed/interrupted）编写测试到 tests/test_screening.py
- [x] T007 [P] 为筛选条件快照冻结与字段无强制必填编写测试到 tests/test_screening.py
- [x] T008 在 webui/screening.py 实现执行运行状态机、条件快照与字段无必填规则
- [x] T009 在 webui/app.py 扩展令牌保护到筛选相关接口，沿用 001 的 X-Boss-Token 机制
- [x] T010 运行基础设施测试并修复范围内失败：tests/test_webui_store.py、tests/test_screening.py

## 第三阶段：US1 简历读取与自动填筛

目标：用户上传简历后，AI 读取简历并给出筛选栏建议值，用户可改可不动，确认值优先。

独立验收：在模拟 AI 下，用户上传 TXT 简历后拿到筛选项建议值，修改某字段后执行采用修改值，未改字段采用建议值，AI 无法提取且用户未填的字段留空。

- [x] T011 [P] [US1] 为 BOSS 筛选选项枚举来源（薪资段、经验、学历、公司规模、融资阶段、行业、城市）编写测试到 tests/test_screening.py
- [x] T012 [P] [US1] 为 AI 读简历给筛选项建议的 JSON 解析、超时、拒绝与错误脱敏编写测试到 tests/test_ai.py
- [x] T013 [P] [US1] 为用户确认值优先、AI 不得覆盖、未改字段用建议值、空字段不参与编写测试到 tests/test_screening.py
- [x] T014 [US1] 在 webui/screening.py 实现筛选项枚举与字段合并规则（用户值优先、AI 值次之、空值跳过）
- [x] T015 [US1] 在 webui/ai.py 实现读简历给筛选项建议的提示词合同、请求超时、JSON 合同校验与安全错误分类
- [x] T016 [US1] 在 webui/app.py 实现上传简历、读简历填筛、返回建议值的接口契约，复用 001 的简历存储
- [x] T017 [US1] 在 tests/test_webui_app.py 增加令牌保护、Key 不回显、简历不进日志、建议值结构正确 的接口集成测试
- [x] T018 [US1] 运行 US1 测试并修复范围内失败：tests/test_ai.py、tests/test_screening.py、tests/test_webui_app.py

## 第四阶段：US2 第一层 API 搜索

目标：用户确认筛选条件并点击执行后，系统用确认条件调 BOSS 搜索 API 抓回一批职位，全部进入第二层。

独立验收：在模拟抓取下，执行运行用冻结条件调用抓取器，抓回职位全部进入第二层，城市留空时按全国搜索，运行状态正确推进。

- [x] T019 [P] [US2] 为确认条件到 BOSS 筛选参数映射（scale/salary/experience/degree/城市代码）编写测试到 tests/test_screening.py
- [x] T020 [P] [US2] 为执行运行调抓取器、产物路径、城市留空回退全国、状态推进编写测试到 tests/test_screening.py
- [x] T021 [US2] 在 webui/screening.py 实现确认条件到抓取参数映射与第一层搜索编排，复用 scripts/boss_cdp_raw.py
- [x] T022 [US2] 在 webui/app.py 实现确认执行接口，冻结条件、创建运行、调第一层、返回运行状态
- [x] T023 [US2] 在 tests/test_webui_app.py 编写执行接口、条件冻结、城市留空与产物校验的集成测试
- [x] T024 [US2] 运行 US2 测试并修复范围内失败：tests/test_screening.py、tests/test_webui_app.py

## 第五阶段：US3 第二层核验与分区

目标：对每条抓回职位做硬规则核验（AI 语义相似度本次留占位恒过），两条都过进符合区，任一不过进不符合区，不符合区不标原因。

独立验收：在模拟抓取下，硬规则按用户确认字段核验，未选字段不核，符合区与不符合区正确分流，不符合区不区分原因，符合区先按抓回顺序排不使用相似度排序。

- [x] T025 [P] [US3] 为硬规则核验（用户选了什么核什么、没选不核、城市在内无强制必填）编写测试到 tests/test_screening.py
- [x] T026 [P] [US3] 为 AI 语义相似度占位（恒返回过、不调 AI、留接口契约）编写测试到 tests/test_ai.py
- [x] T027 [P] [US3] 为两条核验分流（都过进符合、任一不过进不符合、不标原因）编写测试到 tests/test_screening.py
- [x] T028 [P] [US3] 为区域生命周期（本次执行临时、下次执行清空符合与不符合）编写测试到 tests/test_webui_store.py
- [x] T029 [P] [US3] 为符合区按抓回顺序排列、不使用相似度排序编写测试到 tests/test_screening.py
- [x] T030 [US3] 在 webui/screening.py 实现硬规则核验、分流与区域生命周期
- [x] T031 [US3] 在 webui/ai.py 定义 AI 语义相似度接口契约与占位实现（恒返回过），不实现框架
- [x] T032 [US3] 在 webui/store.py 实现 screening_results 持久化（verdict、核验明细不存原因）与区域清空
- [x] T033 [US3] 在 webui/app.py 实现查询符合区、不符合区接口，不返回排除原因
- [x] T034 [US3] 在 tests/test_webui_app.py 编写分流、区域清空、不标原因与排序的集成测试
- [x] T035 [US3] 运行 US3 测试并修复范围内失败：tests/test_screening.py、tests/test_ai.py、tests/test_webui_store.py、tests/test_webui_app.py

## 第六阶段：US4 感兴趣与垃圾桶持久化

目标：用户在符合区或不符合区任意岗位标记感兴趣或不感兴趣，感兴趣进持久区可跳转，不感兴趣进持久垃圾桶并在后续展示时按具体岗位排除。

独立验收：感兴趣与垃圾桶持久保留，区域清空不影响它们，垃圾桶岗位在后续执行展示时被排除且仅按具体岗位排除不扩展，感兴趣区卡片可点击跳转 BOSS 且仅 HTTPS 预期域名。

- [x] T036 [P] [US4] 为感兴趣/不感兴趣持久化、复用 profile_jobs 状态与 feedback_events 编写测试到 tests/test_webui_store.py
- [x] T037 [P] [US4] 为展示阶段排除垃圾桶具体岗位、不扩展到同公司或相似岗位编写测试到 tests/test_screening.py
- [x] T038 [P] [US4] 为感兴趣区链接校验（仅 HTTPS 且预期 BOSS 域名）编写测试到 tests/test_screening.py
- [x] T039 [P] [US4] 为换简历时感兴趣与垃圾桶跨简历持久保留（暂定方案）编写测试到 tests/test_webui_store.py
- [x] T040 [US4] 在 webui/store.py 实现感兴趣/垃圾桶持久化，复用 profile_jobs 状态与 feedback_events
- [x] T041 [US4] 在 webui/screening.py 实现展示阶段排除逻辑（仅具体岗位，不扩展）
- [x] T042 [US4] 在 webui/app.py 实现感兴趣、不感兴趣、感兴趣区列表、垃圾桶区列表接口，链接经 normalize_job_link 校验
- [x] T043 [US4] 在 tests/test_webui_app.py 编写反馈、展示排除、持久保留与链接安全的集成测试
- [x] T044 [US4] 运行 US4 测试并修复范围内失败：tests/test_webui_store.py、tests/test_screening.py、tests/test_webui_app.py

## 第七阶段：US5 AI 不可用降级

目标：AI 服务不可用时，系统提示并退化为人工填筛 + 脚本抓取，第二层仅硬规则核验，上传简历可跳过。

独立验收：AI 不可用时提示用户，上传简历可跳过，筛选栏退化为人工填写，第一层正常抓取，第二层仅硬规则，运行状态正确。

- [x] T045 [P] [US5] 为 AI 不可用检测与提示编写测试到 tests/test_ai.py
- [x] T046 [P] [US5] 为降级路径（人工填筛、跳过简历、仅硬规则核验、不调语义相似度）编写测试到 tests/test_screening.py
- [x] T047 [US5] 在 webui/ai.py 实现 AI 可用性检测与安全错误返回
- [x] T048 [US5] 在 webui/screening.py 实现降级编排：跳过 AI 填筛与语义相似度，仅走人工条件 + 硬规则
- [x] T049 [US5] 在 webui/app.py 实现降级接口：允许跳过简历、人工填筛、仅硬规则核验
- [x] T050 [US5] 在 tests/test_webui_app.py 编写 AI 不可用降级端到端集成测试
- [x] T051 [US5] 运行 US5 测试并修复范围内失败：tests/test_ai.py、tests/test_screening.py、tests/test_webui_app.py

## 第八阶段：US6 前端两区切换与卡片交互

目标：用户在桌面上传简历、看建议值、确认执行、切换符合/不符合区、看岗位卡片、标记感兴趣/不感兴趣、回看感兴趣区与垃圾桶区。

独立验收：筛选栏展示建议值可改，两按钮切换两区，卡片带感兴趣/不感兴趣按钮且不触发跳转，感兴趣区卡片跳转 BOSS 且仅 HTTPS 预期域名，垃圾桶区可查看，AI 不可用时上传简历可跳过且筛选栏退化人工填写。

- [x] T052 [P] [US6] 为筛选页 DOM 契约（筛选栏、执行按钮、两区切换、卡片、反馈按钮、感兴趣区与垃圾桶区入口）编写测试到 tests/test_webui_app.py
- [x] T053 [P] [US6] 为浏览器卡片跳转、按钮阻止跳转、两区切换、垃圾桶查看、降级态展示编写测试到 tests/test_webui_browser.py
- [x] T054 [US6] 在 webui/index.html 实现筛选栏、建议值展示与可编辑、执行按钮与运行状态
- [x] T055 [US6] 在 webui/index.html 实现符合/不符合两区切换、岗位卡片、感兴趣/不感兴趣按钮
- [x] T056 [US6] 在 webui/index.html 实现感兴趣区与垃圾桶区入口、安全跳转与垃圾桶列表
- [x] T057 [US6] 在 webui/index.html 实现 AI 不可用降级态：跳过简历、人工填筛、仅硬规则提示
- [x] T058 [US6] 在 tests/test_webui_app.py 和 tests/test_webui_browser.py 完成前端接口与交互测试
- [x] T059 [US6] 运行 US6 测试并修复范围内失败：tests/test_webui_app.py、tests/test_webui_browser.py

## 第九阶段：收尾与端到端验证

- [x] T060 [P] 更新 README.md，说明简历驱动筛选、两层核验、区域生命周期、感兴趣/垃圾桶、降级与不自动投递边界
- [x] T061 [P] 更新 README.en.md，使英文说明与 README.md 的功能边界一致
- [x] T062 运行完整自动化测试并将结果记录到 specs/002-resume-driven-filtering/validation.md：python -m unittest discover -s tests -v
- [x] T063 按 quickstart 执行本地浏览器验收并记录结果到 specs/002-resume-driven-filtering/validation.md
- [x] T064 在已登录专用 Chrome 上执行真实来源冒烟测试，记录成功或失败及可追踪日志到 specs/002-resume-driven-filtering/validation.md
- [x] T065 复查敏感数据、受控路径、展示排除边界与不自动投递边界，更新 specs/002-resume-driven-filtering/validation.md

## 并行机会

- T002 与 T003 可并行。
- T006、T007 可并行。
- US1 的 T011、T012、T013 可并行。
- US2 的 T019、T020 可并行。
- US3 的 T025、T026、T027、T028、T029 可并行。
- US4 的 T036、T037、T038、T039 可并行。
- US5 的 T045、T046 可并行。
- US6 的 T052、T053 可并行。
- T060 与 T061 可并行。

## 实施策略

先完成基础设施与 US1，得到"上传简历→AI 填筛→确认"的闭环。再加 US2、US3，得到"确认→搜索→硬规则核验→分流"的完整筛选闭环（AI 语义相似度占位恒过，不阻塞）。随后加 US4 的持久化与展示排除、US5 的降级路径，最后完成 US6 的前端体验。

第二层 AI 语义相似度框架不在本批任务内，T031 只留接口契约与占位；框架设计另做，接入时替换占位即可，不改动分流与区域逻辑。任何阶段都不得把单元测试或模拟抓取当作真实来源成功；真实来源验证只在用户专用 Chrome 已登录后执行。
