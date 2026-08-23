# Feature Specification: 错误如实呈现与数据口径一致（020）

**Feature Branch**: `020-error-truth-consistency`

**Created**: 2026-08-23

**Status**: Draft

**Input**: 需求已在上一会话完成 grill-me 边界质询并冻结（冻结核为自包含任务提示词），本 spec 按其整理。用户描述："一个 Spec 修复 7 个已验证缺陷：熔断器把登录失效误报成 IP 级风控且永不复位；前端 70+ 条错误码中文映射是死代码；续跑时跨平台重复岗位同时进保留与剔除两个列表；用过收藏/反馈的画像永远删不掉；纯抓取轮发起 AI 筛选后运行中主按钮仍显示可点的「开始 AI 筛选」；判定链合并门槛用数量比较导致已剔除岗位复活；终态 succeeded 落库后写历史轮失败被静默吞掉。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 登录失效如实报告，熔断器可复位 (Priority: P1)

我的一次抓取里连续两个岗位撞上登录墙（登录过期是日常必发生的事）。熔断器打开后，界面不应把原因谎报成「IP 级风控拦截」并引导我去换网络、任务硬停；而应如实报告「登录已失效」，走既有的登录二次复核与跳过链路。如果是真风控（验证码/限流/IP 拦截）才报风控。熔断器打开后不应永久卡死：冷却期满且登录探测通过后，后续组合/批次应能继续发起抓取。

**Why this priority**: 登录过期是日常事件，被误报为风控会引导用户做无意义的网络更换并硬停任务，是七条中误导性最强的一条。

**Independent Test**: 单元级驱动 source 熔断器连续两次 `source_login_required` 开闸，断言开闸返回的错误码是 `source_login_required` 而非 `source_blocked`；连续两次风控类 signal 开闸时返回对应风控码。冷却期满 + preflight 通过后调用复位入口，断言熔断器关闭、抓取可继续发起；冷却未满或 preflight 失败时不复位。

**Acceptance Scenarios**:

1. **Given** JD 批内连续两个岗位返回 `source_login_required`（熔断器开闸），**When** 后续列表/JD 请求被熔断，**Then** 返回的失败码是 `source_login_required`（登录文案、可走登录二次复核），不是 `source_blocked`。
2. **Given** 熔断器因风控类 signal（`source_verification_required`/`source_rate_limited`/`source_blocked`）开闸，**When** 后续请求被熔断，**Then** 返回对应的风控类失败码，风控硬停链路（含硬停文案）不变。
3. **Given** 熔断器已打开且冷却期已满，**When** 编排在列表/JD 批次发起前做登录探测且探测通过，**Then** 熔断器复位、该批次正常发起；探测不通过或冷却未满时不复位，仍返回开闸失败。
4. **Given** 熔断器因 `source_cdp_unavailable` 相关信号触发浏览器自动重启链，**When** 重启链运行，**Then** 该链路行为不变（不被本次修复误伤）。

---

### User Story 2 - 后端只回机器码时用户看到中文错误 (Priority: P2)

我操作界面时后端某个接口失败，响应里只有机器码（如 `block_not_resolved`）没有中文原因。界面不应把英文机器码原样甩给我，而应先查前端已有的错误码中文映射表，查不到才退回原始值。

**Why this priority**: 纯展示问题，不损数据，但 70+ 条已写好的中文映射全部是死代码，属明显浪费且观感差。

**Independent Test**: 构造 ApiError payload 只含 `error_code: "job_offline"`（映射表内条目、无 user_message/message/error_reason/error），断言错误消息是映射表中的「岗位已下架」；映射表没有的码（如 `block_not_resolved`，本就是直出示象的来源）保持现状回退链原样直出。

**Acceptance Scenarios**:

1. **Given** 接口错误 payload 仅含机器码 `error_code`，**When** 前端构造错误提示，**Then** 优先使用错误码中文映射表的文案。
2. **Given** payload 含更具体的 `user_message`/`message`/`error_reason`，**When** 构造错误提示，**Then** 仍按既有优先级使用它们，映射表只在机器码兜底段之前、`error`/`error_code` 直出之前介入。

---

### User Story 3 - 续跑时跨平台重复岗位只进剔除列表 (Priority: P1)

上一次运行里岗位 X 已进粗筛断点且被判「保留」，暂停期间对端平台出现了同指纹岗位（跨平台去重命中）。我点「继续」续跑时：X 应只出现在「跨平台重复剔除」一侧，不能同时出现在保留列表和剔除列表，不能计数翻倍，也不能被继续精筛。

**Why this priority**: 直接违背 019 已冻结契约（SC-003/FR-005「不重复计数、任意次续跑与一次跑完一致」），是数据口径错误。

