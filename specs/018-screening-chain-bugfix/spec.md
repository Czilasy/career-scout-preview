# Feature Specification: 筛选链路三处 Bug 修复（018）

**Feature Branch**: `018-screening-chain-bugfix`

**Created**: 2026-08-22

**Status**: Draft

**Input**: 需求已在上一会话完成 grill-me 边界质询并冻结（冻结核为自包含任务提示词），本 spec 按其整理。用户描述："Career Scout 筛选链路三处 Bug 修复（018）+ 事故数据清理：AI 响应 results 字段类型守卫；续跑幸存者语义反转 + 判定同源链合并 + 护栏事件；收尾顺序换位防幽灵历史轮；删除 live 库幽灵轮 828f8807。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - AI 返回坏格式不再炸掉整轮筛选 (Priority: P1)

我发起 AI 精筛，AI 端点某次返回了格式坏掉的响应（比如 results 字段不是数组而是一个数字）。这一批岗位不应让整个任务以 internal_error 崩掉，而应按"该批无结果"降级：走既有的单条重试预算，重试耗尽后标 uncertain 待人工确认，连续全空仍触发既有熔断。

**Why this priority**: 这是事故的三次 internal_error 中两次（TypeError: 'int' object is not iterable）的直接根因；不修它，任何一次端点格式抖动都会废掉整轮。

**Independent Test**: mock AI 端点返回 `{"results": 40}`，断言精筛调用不抛异常、相关岗位产出 uncertain 判定；对粗筛的同款解析做同样验证（存在同款写法才加守卫）。

**Acceptance Scenarios**:

1. **Given** 精筛批次调用返回 `{"results": <数字>}`，**When** 解析响应，**Then** 不抛 TypeError，该批按无结果降级进入 missing/重试/uncertain 链路。
2. **Given** 粗筛批次调用返回 `{"dropped": <数字>}`，**When** 解析响应，**Then** 同样不抛异常，该批按"默认全保留"处理。

---

### User Story 2 - 续跑不丢岗位：断点里有的岗位默认保留 (Priority: P1)

一次抓取跑了多轮筛选（失败→续跑→失败→续跑）。链上第一条 run 保存了全部粗筛判定，后面的 run 只保存了少量精筛判定。我再点"继续"时：断点（checkpoint）里出现的岗位，只有当判定链上**明确写着 dropped** 才被移除；判定记录缺失的岗位一律保留，不允许静默消失。精筛判定从同一抓取、同条件、同画像的全部历史 run 按时间从旧到新合并继承（新覆盖旧）。

**Why this priority**: 事故丢岗机理——277 条判定在链上第一条 run 名下，续跑只读最近一条 run 的 40 条判定，幸存者集合塌缩成 40 条。这是三处修复中对用户数据伤害最大的一处。

**Independent Test**: 事故链回归测试：run1 写 277 条粗筛判定（165 kept + 112 dropped）+ ai_rough checkpoint；run2 名下仅 40 条 not_match 精筛判定 + ai_fine checkpoint；模拟 run3 续跑，断言幸存者 165、40 条精筛判定被继承、无岗位静默消失。

**Acceptance Scenarios**:

1. **Given** 断点岗位集合减去现有判定键集非空（存在无判定记录的断点岗位；修订自 020——精筛判定计入总数后"判定数少于断点数"的数量比较会漏判，改为覆盖比较口径）且同源链上有历史判定，**When** 加载续跑判定，**Then** 按创建时间从旧到新合并同源链（同抓取、同冻结条件、同画像、同画像事实，排除自身），新的覆盖旧的。
2. **Given** 续跑场景下合并后断点内仍有岗位没有判定记录，**When** 续跑开始，**Then** 追加一条 resume_inconsistent 事件仅作记录（负载含缺失岗位数，修订自 020），不阻断流程、不加新错误码。
3. **Given** 断点中的岗位在判定链上没有 dropped 记录，**When** 计算粗筛幸存者，**Then** 该岗位被保留（旧数据纯字符串 verdict "kept"/"dropped" 与早期误写的 "match" 均兼容）。
4. **Given** 早期数据只有纯字符串 verdict（无 reason 等结构），**When** 合并判定，**Then** 兼容读取且语义不变。

