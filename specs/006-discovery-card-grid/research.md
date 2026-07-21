# Research: 发现结果页卡片网格化与体验修复

**Feature**: 006-discovery-card-grid  
**Date**: 2026-07-22

## 调研目标

确认 `webui/index.html` 中结果页的现有代码结构、后端能力是否已满足需求、以及各改动的精确位置。

## 调研结论

### R-001：结果页列表容器与卡片 CSS

**现状**：
- 列表容器 `#discoveryResultsList`（[index.html#L1162](file:///d:/项目/boss/webui/index.html#L1162)）是一个裸 `div`，无 grid/flex 布局。
- 卡片 `.discovery-job-card`（[index.html#L958-L963](file:///d:/项目/boss/webui/index.html#L958)）使用 `display: flex; flex-direction: column; margin-bottom: 10px`，竖向堆叠。
- 卡片渲染函数 `createDiscoveryCard`（[index.html#L4358](file:///d:/项目/boss/webui/index.html#L4358)）构建结构：header（标题+公司+薪资）→ meta（地点+经验+学历）→ badges → completeness → JD（截断 200 字）→ source → sort → explanation → gaps → actions。

**决策**：
- 将 `#discoveryResultsList` 改为 `display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; align-items: start`。`align-items: start` 让每张卡片按自身内容高度展示，不强制等高。
- 卡片去掉 `margin-bottom`，改为 grid 自动间距。
- `createDiscoveryCard` 精简为三区：header（标题+薪资+条件）→ JD（完整）→ actions（按钮）。删除 badges、completeness、source、sort、explanation、gaps、company 的渲染代码。

**理由**：用户线框图明确只有三区。`auto-fill + minmax` 是 CSS Grid 自适应列数的标准方案。`align-items: start` 让卡片高度跟随 JD 内容自然变化，不设固定高度。

### R-002：JD 展示方式

**现状**：[index.html#L4427-L4431](file:///d:/项目/boss/webui/index.html#L4427) 中 `jdText.length > 200 ? jdText.slice(0, 200) + "…" : jdText`，截断到 200 字。

**决策**：去掉截断，完整渲染 JD。不设 `max-height`，不设 `overflow-y`。JD 区域高度由内容自然决定，卡片高度随之自适应。Grid 用 `align-items: start` 避免同行卡片被拉伸到等高。

**理由**：用户明确要求"自适应"，不要固定高度。`job.jd` 字段已包含完整内容，无需额外请求。

### R-003：死元素 DOM 位置

**现状**：
- `discoveryResultsHeader`（含 `discoveryDirectionSelector` + `discoveryCategoryFilter`）：[index.html#L1150-L1151](file:///d:/项目/boss/webui/index.html#L1150) 附近。
- `discovery-feedback-tools`：[index.html#L1152-L1160](file:///d:/项目/boss/webui/index.html#L1152)。
- JS 中 [index.html#L3788-L3790](file:///d:/项目/boss/webui/index.html#L3788) 尝试 `legacyHeader.hidden = true`，但 CSS `display: flex` 覆盖了 `hidden` 属性。

**决策**：直接从 DOM 中删除这两个元素块。同时删除 JS 中 `legacyHeader.hidden = true` 的逻辑（已无目标元素）。

**理由**：用户明确要求删掉而非隐藏。删除 DOM 比加 `!important` CSS 更干净，不会有残留。

### R-004：横条与按钮位置

**现状**：
- `discovery-topbar`（含「简历驱动发现」eyebrow + 「上传简历 · 确认方向 · 发现岗位」标题 + 「重新上传」按钮）：[index.html#L1066-L1074](file:///d:/项目/boss/webui/index.html#L1066)。
- `header-actions`（含「AI 设置」按钮 + 连接状态）：[index.html#L1053-L1056](file:///d:/项目/boss/webui/index.html#L1053)。

**决策**：删除整个 `discovery-topbar` 块。在 `header-actions` 中「AI 设置」按钮前插入「重新上传」按钮（`onclick="switchToDiscovery('upload')"` 保留）。

**理由**：用户要求横条整个去掉，只保留「重新上传」按钮并挪到顶部与「AI 设置」并排。

### R-005：刷新恢复 bug

**现状**：
- `saveDiscoveryRun(runId)`（[index.html#L2922](file:///d:/项目/boss/webui/index.html#L2922)）在运行启动时将 run_id 存入 localStorage。
- `restoreDiscoveryRun()`（[index.html#L4713](file:///d:/项目/boss/webui/index.html#L4713)）从 localStorage 读取 run_id → 调后端 API → 恢复到结果视图 → 渲染卡片。**函数已完整实现。**
- `init()`（[index.html#L1631-L1642](file:///d:/项目/boss/webui/index.html#L1631)）调了 `restoreSearchRun()`、`restoreScreeningRun()`、`loadLatestPipelineResult()`，但**没调 `restoreDiscoveryRun()`**。
- `loadLatestPipelineResult()`（[index.html#L3828-L3841](file:///d:/项目/boss/webui/index.html#L3828)）只把 jobs 存进变量 + 点亮步骤指示器，不渲染卡片、不切视图。

**决策**：在 `init()` 中 `loadProfiles()` 之后（`restoreDiscoveryRun` 依赖 `currentProfileId`）加 `await restoreDiscoveryRun()`。保留 `loadLatestPipelineResult()` 不动（它做的是点亮步骤指示器，与 `restoreDiscoveryRun` 不冲突）。

**理由**：`restoreDiscoveryRun` 已完整实现且经过 005 的测试，只是漏接了 init。`loadLatestPipelineResult` 负责的是另一件事（步骤指示器），两者不互斥。

### R-006：后端能力确认

**反馈端点**：`POST /api/discovery/feedback`（[app.py#L2634](file:///d:/项目/boss/webui/app.py#L2634)）已存在，支持 `action: "interested" / "not_interested"`。前端 `markDiscoveryReject`（[index.html#L4593](file:///d:/项目/boss/webui/index.html#L4593)）和取消感兴趣逻辑已接入。

**桥接机制**：`_bridge_discovery_feedback_to_legacy`（[store.py#L4181](file:///d:/项目/boss/webui/store.py#L4181)）在 `create_discovery_feedback` 内部自动调用，将 job-level 反馈写入 `profile_jobs` 表（`status='interested'` 或 `status='deleted'`）。筛选工作台通过 `list_screening_interested`（[store.py#L1767](file:///d:/项目/boss/webui/store.py#L1767)）读取同一张表。

**结论**：反馈→持久化→工作台联动链路已完整打通，本次改动不需要新增后端端点。

### R-007：标灰保留现有逻辑

**现状**：`markDiscoveryReject`（[index.html#L4593](file:///d:/项目/boss/webui/index.html#L4593)）调用后端写入 `not_interested`。卡片渲染时 [index.html#L4522-L4528](file:///d:/项目/boss/webui/index.html#L4522) 检查 `activeFeedback.action === "not_interested"` 并显示「恢复」按钮（`restoreDiscoveryJob`）。

**决策**：在 `createDiscoveryCard` 中，当岗位已标记 `not_interested` 时给 card 元素加 CSS class `marked-rejected`。加 CSS `.discovery-job-card.marked-rejected { opacity: 0.5; filter: grayscale(0.6); }`。

**理由**：视觉反馈通过 CSS class 实现，不改变现有 JS 逻辑结构。恢复按钮已存在，不需要新增。
