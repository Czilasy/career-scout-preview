# 037 顶栏灵动岛 v2 + 提醒按钮回退 · Spec

**Feature Branch**: `037-dynamic-island-v2`
**Created**: 2026-09-03
**Input**: 用户确认（基于 036 设计调研与口头反馈："可以，根据你的建议来。我其实并不知道该做什么。我只是希望一个活泼的灵动岛在上面，跟手机上的那个岛一样。"）

## 最高目标（用户原话级，验收/任务的唯一衡量尺）

- **岛要"活"**：常驻顶栏，空闲时能看出呼吸；有事来时"啵"地弹一下；展开成面板时连续形变、像液体；数字跳动有弹性；点击/悬停有反馈；收起有回弹。系统"减少动态"时退化为静态切换。
- **岛承接所有全局提醒**：任务跑完（含"待确认/待筛选"）、任务出错、任务暂停；可并存叠加，按优先级排；点开面板看全部，点行直达。
- **提醒按钮回归最原始职能**：只承担"投递提醒"；角标数字=服务端投递数；点开=投递列表；数字与列表条目必须一致（根治角标有数、抽屉空的 Bug）。
- **顺序硬约束**：岛先接住所有提醒，提醒按钮才能回退。

## User Scenarios & Testing

### US-1 — 岛要"活"（常驻灵动岛的形变与节奏）
顶栏中部的灵动岛任何时候都活着：空闲时它有非常缓慢的呼吸感（scale 1 ↔ 1.006，4s 循环）；鼠标移上去它轻轻放大抬起来（scale 1.03，阴影加重）；按下去它微微收缩（scale 0.96）；松开回弹；新通知来时整个胶囊做一次"啵"的弹簧弹动（scale 1 → 1.12 → 1，~350ms）；数字变化时旧数字上滑淡出、新数字下滑淡入（弹簧 280ms），数字本身再"啵"一下放大回弹。
**Acceptance**:
- idle 态可观察到持续呼吸；系统开"减少动态"时呼吸静止，其他交互仍退化为瞬时。
- hover/press 通过 whileHover/whilePress 弹性缩放。
- 新通知到达：胶囊至少播放一次明显的弹跳动效（可用测试断点：props.notices 由 0 变 1 时，胶囊 CSS transform 含 scale 关键帧插值）。
- 数字由 `匹配 5 · 待确认 2` 变为 `匹配 6 · 待确认 3` 时，旧值离开、新值入场，元素 :key 变更触发。
- 全部动画在 reduce-motion 下退化为瞬时变化（useReducedMotion 控制）。

### US-2 — 岛承接所有全局提醒
不管后台任务在干啥、出了啥事，用户能从一个地方看到所有需要他注意的事。
- 任务跑完：来一条"本轮任务已完成"通知，detail 写"匹配 N · 待确认 M"（phase=scraped 写"待筛选 N"）。点开面板可见，点行去结果页。
- 任务出错：来一条红色"任务出错"通知，detail 写出错原因。点开面板可见，点行去处理现场（出错时跳到当前任务的活跃步骤）。
- 任务暂停：来一条琥珀色"任务已暂停"通知，detail 写"任务已暂停，请处理后继续"。点开面板可见，点行去暂停现场。
- 多类并存：出错 + 跑完 + 暂停可同时在池中，胶囊按优先级在 collapsed 形态展示最高优先级那条（出错 > 暂停 > 跑完），面板里都列出。
- 新一轮任务启动（running 出现）或任务被重置（idle 出现）：通知池清空。

**Acceptance**:
- 模拟三次状态跃迁 running→completed→attention/error，胶囊 collapsed 态从运行切到完成（含数字）再切到红色出错；面板始终能列出已发生的通知。
- 通知读过的行视觉淡化；未读数出现在胶囊右上角小点；展开面板期间未读行高亮，收起时统一标记已读（同 FR-005 修订口径）。
- 离开顶栏去抽屉（收藏/历史/提醒）时，岛面板互斥收起；回来时未读仍保留，新轮启动时才清。
- 零新后端接口，全部由 `roundStatus` 跃迁派生。