---

### User Story 3 - 收尾校验失败不再留下幽灵历史轮 (Priority: P1)

筛选跑完收尾时，如果终态校验发现状态不合法（例如应判 paused），任务应失败并且**库里不留下任何 done 历史轮**。历史轮写入必须发生在终态校验通过之后；校验通过的正常完成路径行为不变（照常写轮、照常发事件）。

**Why this priority**: 事故第三次 internal_error（invalid_ai_terminal_status:paused）在失败前已写入一条 40 岗位的 done 历史轮（828f8807），刷新后污染结果页与历史抽屉，需要人工清库。

**Independent Test**: 构造 finalize 判 paused 的场景，断言不产生任何 done 轮记录、任务 failed(internal_error)；再跑正常完成路径，断言历史轮照常写入。

**Acceptance Scenarios**:

1. **Given** 精筛完成但计数对不上（终态校验判 paused），**When** 收尾，**Then** 抛错、任务 failed(internal_error)、历史列表零新增、无 done 轮记录。
2. **Given** 精筛正常完成，**When** 收尾，**Then** 计数落库 → done 事件 → 终态校验 → 写历史轮 → 历史快照事件 → 清理，一条不少。
3. **Given** 用户以"结束保存"收尾（partial），**When** 收尾，**Then** 与正常完成一样照常写轮。
4. **Given** 终态校验通过、succeeded 已落库后写历史轮失败（典型为瞬时数据库锁，修订自 020），**When** 收尾，**Then** 先做短退避重试；重试耗尽且该流程确无结果轮时条件降级为 failed 并提示「点继续可重试保存」，不静默吞掉；已有结果轮时保持 succeeded 落诊断事件。

---

### User Story 4 - 事故数据一次性清理 (Priority: P2)

代码修复合入后，live 库中那条 40 岗位的幽灵 done 轮（828f8807）被删除；三条失败 run（03fb82e1/0f0baa1b/94e2c440）及其判定、checkpoint 完整保留，用户之后可以继续续跑。

**Why this priority**: 幽灵轮不清，结果页与历史抽屉一直被污染；但它是数据清理动作，代码修复优先。

**Independent Test**: 删除前后各备份一次；删除后用 db_info 复核最新 run 已不是 828f8807，且三条失败 run 的判定行数不变。

**Acceptance Scenarios**:

1. **Given** 代码修复已合入，**When** 执行清理，**Then** 仅删除 828f8807 的 screening_results 与 screening_runs 行，其余任何行不动。
2. **Given** 清理完成，**When** 查询最新 run，**Then** 最新 run 不是 828f8807。

---

### Edge Cases

