# Tasks: 发现结果页卡片网格化与体验修复

**Feature**: 006-discovery-card-grid  
**Date**: 2026-07-22  
**Spec**: [spec.md](file:///d:/项目/boss/specs/006-discovery-card-grid/spec.md)  
**Plan**: [plan.md](file:///d:/项目/boss/specs/006-discovery-card-grid/plan.md)

所有改动限于 `webui/index.html` 单文件。

## Phase 1: Foundational（刷新恢复 + 顶部清理）

这些任务相互独立，可并行执行。

- [ ] T001 [P] [US3] 在 `init()` 函数中 `loadProfiles()` 之后添加 `await restoreDiscoveryRun()` 调用，修复刷新丢失结果的问题。文件：`webui/index.html`（[L1631-L1642](file:///d:/项目/boss/webui/index.html#L1631) 附近）
- [ ] T002 [P] [US2] 删除 `discovery-topbar` 整块 DOM（含「简历驱动发现」eyebrow、「上传简历 · 确认方向 · 发现岗位」标题、「重新上传」按钮）。文件：`webui/index.html`（[L1066-L1074](file:///d:/项目/boss/webui/index.html#L1066)）
- [ ] T003 [P] [US2] 在 `header-actions` 中「AI 设置」按钮前插入「重新上传」按钮（`<button class="btn" type="button" onclick="switchToDiscovery('upload')">重新上传</button>`）。文件：`webui/index.html`（[L1053-L1056](file:///d:/项目/boss/webui/index.html#L1053)）
- [ ] T004 [P] [US2] 删除 `discoveryResultsHeader` 整块 DOM（含 `discoveryDirectionSelector` 和 `discoveryCategoryFilter`）。文件：`webui/index.html`（[L1150-L1151](file:///d:/项目/boss/webui/index.html#L1150) 附近）
- [ ] T005 [P] [US2] 删除 `discovery-feedback-tools` 整块 DOM（含方向反馈 label、select、原因 select、提交按钮）。文件：`webui/index.html`（[L1152-L1160](file:///d:/项目/boss/webui/index.html#L1152)）
- [ ] T006 [P] [US2] 删除 JS 中 `legacyHeader.hidden = true` 逻辑（目标元素已不存在）。文件：`webui/index.html`（[L3788-L3790](file:///d:/项目/boss/webui/index.html#L3788)）

**Phase 1 验证**：刷新页面后结果能恢复；顶部无横条、无死元素、无空白间隙；「重新上传」按钮在「AI 设置」左侧可点击。

## Phase 2: US1 — 卡片网格布局

重构卡片为三区结构，改为网格布局。此阶段任务有依赖关系，按顺序执行。

- [ ] T007 [US1] 在 CSS 中为 `#discoveryResultsList` 添加 grid 布局：`display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; align-items: start;`。文件：`webui/index.html`（CSS 区，`#discoveryResultsList` 目前无布局规则，需新增）
- [ ] T008 [US1] 修改 `.discovery-job-card` CSS：去掉 `margin-bottom: 10px`，保留 `display: flex; flex-direction: column; gap: 8px`。文件：`webui/index.html`（[L958-L963](file:///d:/项目/boss/webui/index.html#L958)）
- [ ] T009 [US1] 重写 `createDiscoveryCard` 函数，精简为三区结构。删除以下区块的渲染代码：badges（[L4400-L4419](file:///d:/项目/boss/webui/index.html#L4400)）、completeness（[L4420-L4424](file:///d:/项目/boss/webui/index.html#L4420)）、source（[L4434-L4451](file:///d:/项目/boss/webui/index.html#L4434)）、sort（[L4452-L4461](file:///d:/项目/boss/webui/index.html#L4452)）、explanation+gaps（[L4462-L4510](file:///d:/项目/boss/webui/index.html#L4462) 附近）、company（[L4372-L4375](file:///d:/项目/boss/webui/index.html#L4372)）。保留：header（标题+薪资）、meta（地点+经验+学历）、JD、actions。文件：`webui/index.html`（[L4358](file:///d:/项目/boss/webui/index.html#L4358) 起）
- [ ] T010 [US1] 修改 JD 渲染逻辑：去掉 200 字截断（`jdText.length > 200 ? jdText.slice(0, 200) + "…" : jdText` 改为直接 `jd.textContent = jdText`），不设 max-height 和 overflow-y。文件：`webui/index.html`（[L4427-L4431](file:///d:/项目/boss/webui/index.html#L4427)）

**Phase 2 验证**：结果页岗位以网格排列，桌面宽度每行 3~4 张；卡片只有三区（标题+薪资+条件 / 完整JD / 按钮）；JD 完整展示无截断；拖动窗口列数自适应。

## Phase 3: US3 — 卡片交互

岗位名跳转、标灰保留、按钮状态。依赖 Phase 2 的卡片结构。

- [ ] T011 [P] [US3] 将卡片中岗位名称元素改为可点击链接，点击调用 `openDiscoveryJobLink(event, job.source_url)` 在新标签打开 BOSS 直聘。复用现有函数（[L4741](file:///d:/项目/boss/webui/index.html#L4741)）。文件：`webui/index.html`（`createDiscoveryCard` 中 title 元素，[L4368-L4370](file:///d:/项目/boss/webui/index.html#L4368)）
- [ ] T012 [P] [US3] 添加 CSS 规则：`.discovery-job-card.marked-rejected { opacity: 0.5; filter: grayscale(0.6); }`。文件：`webui/index.html`（CSS 区，`.discovery-job-card-actions` 规则附近，[L1002](file:///d:/项目/boss/webui/index.html#L1002) 后）
- [ ] T013 [US3] 在 `createDiscoveryCard` 函数中，当岗位已标记 `not_interested` 时给 card 元素添加 `marked-rejected` class。检查 `activeFeedback.action === "not_interested"` 逻辑（现有逻辑在 [L4522-L4528](file:///d:/项目/boss/webui/index.html#L4522)，需在卡片创建时同步判断）。文件：`webui/index.html`

**Phase 3 验证**：点岗位名 → 新标签打开 BOSS；点不感兴趣 → 卡片标灰 + 出现恢复按钮 → 点恢复 → 卡片恢复正常；点感兴趣 → 按钮变「已感兴趣」→ 筛选工作台出现该岗位。

## Phase 4: Polish

- [ ] T014 运行 `python -m py_compile scripts/boss_cdp_raw.py` 确认无语法错误
- [ ] T015 运行 `python -m unittest tests.test_chrome_setup` 确认无回归
- [ ] T016 按 [quickstart.md](file:///d:/项目/boss/specs/006-discovery-card-grid/quickstart.md) V1~V7 场景手动验证

## 依赖关系

```
Phase 1 (T001~T006): 全部独立，可并行
    ↓
Phase 2 (T007→T008→T009→T010): 顺序执行，T009 依赖 T008 的 CSS
    ↓
Phase 3 (T011, T012 可并行; T013 依赖 T009 的卡片结构)
    ↓
Phase 4 (T014→T015→T016): 顺序执行
```

## 并行机会

- Phase 1：T001~T006 全部可并行（不同代码区域，互不影响）
- Phase 3：T011 和 T012 可并行（JS 事件 vs CSS 规则）

## MVP 范围

MVP = Phase 1 + Phase 2（T001~T010）。完成后结果页即可用：网格布局、完整 JD、顶部干净、刷新恢复。Phase 3 的交互优化可在 MVP 验证后追加。