### US-3 — 提醒按钮回归
"提醒"按钮的角标只反映投递提醒数（来自 `GET /api/job-reminders/count` 的 total）；点开抽屉的列表/total 也是同一服务端源（`GET /api/job-reminders` 的 total），数字与列表条数天然一致；不再合成胶囊派生的待确认/出错/跑完数。
**Acceptance**:
- 投递为 0、任务跑完：角标为 0，按钮 aria-label="查看提醒"；不再出现"角标显示 3、抽屉空"的错位。
- 投递为 5：角标显示 5（≥100 时显示 99+，aria 仍说真数 137）；抽屉列表 total=5（同服务端），断言相等。
- 抽屉动作成功（跟进/标记已荒废）后角标按服务端新 total 刷新，行为不变（沿用 002 合同）。

### 边角
- 跨 profile 切换：通知池重置（App 层在 `currentProfileId` 切换时调 `notices.reset()`），避免显示旧 profile 的通知。
- 浏览器模式：行为与 EXE 一致；岛在所有平台受主题色控制，kaleido 特殊主题下半透明毛玻璃特判。
- 窄屏（≤760px）：面板改全屏宽度，居中；胶囊与按钮在 1300px 断点下收纯图标的行为不受影响。

## Requirements

### Functional

- **FR-001** 灵动岛 MUST 常驻顶栏中心；空闲时显示当前平台名（BOSS/智联），点击回主页。
- **FR-002** 灵动岛 MUST 显示实时进度（抓取/筛选 done/total；total 未知省略分母）。
- **FR-003** 灵动岛 MUST 弹出通知通知面板（覆盖四态判定 live 优先级），通知面板展示跑完/待确认/出错/暂停四类并存的通知；点击通知行直达目标页。
- **FR-004** 通知池 MUST 由 `roundStatus` 跃迁派生；状态从未观察→任意不产生通知；任何→running 清空通知；任何→idle 清空通知；running→completed 产跑完；completed→attention/error 产错误；attention/paused→其他 产暂停；同 kind 通知按"替换"策略（同一时刻同类最多 1 条）。
- **FR-005** 通知已读为会话级内存标记：展开面板期间未读行保持高亮；收起（dismiss，backdrop/Escape/行点击/collapse 统一收口）时对"关闭瞬间已在面板的通知集合"统一标已读，关闭窗口期新到达的通知保持未读；不跨会话持久化；进入新一轮或重置时整体清空。（037 复审二修订：原"点开面板/点行即全部已读"改为"收起才标，带 id 快照"，修未读高亮死代码与误吞竞态。）
- **FR-006** 灵动岛 MUST 使用 motion-v 实现动画（入场弹簧、数字键入、弹动呼吸）；入场动画通过 `:initial`/`:animate` 触发；避开 AnimatePresence 的退出动画链路（jsdom 测试稳定性）；系统"减少动态"时所有弹簧退化为 `{duration: 0}`。
- **FR-007** 灵动岛面板 MUST 与收藏/历史/提醒三个抽屉互斥：开面板关三抽屉；开任一抽屉关面板；Escape/点击面板背景关面板。
- **FR-008** 提醒按钮 MUST 退化为单源投递提醒入口：角标=服务端 `total`；点击=打开投递提醒抽屉；aria-label 简化为"查看投递提醒，共 N 个逾期岗位"。
- **FR-009** 通知面板/胶囊 MUST 跟随主题：明暗走令牌；kaleido 特殊主题下半透明 + blur 6px 保证可见；不可见色对比度满足品牌令牌。
- **FR-010** 灵动岛 MUST 提供 `defineExpose({ collapse })`；App 在打开三抽屉时调用 `collapse()`，反之灵动岛通过 `expand` 事件通知 App 关闭抽屉。
- **FR-011** 全局测试 matchMedia 替身 MUST 在测试中按 query 返回不同结果：`(prefers-reduced-motion: reduce)` 默认返回 true（动画静态），`(max-width: ...)` 由原 `__setNarrowMatchMedia` 控制。
- **FR-012** 交付时 MUST 启用 motion-v 的 Vue 适配 API：`Motion` 组件（`whileHover`/`whilePress`/`:initial`/`:animate`）+ `useReducedMotion`；不引入 GSAP / @vueuse/motion / 其他动画库。

### Non-Functional / 架构

