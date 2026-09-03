# 037 灵动岛 v3 · 独立审查提示词

> 用途：把本文档交给**另一个 AI / 有能力的评审者**，让它**独立审查** 037 v3 的实现是否真的兑现了 Spec 的承诺。
> 关键：审查方**不应只信实现方自述**，而应把「Spec 承诺 ↔ 代码实况 ↔ 测试证据」三者对照，独立挑刺。
> 项目根：`/d/项目/career-scout-preview`（下文所有路径以此相对）。

---

## 一、背景与你的角色

你是一名严格的代码审查者（前端 Vue 3 + TS + vitest + motion-v）。你要审查的是 **Spec 037「灵动岛 v3 · live 仪表盘 + 转盘轮播 + toast 折叠」** 的实现是否忠实兑现了其 spec 承诺。你独立核对，不受实现方自述影响。

请先读这些**权威文档**（它们定义了什么叫"做对了"）：
1. `specs/037-dynamic-island-v3/spec.md` —— 唯一现行 Spec：最高目标、功能要求、成功标准、红线边界、删除清单
2. `specs/037-dynamic-island-v3/plan.md` —— 设计策略、数据流、动画策略
3. `specs/037-dynamic-island-v3/tasks.md` —— 应执行的任务拆解

然后再核对实现。

---

## 二、背景真相（实现方的最高指导，你要拿来当"验收尺"）

用户原话级要求，**任何实现不得违背**：
1. **岛要展示中间态，不只展示结果** —— running 态必须实时显示进度（如"正在抓取 12/30"），旧版 `useIslandNotices` 曾 `clearAll()` 主动丢弃 running 数据，037 v3 必须改掉。
2. **岛是"弹弹的"实时仪表盘** —— pill 宽度随内容 spring 弹性伸缩，数字跳有弹性，颜色多彩。
3. **转盘轮播优雅处理打断** —— 主流程永远 pinned 在 lane 0；打断到达垂直转一次展示、转完沉入 panel 未读、不在 pill 重复；多条积压只转最新一条，余数只进 panel。**不自动轮播**。
4. **报错/暂停是 live state 红光** —— 岛体隐隐闪光红，任务恢复自然褪红，不需手动 dismiss，不进打断队列。
5. **toast 折叠** —— TaskCompletedToast 删除，"完成→查看最新"由 completed pill 点击 navigate 接管；所有 NoticeBar 提示进入灵动岛，展示一次后都沉入未读历史；warning/error 保留原 tone，info/success 使用提示色。
6. **硬不变式** —— carousel 只切显示位，**永不冻结底层数据流**：打断展示期间主流程数据照常由 roundStatus 驱动更新，转回来直接看到新数字。

---

## 三、交付变更清单（`git status --short` 视角）

- 新增：
  - `webui/src/composables/useIslandCarousel.ts`（转盘轮播状态机）
  - `webui/src/composables/__tests__/useIslandCarousel.spec.ts`
  - `specs/037-dynamic-island-v3/{spec,plan,tasks}.md`
- 修改：
  - `webui/src/App.vue`（接线 carousel / NoticeBar 全量接入 / 提醒打断 / reset）
  - `webui/src/components/DynamicIsland.vue`（重写：carousel+live+两色+红光+badge）
  - `webui/src/components/IslandNoticePanel.vue`（interrupt kind + tone + 当前两色口径）
  - `webui/src/composables/useIslandNotices.ts`（running 不清池 + sinkInterrupt + kind=interrupt）
  - `webui/src/components/__tests__/{DynamicIsland,IslandNoticePanel}.spec.ts`
  - `webui/src/composables/__tests__/useIslandNotices.spec.ts`
  - `webui/src/__tests__/App.spec.ts`
  - `webui/src/views/DiscoveryView.vue`（**红线例外：删除 toast 接线并转交恢复提示**）
  - `CHANGELOG.md`、`.specify/memory/constitution.md`、`README.md`、`specs/037-dynamic-island-v3/checklists/requirements.md`
- 删除：
  - `webui/src/components/TaskCompletedToast.vue`
  - `webui/src/components/__tests__/TaskCompletedToast.spec.ts`

---

## 四、审查维度与必查问题

### 4.1 Spec ↔ 代码一致性
- 逐条核对 spec.md 的 **FR-001…FR-013** 与 **SC-001…SC-010**，在代码里找证据（函数、computed、模板分支、CSS 钩子），逐条标注「已兑现 / 部分兑现 / 未兑现 + 证据行号」。
- spec 的验收标准是否真有对应测试覆盖？没有的列出缺口。
- **特别注意**：完成态当前只验收两色（matched 绿 + pending 琥珀），因为 `useDiscoveryState.ts` 的 capsule 只上抛 `{matched, pending}` 两项；不要把未来四色扩展误判为当前缺陷，但要核对实现和文档是否都没有伪造缺失计数。

### 4.2 硬不变式（FR-011）是最大审计点
- 打断展示期间，主流程数据流**绝不能**被暂停/缓存/快照。
- 看 `useIslandCarousel.ts` 的 `mainLaneState` 是不是 `computed` 直接读 `roundStatus.value?.capsule`（而不是一份拷贝 ref）。
- 打断 lane 展示时，`mainLaneState` 是否仍随 roundStatus 重算？
- 转回 lane 0 时 pill 是否立即显示最新数字？

