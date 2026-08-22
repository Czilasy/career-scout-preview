# Tasks: 016-error-module-rework

生成于 2026-08-22。依据：plan.md / research.md / data-model.md / contracts/error-module-contracts.md / spec.md。
测试任务显式包含（Spec 验收标准要求回归样本与一致性测试）。

## Phase 1: Setup

- [x] T001 阅读本 Spec 全部设计产物（spec/plan/research/data-model/contracts/quickstart），确认行号快照与当前代码一致（`webui/source.py`、`scripts/boss_cdp_raw.py`、`webui/error_registry.py`、`webui/pipeline_exec.py`、`webui/app.py`、`webui/task_runners.py`）；不一致处以代码为准并回注 research.md。

## Phase 2: Foundational（注册表统一，阻塞所有故事）

- [x] T002 [P] 重构 `webui/error_registry.py`：删除 4 个重复 taxonomy 码（`captcha_required`/`login_expired`/`ip_risk_control`/`cdp_unavailable`）改为别名；新增 `source_account_restricted`（blocking/systemic）与 `source_status_unclear`（非阻断/independent）；`SYSTEMIC_BLOCK_CODES` 改为 `{c | blocking and impact=="systemic"}` 派生；新增 `ALIAS_TO_CODE` 并让 `resolve_code` 先正名后别名；`to_json()` 输出 aliases。
- [x] T003 [P] 更新 `tests/test_error_registry.py`：别名解析断言（4 个旧码 → 新码）、派生集合一致性断言、新码语义断言、未知码兜底不变。
- [x] T004 [P] 扩展 `scripts/boss_cdp_signals.py` 为 BOSS 信号单一来源：迁入 `RISK_CONTROL_KEYWORDS`/`DETAIL_RATE_LIMIT_KEYWORDS`（`boss_cdp_raw.py` 改 import 并删除本地定义）；新增 `emit_failure_line(code, hint)`（打印 `__CAREERSCOUT_FAILED__ code=<code> hint=<text>`）、`parse_failure_line(text) -> (code,hint)|None`、`classify_diagnosis_tier(diagnosis, *, consecutive_block=1) -> (code, hint)`（实锤分档：验证码页/限流页/code:31/HTTP 403 或 429 连续两次 → 实锤码；单次 403/412/418/429、code:37、空响应、结构异常 → `source_status_unclear`）。
- [x] T005 新增 `tests/test_risk_signal_tiers.py`：分档全样本（正常空/单次拦截复现/连续拦截/code:31/code:37/验证码页/限流页）、失败行 emit/parse 幂等与格式边界、岗位正文敏感词不影响分档。

## Phase 3: US1 硬阻断正确停止与误报治理（P1）

- [x] T006 [US1] `scripts/boss_cdp_raw.py` `probe_login_state_tri` 判定换序：401→not_logged_in；JSON 结构=已登录（`is_logged_in_search_response`）→ logged_in（先于关键词）；结构完整无工资→not_logged_in；其余文本仅高置信风控短语→restricted；解析失败→unknown。
- [x] T007 [US1] `scripts/boss_cdp_raw.py` 列表链路：HTTP 403/429 单次出现时原地重试本页一次（重新导航+再取），复现才判实锤退出；`check_list_risk` 改用 `classify_diagnosis_tier`；删除"大概率被风控限制（也可能是…没有职位）"文案与该定性路径；结构异常空页（parse_failed/unexpected_shape/js_exception/empty_response）原地重试一次，仍异常计入异常空页；连续异常空页达阈值 → 停止翻页并以 `__CAREERSCOUT_FAILED__ code=source_status_unclear` 退出；全部失败退出路径（退出码 1/2/3/10/11）统一打印失败行（code 取实锤码或 status_unclear）。
- [x] T008 [US1] `webui/source.py` `_classify_failed_code` 重写：优先 `parse_failure_line(captured)`；缺行兜底按退出码粗分（2/3/11 精确；10→`source_status_unclear`；1→高置信登录短语→`source_login_required` 否则 `source_unknown_error`）；删除 `_RATE_LIMIT_KEYWORDS` 全文扫描与 `_VERIFICATION_KEYWORDS` 主路径（高置信短语兜底保留）；`_exit_reason` 同步只取失败行 hint。
- [x] T009 [US1] `webui/source.py` preflight/recheck：探测 restricted（复探后仍受限）→ `source_account_restricted`（不再用 source_blocked 兜账号受限）；401/未登录路径不变；探测结果不再写 restricted 入缓存（见 T014 的两态收敛协同）。
- [x] T010 [US1] `webui/pipeline_exec.py`：`_HARD_STOP_CODES` 与 `_jd_hard_stop_codes` 改引用注册表派生集合（删除两处手工清单）；hard_stop 暂停文案来源统一注册表 `user_message`；`webui/task_runners.py` 删除 `_SCRAPE_BLOCK_PATTERNS`，`_classify_scrape_block` 只在 `hard_stop_code` 缺失时用失败行 hint 解析兜底。
- [x] T011 [US1] 更新受影响后端测试：`tests/test_source.py`（分类优先级/新码/无全文扫描）、`tests/test_boss_cdp_signals.py`、`tests/test_rate_limit_stop.py`、`tests/test_healthy_pipeline.py` 硬停断言、`tests/test_resume*.py` 阻断码断言。