**Independent Test**: 构造「断点内已保留岗位 + 本轮新命中跨平台重复」的续跑场景，断言该岗位只出现在剔除列表、保留幸存者不含它、总计数不翻倍。

**Acceptance Scenarios**:

1. **Given** 续跑断点内岗位 X 判定为保留，且本轮 X 命中跨平台去重，**When** 粗筛收尾计算保留/剔除，**Then** X 仅存在于剔除列表（剔除原因可追溯），保留列表与幸存者不含 X。
2. **Given** 不含跨平台重复的普通续跑，**When** 同一计算执行，**Then** 行为与现状一致（断点内默认保留语义不变）。

---

### User Story 4 - 用过的画像能删掉 (Priority: P2)

我对某些岗位用过收藏、反馈等操作后，想删掉这个画像。删除不应因为历史操作记录的外键约束而失败报错；画像及其全部关联数据（含岗位状态事件、命令回执）应一并删除。

**Why this priority**: 功能完全不可用（删除必炸），但影响面是单画像管理操作，低于数据口径与误导性硬停。

**Independent Test**: 临时库建画像 → 关联岗位 → 写入收藏/反馈事件与回执 → 删除画像成功且子表行同灭；无事件画像删除行为不变。

**Acceptance Scenarios**:

1. **Given** 画像存在岗位状态事件与命令回执，**When** 删除画像，**Then** 删除成功，事件与回控行一并清理，无外键错误。
2. **Given** 画像无任何使用记录，**When** 删除画像，**Then** 行为与现状一致（含简历文件由上层清理的既有分工不变）。

---

### User Story 5 - 筛选运行中主按钮如实显示暂停 (Priority: P2)

我完成一轮纯抓取（未筛选），随后发起 AI 筛选。整个筛选运行期（可达几十分钟）主按钮必须显示「暂停筛选」且不可重复发起；不能因为本轮曾是"已抓取未筛选"状态而一直显示可点的「开始 AI 筛选」——误点会得到启动失败提示而任务其实在跑。

**Why this priority**: 长时间运行期界面状态错误且可误操作，但一次误点不损数据（后端会拒绝重复发起）。

**Independent Test**: 组件测试：scraped_only 轮发起 AI 筛选进入运行态后，断言主按钮为「暂停筛选」、不可再发起；筛选结束后状态回归正确。

**Acceptance Scenarios**:

1. **Given** 轮状态为已抓取未筛选，**When** 用户发起 AI 筛选且任务进入运行，**Then** 主按钮显示「暂停筛选」语义，重复发起入口不可用。
2. **Given** 筛选结束（成功/部分完成），**When** 轮状态刷新，**Then** 按钮按终态正确显示，不残留运行态。
3. **Given** 从历史/刷新恢复的已抓取未筛选轮（未发起筛选），**When** 查看主按钮，**Then** 仍显示「开始 AI 筛选」（待筛选展示模式不变）。

---

### User Story 6 - 二次接管续跑不复活已剔除岗位 (Priority: P1)

同一次抓取链上：run1 粗筛判掉一批岗位，run2 接管后只写了精筛判定。我第三次点「继续」时，判定链必须把 run1 的剔除记录合并进来——判定数量够了不能当作"覆盖完整"。断点里任何没有判定记录的岗位才触发合并；已明确剔除的岗位不复活。

**Why this priority**: 018 的数量比较公式本身有洞：精筛判定计入总数后，"数量 ≥ 断点数"不等于"断点全覆盖"。岗位复活与静默保留同样是数据口径错误，且根因在 018 spec 文本（验收场景原文即数量比较），必须连 spec 一起修订，避免 spec 与实现漂移。

**Independent Test**: 多 run 链场景：run1 粗筛写 dropped 判定 + 断点 → run2 接管只写精筛判定（数量 ≥ 断点数但键集不覆盖）→ run3 续跑加载判定，断言合并发生、run1 的 dropped 岗位不复活、resume_inconsistent 护栏事件按新口径触发。

**Acceptance Scenarios**:

1. **Given** 断点岗位集合减去现有判定键集非空（存在无判定记录的断点岗位），**When** 续跑加载判定，**Then** 触发同源链合并（同源校验与新旧覆盖语义不变）。
2. **Given** 断点岗位全部有判定记录（数量比较旧公式也会放行的场景），**When** 续跑加载判定，**Then** 跳过合并，行为与现状一致。
3. **Given** 合并后仍存在无判定记录的断点岗位，**When** 续跑开始，**Then** resume_inconsistent 事件按覆盖口径记录缺失数，不阻断。
4. **本条修订 018**：`specs/018-screening-chain-bugfix/spec.md` US2 验收场景 1 与 FR-004 中「判定数少于断点数」的触发表述 MUST 同步改为覆盖比较表述，修订注明出自 020。

