# Tasks: AI 判定链路修复与画像事实收口（B063 + B062 + B058）

> 状态说明：本文件于实现后补录，用于如实收口 Spec 015 的任务边界与验收证据，不把事后记录伪装成实现前已存在的任务清单。

## File Boundaries

- 允许修改：`webui/ai.py`、`webui/ai_retry.py`、`webui/ai_raw_log.py`、`webui/profile_facts.py`、`webui/ai_prompts.py`、`webui/prompt_texts.py`、`webui/src/components/JobWorkspace.vue`、`webui/src/styles.css` 及对应测试/构建产物。
- 禁止修改：`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/*`（Spec 015 原边界）。本次 Chrome CLI 修复是独立阻断项，不属于 Spec 015 用户故事。
- 引用方向：`ai.py -> profile_facts.py / ai_prompts.py / ai_retry.py / ai_raw_log.py`；前端组件只通过 CSS 类名接入样式。

## Phase 1: B063 空响应重试（P1）

**独立验收**：空响应首次失败后按默认策略重试；成功响应返回 JSON；连续空响应最终带 `failure_phase=empty_response`；JSON 垃圾不重试；日志带操作类型。

- [x] T001 [US1] 在 `tests/test_ai.py` 覆盖 HTTP 200 空响应重试成功、耗尽和原始响应记录。
- [x] T002 [US1] 在 `tests/test_ai_retry.py` 配置 `invalid_response` 默认最多 2 次重试和退避策略。
- [x] T003 [US1] 在 `webui/ai_retry.py` 接入空响应错误码策略，并保持 JSON 解析错误不重试。
- [x] T004 [US1] 在 `webui/ai.py` 将空流式内容标记为 `empty_response` 并接入统一重试循环。
- [x] T005 [US1] 在 `webui/ai_raw_log.py` 和 `webui/ai.py` 记录 `operation`、`correlation_id`、`attempt_index`。

## Phase 2: B062 画像事实与宽松匹配（P1）

**独立验收**：画像事实新字段合法；提示词含字段填写说明书且无第四层硬编码默认偏好；未确认软性维度进入匹配并写 caveats；硬条件和高危风控仍强制不匹配。

- [x] T006 [P] [US2] 在 `tests/test_profile_facts.py` 覆盖 `degree_type`、`week_off`、`overtime`、`job_type` 默认和旧数据缺省。
- [x] T007 [P] [US2] 在 `tests/test_ai_prompts.py` 覆盖字段说明书和第四层移除。
- [x] T008 [US2] 在 `webui/profile_facts.py` 实现字段契约、宽松校验、默认接受度和描述生成。
- [x] T009 [US2] 在 `webui/prompt_texts.py` 与 `webui/ai_prompts.py` 拆分简历分析/精筛提示词。
- [x] T010 [US2] 在 `webui/ai.py` 接入新画像校验、画像描述和精筛提示词组装。

## Phase 3: B058 判定卡片双栏（P2）

**独立验收**：桌面端两个区域 50/50 并排且可换行；移动端恢复单列；标题、标签、颜色语义和数据结构不变。

- [x] T011 [US3] 在 `webui/src/components/JobWorkspace.vue` 增加 `verdict-pair` 容器。
- [x] T012 [US3] 在 `webui/src/styles.css` 增加桌面双栏、字号和移动端单列规则。
- [x] T013 [US3] 在 `webui/src/components/__tests__/JobWorkspace.spec.ts` 覆盖桌面/移动端布局契约。

## Phase 4: 验证与独立阻断项

- [x] T014 运行 Spec 聚焦后端测试、前端测试和 `npm run build`。
- [x] T015 运行后端全量测试并修复与本轮环境/入口相关的失败；Chrome CLI 三个失败已修复，后端全量仅剩卫生门禁。
- [ ] T016 通过仓库卫生检查，确保新增公开文件进入版本跟踪范围。
- [x] T017 [P] 修复 `scripts/boss_cdp_raw.py` 直接执行时的仓库根目录导入 bootstrap，确保 `--help`、`--copy-login-state` 和会话导入授权门禁先于运行时依赖检查。

## Dependencies and Verification

- US1 与 US2 可在基础模块存在后并行；US3 独立于后端。
- T015/T016 是 Spec 015 的最终收口门禁；T017 是独立 Chrome CLI 阻断项，但影响全量测试。
- 聚焦：`uv run python -m unittest tests.test_ai tests.test_ai_retry tests.test_ai_prompts tests.test_profile_facts tests.test_tuning`。
- 前端：`npm test`、`npm run build`（工作目录 `webui`）。
- 全量：`uv run python -m unittest discover tests`。
- 卫生：`uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`。

## Implementation Record

实现前未生成本文件；当前勾选项表示代码已经存在并经对应检查证明，不表示原始开发顺序合规。未勾选项保留为当前真实未完成门禁。