### 4.3 只转一次语义（FR-004/FR-005）与沉入
- `pushInterrupt` 是否在没有 active interrupt 时首次转入展示位，并在已有非 sticky interrupt 时切到最新一条而不触发自动轮播？
- 已在展示打断时再 push，是否保持主流程数据继续更新，同时避免补转旧队列项？
- 非 sticky interrupt 是否定时自动沉入 panel（经 onSinkInterrupt → `sinkInterrupt` → upsert kind=interrupt 未读）？
- 当前产品来源是否都不传 sticky？若实现保留 sticky 分支，仅审查其防御路径是否不会卡死队列；不把 sticky 当作当前用户行为。
- **角标单源**：pill 角标 = notices 未读 + badgeCount，有没有**重复计数**（打断既在队列又进了 panel）？
- `reset()` 是否清了所有 timer（防止 profile 切换后残留回调 mutate 死队列）？

### 4.4 live 中间态与两色
- running 态 pill 是否显示"正在抓取 N/M"（scraping，蓝）/"AI精筛 N/M"（screening，紫）？
- completed 态是否显示 chips；phase=scraped（只有抓取没跑精筛）时行为是否合理？
- 红光：attention 态是否出现 glow 层（error/paused），离开 attention 是否褪去，reduce-motion 下是否静止？
- `recrawl_fetch_jd` 是否显示为“抓取 JD”；窄屏长文案是否避免硬裁剪？窗口 resize 后胶囊宽度是否重新测量？

### 4.5 通知中心行为不回退（FR-013）
- panel 展开/dismiss（带 id 快照）/互斥（collapseIsland + 三抽屉）/tab trap/Teleport backdrop/navigate 语义/all-read 直导航/aria-live announce —— 全都要在，并以当前统一 Spec 为验收依据；dialog 打开后焦点只能在 dialog 内循环，关闭后归还胶囊。

### 4.6 红线边界与行数（宪法）
- **禁改范围是否守约**：`useDiscoveryState.ts` 仅允许已确认的 JD 阶段派生与 `jd` 类型变化；后端任何文件 MUST 未动；`ReminderDrawer.vue`、`api.ts`、`jobFeedback.ts` MUST 未动。
- **DiscoveryView.vue 红线例外是否守约**：允许删除 toast 相关接线并将恢复提示转交灵动岛，不得动其余业务逻辑。用 `git diff webui/src/views/DiscoveryView.vue` 核对 diff 是否只落在这两个已确认边界。
- **行数硬线**：Python ≤ 800；Vue ≤ 1200（`DynamicIsland.vue`、`IslandNoticePanel.vue`、`App.vue`）；`useIslandCarousel.ts` 自设 ≤ 250。用 `wc -l` 复核。
- `useDiscoveryState.ts` 里 `taskCompletedToast` ref 应**保留不动**（即使不再有消费者）——核对它确实还在且未被改。

### 4.7 测试质量
- 重点测试文件是否**真的验证了核心承诺**（而非测试实现细节/自说自话）：
  - `useIslandCarousel.spec.ts`：不冻结（打断期间 done 推进）、只转一次、多条积压不自动轮播、sticky dismiss、reset 清 timer。
  - `DynamicIsland.spec.ts`：carousel translateY / 两色 chip / 红光出现+褪去 / 转回数字推进 / badge+playPop / reduce-motion 退化。
  - `App.spec.ts`：所有 NoticeBar tone 都不渲染独立 NoticeBar；展示结束后均沉入未读历史，warning/error 保留原 tone，info/success 使用提示色；reminder 0→N 推一次、profile 切换 reset、toast 不存在回归。
- 测试数量看起来对吗？（实现方自述：38 spec 合计 useIslandCarousel 13 + useIslandNotices 18 + DynamicIsland 34 + IslandNoticePanel 19 + App 38）
- 有没有测试是"改了实现才能让断言假通过"的脆弱写法？

### 4.8 你可以独立复跑的门禁（不要只信自述）
```
cd /d/项目/career-scout-preview/webui && npm run test        # 应全绿
cd /d/项目/career-scout-preview/webui && npm run build       # vue-tsc --noEmit + vite 应 0 错
cd /d/项目/career-scout-preview && uv run python -m unittest tests.test_repo_hygiene   # 应 14/14 OK
```
- `useDiscoveryState.ts` 与 `DiscoveryView.vue` 的实际变更必须符合上面的已确认例外；不要用旧行数假设替代 `git diff` 证据。
- `wc -l` 复核关键 Vue 文件未破线，并单独记录当前实际行数。

---

## 五、输出要求

按如下结构输出一份中文审查报告，**每一条都要有证据（文件:行号 或 可复跑命令的输出）**：

1. **总评**（一句话：这版是否值得交付 / 需返工 / 需补洞）
2. **硬不变式与红线审计结论**（最大审计点，单独列）
3. **逐 FR / SC 一致性表**：已兑现 | 部分兑现（说清缺在哪）| 未兑现（说清后果）
4. **发现的真实缺陷 / 边界漏洞**（按严重度 P0/P1/P2 分级，P0=会造成错误/数据问题，P1=破坏交互/可访问，P2=质量/一致性）
5. **测试覆盖缺口**
6. **对 spec 本身的质疑**（如果有：spec 目标写得是否合理/可实现/与数据源是否匹配）
7. **复跑门禁的结果**

不要客气，不要为了讨好实现方而放水。宁可严格，你的价值就在于找出实现方自己看不见的问题。