- **架构边界（AGENTS + 宪法）**：
  - 允许修改：`webui/src/App.vue`（仅净减接线区，禁向其追加新逻辑到红线）、`webui/src/components/DynamicIsland.vue`（重写）、`webui/src/test/setup.ts`（matchMedia 增强）、新增 `webui/src/composables/useIslandNotices.ts` 与 `webui/src/composables/useReminderBadge.ts`、新增 `webui/src/components/IslandNoticePanel.vue`、各测试文件。
  - 禁止修改：`webui/src/views/DiscoveryView.vue`（1249 行超 Vue 红线 1200，禁改）、`webui/src/composables/useDiscoveryState.ts`（1267 行 TS，禁止追加逻辑；036 派生保持不变）、任何后端文件（`webui/*.py`、`scripts/`、`webui/jobFeedback.ts` 等 API 客户端）。
  - 禁改新增接口：不改动后端 `/api/job-reminders/*`、不新增任何 endpoint。
  - 新文件 MUST 在同一批次登记 `.specify/memory/constitution.md`「模块地图」（原则 VI）。

### Key Entities
- **IslandNotice**：id, kind (`completed`/`error`/`paused`), title, detail?, target, at, read
- **CapsuleStatusPayload**（沿用 036，不变）
- **ReminderBadgeState**（角标单源状态）：reminderTotal, badgeText, ariaLabel
- **LiveCapsulePriority**：attention/error > attention/paused > completed > running > idle（岛 collapsed 形态展示用）

## Success Criteria

- **SC-001** 新通知出现时，胶囊的"啵"弹动可在浏览器视觉走查中确认（测试断点：props.notices 由 0 变 1，胶囊测试桩具有 transform 关键帧）。
- **SC-002** 数字由 N 变 M 时，旧值上滑淡出、新值下滑淡入（测试断点：key 变更后新元素含 `style="transform: ..."`）。
- **SC-003** reduce-motion 媒体查询为 true 时，所有 `:initial`/`:animate` 等价为 undefined 或 `duration:0`，元素直接显示最终态。
- **SC-004** 多类通知并存场景：胶囊 collapsed 显示最高优先级通知；面板列出全部（3 条）；点击行直达对应目标页。
- **SC-005** 投递=0、任务跑完场景：角标=0、按钮 aria="查看提醒"；抽屉空态文案成立。
- **SC-006** 投递=5：角标显示 5；抽屉 total=5（断言相等）。
- **SC-007** 面板/抽屉互斥：开收藏关闭面板与提醒抽屉；开面板关闭收藏与提醒。
- **SC-008** 跨 profile 切换：通知池清空；按钮 count 按新 profile 重拉。
- **SC-009** 验证门禁：聚焦测试 + 前端全量测试 + `npm run build` + 仓库卫生检查全部通过（宪法 V）。
- **SC-010** Windows 真实 EXE 端到端走查：胶囊"活感"在桌面壳中可感；动画减少动态下退化；面板与三抽屉互斥；角标与投递列表一致。

## Verification Scope
- 聚焦测试：`webui/src/composables/__tests__/useIslandNotices.spec.ts`、`webui/src/components/__tests__/IslandNoticePanel.spec.ts`、更新 `DynamicIsland.spec.ts`。
- 前端全量测试：vitest run（覆盖改动相关 spec）。
- `npm run build`：vue-tsc 严格通过 + vite 构建成功。
- 仓库卫生：`uv run python -m unittest tests.test_repo_hygiene`。
- 后端不动；不跑后端全量（宪法 V 适用功能/重构/拆分；本批次仅前端+少量测试 setup）。
- 用户端到端走查在交付后进行。

## Assumptions
- A1 不新增/不改任何后端接口；通知池完全由 036 已上抛的 `roundStatus` 派生。
- A2 已读=会话级内存（不持久化）；用户口头确认。
- A3 TaskCompletedToast（035）与岛"跑完"通知职责重叠，岛交付后由岛替代；本批次保留 035 不删除（待后续清理）。
- A4 NoticeBar（App.vue 表单级错误）与岛重叠，建议维持原状（就地表单反馈）；本批次不动。
- A5 motion-v 引入为唯一动画库；体积由 vite tree-shaking 自动收敛。
- A6 kaleido 半透明 + blur 策略沿用 036 在 DynamicIsland.vue:198-211 的特判，扩展到面板。
- A7 后端 `/api/job-reminders/count` 与 `/api/job-reminders` 的 total 同源（002 spec 合同）；不重新协商。

## Out of Scope
- 删除/重构 TaskCompletedToast（035）；保留至后续清理批次。
- 通知已读的跨会话持久化；多端同步。
- 通知面板排序/分组设置项。
- 自定义顶栏颜色/字号。
- 灵动岛在 macOS/Linux 桌面壳的差异化（沿用 Windows EXE 行为）。