---

### User Story 7 - 筛选完成后结果保存失败不再静默吞掉 (Priority: P1)

AI 筛选全部跑完、终态 succeeded 已落库，此时写历史结果轮失败（典型为瞬时数据库锁）。系统不能把这事吞掉后让内存显示 failed 而 DB 是 succeeded、又没有任何结果轮——几小时筛选成果"消失"且终态堵死续跑。应先短重试；仍失败则把 DB 终态条件降级为 failed（仅当该流程确实没有结果轮时），并给出明确提示「筛选已完成但结果保存失败，点继续可重试保存」；点继续后应跳过重筛直达收尾重建结果轮。

**Why this priority**: 用户损失感知最大（成果"消失"），且堵死续跑。根因是 018 FR-007「纯换序，不新增补偿/回滚逻辑」的决定在新事实下有洞，属显式修订 018 契约。

**Independent Test**: 构造 finalize 成功后 save_finished_round 抛瞬时锁错误的场景：断言重试发生；重试耗尽后 run 被条件降级为 failed（且仅当无结果轮时降级，已有结果轮时不动 succeeded）；内存错误信息含「点继续可重试保存」；随后模拟续跑，断言不重筛、直达收尾并成功落结果轮。

**Acceptance Scenarios**:

1. **Given** 终态 succeeded 已写库且结果轮保存首次失败，**When** 收尾路径执行，**Then** 先做 2-3 次短退避重试。
2. **Given** 重试仍失败且该流程名下确无任何 result_snapshot 轮，**When** 条件降级执行，**Then** 事务内校验通过后 succeeded → failed 转换成功，失败原因落库。
3. **Given** 该流程名下已存在结果轮（不应发生，防御），**When** 条件降级尝试，**Then** 拒绝降级，保持 succeeded，落诊断事件。
4. **Given** 降级为 failed 后用户点继续，**When** 续跑执行，**Then** 判定/JD 断点齐全，不重新筛选，直达收尾重建结果轮并恢复终态。
5. **Given** 正常完成路径（结果轮一次写成功），**When** 收尾，**Then** 行为与 018 换序后的现状一致，无多余降级尝试。
6. **本条修订 018**：FR-007「纯换序，不新增补偿/回滚逻辑」MUST 由 020 显式修订为「换序 + scoped 重试与条件降级」，修订注明出自 020。

---

### Edge Cases

- 熔断器开闸但 `last_signal` 缺失（理论不可达，防御）→ 开闸失败码回落 `source_blocked`，不产生未知码。
- 熔断器冷却期内到达的请求 → 不做登录探测（避免冷却期内空耗探测），直接返回开闸失败。
- 跨平台重复条目与本轮粗筛判定同时命中同一岗位 → 剔除侧以显式重复条目为准（现状语义），保留侧不得同时出现。
- 画像删除时子表存在行但主表行已被人并发删除 → 行为与现状一致（KeyError 语义不变）。
- 映射表查不到的错误码 → 沿既有回退链直出原始值，不显示「未知错误」类的伪造文案。
- scraped_only 轮筛选启动请求自身失败（409 等）→ 界面回到可重试状态，不残留运行态。
- 判定值为 dict 与早期纯字符串、早期误写 "match" → 合并语义与 018 现状一致，不因门槛改法而变化。
- 条件降级事务内查询失败 → 降级整体失败走既有 failed 落库路径，不允许半降级。
- 用户在重试/降级期间恰好手动结束保存 → 既有 user_finished 守卫优先，不与其冲突。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 熔断器开闸后的列表/JD 请求失败码 MUST 透传开闸信号（`last_signal`）：登录开闸报 `source_login_required`，风控类开闸报对应风控码；全部开闸检查点（列表、单岗位详情、JD 批量、智联批量的串行与并行路径）口径一致，信号缺失时回落 `source_blocked`。
- **FR-002**: 熔断器 MUST 存在复位接线：编排在列表/JD 批次发起前，冷却期满时执行登录探测，探测通过才复位（`try_reset`）；冷却未满或探测失败不复位。`source_cdp_unavailable` 的浏览器自动重启链 MUST 保持既有行为。
- **FR-003**: 前端错误消息回退链 MUST 在直出机器码（`error`/`error_code`）之前插入错误码中文映射表查表；更具体的 `user_message`/`message`/`error_reason` 优先级不变。
- **FR-004**: 粗筛续跑幸存者计算 MUST 排除本轮跨平台去重命中的岗位：断点内保留岗位命中 `_dup_ids` 时不得进入保留/幸存者列表（019 SC-003/FR-005 契约的续跑反向边，019 spec 无需修订）。
- **FR-005**: `delete_profile` MUST 在删主表行前显式清理岗位状态事件与命令回执两张子表；不修改迁移与外键定义；无子表行画像的删除行为不变。
- **FR-006**: 轮状态计算 MUST 让运行态优先于「已抓取未筛选」次级状态：scraped_only 轮发起筛选进入运行态后主按钮为暂停语义且不可重复发起；恢复/加载路径的 scraped_only 展示语义不变。
- **FR-007**: 续跑判定加载的合并触发条件 MUST 从「判定数 < 断点数」改为「断点岗位集合减判定键集非空」（覆盖比较）；resume_inconsistent 护栏事件同口径；018 spec 的对应表述（US2 场景 1、FR-004、SC 相关联动）MUST 同步修订并注明出自 020。
- **FR-008**: 终态 succeeded 落库后结果轮保存失败时 MUST 先做 2-3 次短退避重试；重试耗尽 MUST 走新增 store 条件降级方法（事务内校验该流程无 result_snapshot 轮才允许 succeeded → failed）；内存错误信息 MUST 明示「筛选已完成但结果保存失败，点继续可重试保存」并落诊断事件；降级后的续跑 MUST 跳过重筛直达收尾。018 FR-007 的「纯换序」表述 MUST 由本条显式修订。