- AI 响应 `results`/`dropped` 字段为 null、字符串、布尔等其他非列表类型 → 一律按无结果/空名单降级，不抛异常。
- 同源链上存在条件或画像不一致的 run → 跳过该 run，不合入其判定。
- 同源链合并时当前 run 自身 → 排除，避免自己覆盖自己之外的重复读取开销与语义混乱。
- 断点存在但同源链上一个判定都没有（全新链）→ 行为与现状一致，只读 run 自身判定。
- 收尾校验抛错时 job_success/job_fail 事件已落库 → 保留（换序设计如此，事件先于终态是 017 的既有契约）；但历史轮绝不能先写。
- 正常完成路径的 `save_finished_round` status 参数保持 "done" 不变，"结束保存"路径的既有调用不受本次换序影响。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 精筛响应解析 MUST 对 `results` 字段做类型守卫：仅当值为列表时使用，否则按空列表处理，走既有 missing → 单条重试预算 → uncertain → 熔断链路。
- **FR-002**: 粗筛响应解析 MUST 对 `dropped` 字段加同款类型守卫（现状存在同款写法，故同样处理）。
- **FR-003**: 粗筛续跑幸存者计算 MUST 反转为"断点内默认保留、仅判定链上明确 dropped 才移除"；判定来源 MUST 能看到同源链合并后的完整判定（否则链上第一条 run 的 dropped 不可见会被误保留）。
- **FR-004**: 续跑判定加载的回退段 MUST 替换为同源链合并：取与当前 scrape_task_id 相同的全部非快照 run，逐 run 校验 frozen_filters、profile_summary、profile_facts 一致，排除当前 run，按 created_at 从旧到新合并 screening_results（新覆盖旧）；不保留旧的结果快照回退代码。合并触发条件为覆盖比较——断点岗位集合减判定键集非空即合并（修订自 020：数量比较在精筛判定计入总数时会漏判）。
- **FR-005**: store 的同源 run 查询方法 MUST 以向后兼容的可选参数原地扩展以支持"全部状态按创建时间升序"查询；不新增 store 方法。
- **FR-006**: 续跑开始时若合并后断点内仍有岗位没有判定记录（覆盖口径，修订自 020），MUST 追加 `resume_inconsistent` 事件（仅记录，不阻断、不加新错误码）。
- **FR-007**: `_run_ai_screen_task` 成功收尾段 MUST 换序为：job_events 落库 → run 计数（current_stage="done"）→ done 事件 → finalize 校验（不合法直接抛错，此时无任何历史轮）→ 写历史轮 → history_snapshot 事件 → 历史修剪 → 内存置 done 与清理；换序 + scoped 重试与条件降级（修订自 020：写历史轮对瞬时数据库锁做短退避重试，重试耗尽且该流程确无结果轮时允许 succeeded 条件降级为 failed 并提示可续跑重试保存）。
- **FR-008**: live 库清理 MUST 仅删除 run 828f8807 的 screening_results 与 screening_runs 行；严禁动 03fb82e1/0f0baa1b/94e2c440 及其判定、checkpoint；清理前 MUST 备份库文件；一次性执行，不入库不提交。

### Key Entities

- **判定（screening_results）**: 一条岗位在一轮筛选里的结论；粗筛为纯字符串 kept/dropped，精筛为含 verdict/reason/caveats/flags 的结构。
- **断点（pipeline_checkpoints）**: 某 run 在某阶段已完成的 job_id 集合（ai_rough/ai_fine）。
- **同源链（screening_runs by scrape_task_id）**: 同一次抓取产生的全部筛选 run 序列，按 created_at 排序即判定演进史。
- **历史轮（result rounds / save_finished_round）: 一条流程定稿后的唯一轮记录；只有走到终点的流程才配拥有。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: mock AI 返回 `{"results": 40}` 时精筛不抛异常且产出 uncertain 判定（tests/test_ai.py）。
- **SC-002**: 事故链回归测试通过：277/165/112/40 的数字场景下幸存者恰为 165、精筛判定继承、零静默丢失（tests/test_webui_app.py）。
- **SC-003**: finalize 判 paused 场景零 done 轮、任务 failed(internal_error)；正常完成路径照常写轮（tests/test_webui_app.py / tests/test_result_rounds.py 相关用例同步修正）。
- **SC-004**: live 库清理后最新 run 不是 828f8807，三条失败 run 判定行数不变。
- **SC-005**: `uv run python -m unittest tests.test_ai tests.test_webui_app tests.test_result_rounds tests.test_repo_hygiene` 全绿（涉及模块聚焦测试；test_screen_flow 因 FR-004 改写其既有用例，一并运行）。

## Verification Scope *(mandatory)*

- 功能/重构/拆分交付：验证范围为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务：默认只做卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）；不自动要求全量测试。
- 只有用户明确要求"全量/全部测试/完整验证"时，收口任务才执行全量测试。
- 本次任务提示词明确限定：只跑涉及的模块（test_ai、test_webui_app、test_result_rounds、test_repo_hygiene，另加被改写的 test_screen_flow）；前端零改动、不重建 dist；版本号不提升。

## Assumptions

- 事故背景已定案（三次失败 run、TypeError 与 invalid_ai_terminal_status 的机理、277/165/112/40 数字），不在本 spec 内重新调查。
- 行号漂移以代码片段内容定位为准（基于 HEAD 900927f）。
- 旧数据兼容：粗筛 verdict 为纯字符串 "kept"/"dropped"，早期误写成 "match" 的在新语义下自动兼容。
- 数据清理在代码修复合入后执行；用临时执行方式，不建脚本文件。