## Phase 4: US2 软失败记录继续（P1）

- [x] T012 [US2] `webui/pipeline_exec.py` combo_failed 分支调用 `_notify_combo_issue({"kind":"combo_failed","failed_code":<统一码>,"reason":<可读原因≤200>,"ts":<ISO 毫秒>})`；全组合软失败零结果的中性哨兵文案保留并确认不再出现"风控/限流"字样。
- [x] T013 [US2] `webui/app.py` task-state 响应附带 `combo_issues`（复用 `store.list_task_events` 过滤 `combo_issue` 且 `kind=="combo_failed"`，最近 20 条倒序，元素含 `combo_key/code_text(注册表文案)/reason/ts`）；修改既有响应组装，不新增路由。
- [x] T014 [US2] `scripts/login_state_cache.py` 状态值域收敛：`LOGIN_STATE_STATES=("logged_in","not_logged_in","unknown")`；读取遗留 `restricted` 视为无缓存触发重探；`webui/source.py` BOSS/智联 preflight 缓存命中只认 logged_in/not_logged_in，restricted 信号当次失败不缓存；`tests/test_login_state_cache.py` 同步。
- [x] T015 [US2] 前端软失败展示：`webui/src/api.ts`/`types.ts` 增加 `combo_issues` 类型；`TaskProgress.vue`（或 DiscoveryView 任务区）展示失败组合摘要（组合名 + 注册表文案 + 时间），样式沿用现有信息条规范；`webui/src/components/__tests__/` 相应 spec。

## Phase 5: US3 错误码统一三域（P1）

- [x] T016 [US3] `scripts/zhilian_cdp_raw.py`：全部失败退出路径打印结构化失败行（信号→统一码映射：verification→`source_verification_required`、rate_limited→`source_rate_limited`、blocked→`source_blocked`、login_required→`source_login_required`、单次抖动类→`source_status_unclear`）；marker 词表不动。
- [x] T017 [US3] `webui/source.py` 智联三个 signal map 对齐统一码语义；`_STATE_TO_SIGNAL`/`_SIGNAL_TO_STATE` 随 T014 收敛；`webui/ai.py` 仅把 `AI_TAXONOMY_TARGETS` 中已删除的重复 taxonomy 码替换为统一码，重试策略零改动。
- [x] T018 [US3] `webui/app.py` `_check_resume_block` 与续跑校验的码集合/翻译表改用统一码+别名解析；`webui/task_runners.py` 暂停码写出统一码。
- [x] T019 [US3] 前端镜像同步：`webui/src/errorCodes.ts` 按新注册表重建（含 aliases 与新码文案）；`webui/src/__tests__/errorCodes.spec.ts` 断言镜像=注册表 `to_json()`；全库检索前端对已删 4 码的引用并清理。
- [x] T020 [US3] 更新 `tests/test_zhilian_risk_signal.py`、`tests/test_webui_app.py` 续跑阻断断言为统一码。

## Phase 6: US4 冷却删除（P2）