### Key Entities

- **熔断信号（breaker last_signal）**: 打开熔断器的最后一个平台级失败码；决定开闸期间对外的失败语义（登录 vs 风控）。
- **跨平台重复集（_dup_ids/_dup_entries）**: 本轮跨平台去重命中的岗位集合与剔除条目，粗筛收尾时强制归入剔除侧。
- **判定覆盖（checkpoint_ids − verdicts 键集）**: 断点内没有判定记录的岗位集合；非空即触发同源链合并，取代数量比较。
- **结果轮（result_snapshot run）**: 流程定稿的唯一轮记录；条件降级的守卫条件（存在即拒绝降级）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 登录开闸返回登录码、风控开闸返回风控码、复位接线生效/不误伤的用例全部通过（tests/test_source.py 扩展 + pipeline 相关测试）。
- **SC-002**: 仅含机器码的错误构造出中文消息；既有优先级场景回归通过（webui/src/__tests__/errorCodes.spec.ts）。
- **SC-003**: 「断点保留 + 本轮重复命中」续跑场景岗位只出现在剔除侧、计数不翻倍（tests/test_webui_app.py）。
- **SC-004**: 带事件/回执画像删除成功且子表同灭；无事件画像删除回归通过（tests/store 相关用例）。
- **SC-005**: scraped_only 轮发起筛选 → 运行中显示暂停按钮、不可重复发起；恢复展示语义回归通过（webui/src/__tests__/DiscoveryView.spec.ts）。
- **SC-006**: 多 run 链（粗筛 dropped → 接管判满 → 续跑）断言 dropped 不复活、护栏事件新口径；全覆盖场景跳过合并回归通过（tests/test_screen_flow.py + tests/test_webui_app.py）。
- **SC-007**: 结果轮保存失败 → 重试 → 条件降级 → 「点继续」恢复路径全链通过；已有结果轮拒绝降级；正常路径无行为变化（tests/test_webui_app.py / tests/test_result_rounds.py）。
- **SC-008**: 018 spec 两处契约修订完成且文本与实现一致；验证门禁全绿：相关聚焦测试、后端全量 `uv run python -m unittest discover -s tests`、前端 `npm test`、`npm run build`、卫生测试。

## Verification Scope *(mandatory)*

- 功能/重构/拆分交付：验证范围为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务：默认只做卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）；不自动要求全量测试。
- 本次任务为功能（缺陷修复）交付：适用完整门禁（宪法 V）；前端源码有改动，必须重建并提交 `webui/dist`。

## Assumptions

- 七条缺陷的证据已在冻结前逐条复核到代码级（file:line），本 spec 不重新调查根因；行号漂移以代码片段内容定位为准。
- 修 bug 铁律：每条先写能复现的失败测试，再修实现。
- 第 6、7 条是对 018 spec 的显式契约修订，修订文本直接落在 018 spec 原文处并注明「修订自 020」；019 spec 无需改动。
- 条件降级方法落在 store 域内（screening run 状态机相邻区域），不改迁移、不改外键、不加新职责到 app.py/store.py 超大文件（仅修改既有函数行为或 store 域内补方法，宪法允许范围）。
- 范围外：其余约 30 条中低严重度审计发现、完整「succeeded 无轮」开机自愈、BACKLOG 维护（B049 顺手归档可选，仅本地文件）。