- [x] T021 [US4] 删除 `webui/cooldown.py`；`webui/app.py` 删除 `_submit_cooldown_guard` 调用与定义、`/api/cooldown/clear` 端点、env-check `cooldowns` 字段与组装、`COOLDOWN_PATH` 初始化、`_restricted_cache_detail` 中冷却依赖（保留纯缓存态提示）；`webui/source.py` 删除 `mark_cooldown` 引用；全库检索 `cooldown` 清零（测试与前端除外）。
- [x] T022 [US4] 前端删除冷却 UI：`EnvCheckDialog.vue` 冷却区、`DiscoveryView.vue` 解除冷却按钮与警告、`types.ts`/`api.ts` 冷却类型；更新 `EnvCheckDialog.spec.ts`、`discovery.spec.ts`、`DiscoveryView.spec.ts`。
- [x] T023 [US4] 删除 `tests/test_cooldown.py`、`tests/test_cooldown_api.py`；`tests/test_env_check.py`、`tests/test_webui_app.py`、`tests/test_webui_store.py`、`tests/test_execution_config.py`、`tests/test_tuning.py` 中冷却断言改写。

## Phase 7: US5+US6 缓存两态收尾与进度无跳变（P2）

- [x] T024 [US5] 验证 T014 后无任何路径写 restricted：`tests/test_source.py` 增加断言"探测 restricted 后 login-state.json 不出现 restricted"；`test_webui_app.py` env-check 缓存分支断言只剩 logged_in/not_logged_in/unknown。
- [x] T025 [US6] 后端断点首拍：`webui/app.py` 续跑/恢复路径首次 task-state 组装时以 checkpoint（completed_combos/JD completed_job_ids/AI 已判定量）推导 processed/overall，不等首个事件；暂停/恢复响应带同一断点进度。
- [x] T026 [US6] 前端断点起步：`DiscoveryView.vue` 恢复路径（继续、报错后继续、刷新挂载）进度初始化取 task-state 断点值，删除本地 0 重置；`TaskProgress.vue` 保持 overall_percent 权威；`DiscoveryView.spec.ts`/`TaskContinue.spec.ts` 增加"首拍=断点值"断言（列表/JD/AI 三线）。

## Phase 8: Polish 与门禁

- [x] T027 全库检索清理：`大概率被风控`、`_RATE_LIMIT_KEYWORDS`（webui 侧）、`_SCRAPE_BLOCK_PATTERNS`、`mark_cooldown`、`cooldown`（非测试/文档）、`login_expired|ip_risk_control|captcha_required|cdp_unavailable` 裸引用（应只剩别名表与历史兼容测试）；确认无中文文案直出英文码。
- [x] T028 文档同步：`README.md` 环境检查/账号状态描述去除冷却；`specs/016-error-module-rework/` 勾选任务；CHANGELOG 条目按仓库格式（修复/增加/优化简单列表）。
- [x] T029 全量后端：`uv run python -m unittest discover -s tests -v`（0 失败）。
- [x] T030 前端与构建：`cd webui && npm test -- --run`（0 失败）+ `npm run build`；dist 产物纳入提交。
- [x] T031 卫生门禁：`uv run python -m unittest tests.test_repo_hygiene` + `git diff --check` + `git status` 复核无意外文件；提交（Conventional Commits，作者邮箱 czyooutzilas@gmail.com）。

## Dependencies

```text
T001 → T002/T004（可并行） → T005/T003（可并行）
T002+T004 → T006/T007（US1 串行内序） → T008 → T009 → T010 → T011
T010 → T012 → T013；T014 依赖 T009；T015 依赖 T013
T004 → T016 → T017 → T018/T019（可并行） → T020
T002 → T021 → T022 → T023
T014 → T024；T013 → T025 → T026
全部 → T027 → T028 → T029 → T030 → T031
```

- 并行机会：T002‖T004；T003‖T005；T018‖T019；前后端任务在契约冻结后可并行。
- MVP：Phase 2 + Phase 3（US1）即可交付"误报消除+硬阻断正确"，US2-US6 为增量。

## Implementation Strategy

按 Phase 顺序增量交付：每完成一个 Phase 跑该域聚焦测试；Phase 8 统一全量门禁。
后端先行（契约由后端定义），前端在 T013/T019 契约稳定后并行。
