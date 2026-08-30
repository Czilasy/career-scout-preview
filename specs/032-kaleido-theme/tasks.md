---
description: "Task list for 万花筒彩蛋主题模块 implementation"
---

# Tasks: 万花筒彩蛋主题模块

**Input**: Design documents from `/specs/032-kaleido-theme/`

**Prerequisites**: plan.md（含 File Boundaries）、spec.md、research.md、data-model.md、contracts/api-theme.md、quickstart.md

**Tests**: 含聚焦测试任务（useTheme/registry/theme 路由值域），用于守住宪法验证门禁；不做 TDD 全流程。

**Organization**: 按 User Story 分阶段；P1=US1（入口）、US2（四页视觉），P2=US3（动效）、US5（退出降级），P3=US4（转场）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 所属 User Story（US1~US5，对应 spec.md）
- 描述必须含确切文件路径

## Path Conventions

单仓应用：后端 `webui/*.py`（tests/ 在根）；前端 `webui/src/`。

## File Boundaries

- **Allowed files**: `webui/src/themes/**`（新增）、`webui/src/composables/useTheme.ts`、`webui/src/composables/__tests__/useTheme.spec.ts`、`webui/src/App.vue`（≤40 行增量）、`webui/src/styles.css`（.theme-picker 块附近 ≤50 行）、`webui/version_update_api.py`（两处校验元组）、`tests/`（theme 路由聚焦用例）、`.specify/memory/constitution.md`（仅「模块地图」小节登记）
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`webui/src/styles/theme.css`、`webui/src/views/**`、`webui/src/composables/useDiscovery*.ts`、其余全部既有模块
- **New files**: `webui/src/themes/registry.ts`、`webui/src/themes/ThemePickerOptions.vue`、`webui/src/themes/__tests__/registry.spec.ts`、`webui/src/themes/kaleido/kaleido.css`、`webui/src/themes/kaleido/KaleidoField.vue`、`webui/src/themes/kaleido/useKaleidoMotion.ts`
- **Reference direction**: `App.vue → themes/registry → kaleido 模块`；useTheme 仅值域扩展、不 import themes；模块不反向依赖 App/views
- **Line gate**: `App.vue` ≤900 行（超出立即把选项逻辑下沉组件）；新文件 Python ≤800 / Vue ≤1200

## Verification Gate (task-type aware)

- 本功能为功能交付：最终门禁＝聚焦测试（useTheme/registry/theme 路由）＋后端全量测试＋前端测试＋`npm run build`＋仓库卫生检查。
- 不生成收口类任务（打包/提交/Release 由用户单独授权）。

## Phase 1: Setup（模块骨架）

**Purpose**: 建立主题容器与模块文件占位，登记测试面

- [ ] T001 创建主题模块骨架：`webui/src/themes/registry.ts`（导出 `THEMES` 注册表：id/label/排序，light/dark/kaleido 三项）与空实现占位文件 `webui/src/themes/kaleido/kaleido.css`（仅文件头注释＋`[data-theme="kaleido"]` 空作用域块）
- [ ] T002 [P] 扩展 `webui/src/composables/useTheme.ts`：`ThemeMode` 增加 `"kaleido"`；`initFromStorage`（L69-71 一带）与 `loadFromBackend`（L42 一带）的值校验放行 `kaleido`；`toggleTheme` 逻辑不动（kaleido 态下普通点击自然落 light）
- [ ] T003 [P] 扩展后端 `webui/version_update_api.py`：`api_theme_get`（L135 一带）与 `api_theme_put`（L144 一带）的 `mode in (...)` 校验元组加入 `"kaleido"`；PUT 错误文案改为「mode 必须为 light、dark 或 kaleido」
- [ ] T004 [P] 扩展聚焦测试：`webui/src/composables/__tests__/useTheme.spec.ts` 增加 kaleido 用例（设值/读回/持久化键不变）；`tests/` 下 theme 路由聚焦用例增加 PUT kaleido 200、PUT bogus 400、GET 回读 kaleido

**Checkpoint**: mode 三值链路打通（手动把 localStorage 改成 kaleido 时属性能挂上），弹层尚无入口

---

## Phase 2: User Story 1 - 长按进入彩蛋并整站换肤 (Priority: P1) 🎯 MVP 前半

**Goal**: 长按弹层出现三个选项，点「万花筒」主题模式生效（此时视觉允许尚为空壳，US2 补齐）

**Independent Test**: 长按主题钮→弹层三项→点万花筒→`<html data-theme="kaleido">` 生效且持久化；普通点击明暗互切与现状一致

### Implementation for User Story 1

- [ ] T005 [US1] 实现 `webui/src/themes/ThemePickerOptions.vue`：三行选项（亮＝白玻璃切片标本／暗＝黑玻璃棱线标本／万花筒＝流动微缩 rosette SVG 标本），props 接收当前 mode，emit 选择事件；当前项菱形指针＋选中标识
- [ ] T006 [US1] 在 `webui/src/styles.css` 的 `.theme-picker` 既有块附近追加选项行样式（≤50 行：行布局、hover、选中态、标本块尺寸）
- [ ] T007 [US1] 修改 `webui/src/App.vue`：`theme-picker` 占位注释（约 L666）替换为 `<ThemePickerOptions>` 并接线（选择回调→`toggleTheme('kaleido')` / `toggleTheme('light'|'dark')`→收起弹层）；文件增量 ≤40 行
- [ ] T008 [US1] 手动验证 quickstart.md §1 前半：长按→弹层→点万花筒→`data-theme` 变为 kaleido 且重启保持（此时页面视觉为无样式空壳属预期）

**Checkpoint**: 入口链路全通，持久化三链路生效

---

## Phase 3: User Story 2 - 四页视觉与设计定稿一致 (Priority: P1) 🎯 MVP 后半

**Goal**: 万花筒主题下四页与 `design/kaleido/page1-4.html` 定稿逐页一致

**Independent Test**: 万花筒主题下逐页与定稿 HTML 并排对照（布局/文案/视觉三查），并操作全部原交互确认行为不变

### Implementation for User Story 2

- [ ] T009 [US2] 编写 `webui/src/themes/kaleido/kaleido.css` 令牌基座段：`[data-theme="kaleido"]` 作用域定义令牌（以 `styles/theme.css` 暗色令牌值为降级底座：背景/表面/文字/边框/品牌色变量全量映射），并加入字体栈声明（衬线标题系统栈，research D8）
- [ ] T010 [US2] 移植共享 chrome 视觉：顶栏（星形 logo、中央胶囊、五图标钮）、第二行（BOSS｜智联分段、四步流程器）的切面/光谱/等宽字样式入 `kaleido.css`（对照 page4.html 顶栏两段）
- [ ] T011 [P] [US2] 移植通用组件视觉：主按钮 `.lit/.ghost`、切面 `.cut` 体系（棱线/玻璃体双层 clip-path）、chip 徽章、输入框、滚动条光谱入 `kaleido.css`
- [ ] T012 [P] [US2] 移植四页专属视觉段入 `kaleido.css`（对照 page1-4.html 各自主区；选择器作用域限定 `[data-theme="kaleido"]` 下对应视图容器，不改 views 模板）：①上传页拖放区/清单、②抓取页双卡/档位、③筛选页条件组、④结果页双栏/判定 tab
- [ ] T013 [US2] 移植流动色层入 `kaleido.css`：edge-flow/fill-sheen/capglow/irishue 关键帧与全部流动规则（对照 page4.html「流动色层」段）
- [ ] T014 [US2] 实现 `webui/src/themes/kaleido/KaleidoField.vue`：整段光场 SVG（三层 12 重对称光轮＋碎玻璃粒子＋目镜暗角＋注视之眼含虹膜/眼白/眼神光）自 page4.html 移植，props 留空、fixed 铺满、pointer-events none
- [ ] T015 [US2] 修改 `webui/src/App.vue`：import `KaleidoField` 并在根部 `v-if="mode === 'kaleido'"` 挂载（约 3 行）；确认四页内容层位于光场之上
- [ ] T016 [US2] 逐页视觉对照验收（quickstart.md §6）：四页 vs 定稿 HTML 并排三查（布局/文案/视觉）；文字必须与现有 views 逐字一致——发现 views 文案与设计稿定稿有出入时，以现有 views 文字为准并在本任务记录偏差清单

**Checkpoint**: MVP 完成——长按进入万花筒，四页视觉与定稿一致，全部原交互可用；STOP AND VALIDATE（quickstart §1、§2 前半、§6）

---

## Phase 4: User Story 3 - 动效层与无障碍降级 (Priority: P2)

**Goal**: 常驻/交互动效按定稿运行；reduced-motion 全静态

**Independent Test**: 正常偏好下观察全部动效；系统开启"减少动态"后页面全静态可用

### Implementation for User Story 3

- [ ] T017 [US3] 实现 `webui/src/themes/kaleido/useKaleidoMotion.ts`：光标驱动瞳孔偏移与苏醒（距光核 210px 内 class 切换）、点击空白转筒（--spin/--turn 累计，弹性过冲）、logo 逃生舱（.calm 3 秒）、reduced-motion 探测短路；在 `KaleidoField.vue`/App 层接线
- [ ] T018 [US3] reduced-motion 全量审计：`kaleido.css` 补齐 `@media (prefers-reduced-motion: reduce)` 关停清单（光轮/轮回/漂移/变焦/流动色/转筒/瞳孔），确认静态呈现信息完整
- [ ] T019 [US3] 性能自查：全部动画仅 transform/opacity/filter＋will-change；无逐行动画；快速切换四页无动画重启抖动

**Checkpoint**: 动效与降级达标（quickstart §2）

---

## Phase 5: User Story 5 - 退出与未覆盖界面降级 (Priority: P2)

**Goal**: 随时切回亮/暗零残留；未主题化界面暗色降级不破版

**Independent Test**: 万花筒→切回暗/亮全站恢复；逐一打开设置弹窗/收藏抽屉/通知确认暗色可读可用

### Implementation for User Story 5

- [ ] T020 [US5] 退出路径验证与修补：长按选「暗/亮」后全站无 `[data-theme="kaleido"]` 残留样式（光场组件随 v-if 卸载）；普通点击明暗互切 ripple 表现不变
- [ ] T021 [US5] 降级走查：万花筒主题下逐一打开设置弹窗、收藏抽屉、历史抽屉、通知/toast、确认对话框，确认暗色令牌基座下可读可用；发现破版仅在 `kaleido.css` 内加窄域修补

**Checkpoint**: 主题可自由进出，全站无破版（quickstart §4）

---

## Phase 6: User Story 4 - 轻量换肤转场 (Priority: P3)

**Goal**: 首次进入有仪式（暗幕＋光轮旋开＋瞳孔睁开），后续从简，亮暗互切不变

**Independent Test**: 首进观察完整转场；再次切换观察简短过渡；亮暗互切确认 ripple 不变；转场中连点无叠加

### Implementation for User Story 4

- [ ] T022 [US4] 在 `useKaleidoMotion.ts` 实现首启转场：sessionStorage 标记首次进入播 0.7~1s 入场序列（暗幕 scrim 淡落→光场一次性快放旋开→瞳孔睁开），转场期间覆盖层 pointer-events none 忽略连点；后续切换 0.2s 过渡
- [ ] T023 [US4] 确认退出与亮暗互切路径不受转场影响（ripple 现状保留）；reduced-motion 下转场跳过为 0.2s 亮度过渡

**Checkpoint**: 转场达标且不影响既有切换（quickstart §1 步骤 4、§2）

---

## Phase 7: Polish & Cross-Cutting

**Purpose**: 门禁收口与登记

- [ ] T024 [P] 宪法「模块地图」登记：`.specify/memory/constitution.md` 追加 themes/ 五个新文件条目（路径＋一句话职责）
- [ ] T025 [P] 文档同步：README 或开发文档若提及主题/外观，补一句彩蛋主题入口说明（无则跳过）
- [ ] T026 运行完整验证门禁（quickstart.md §5）：聚焦测试（useTheme/registry/theme 路由）＋后端全量测试＋前端测试＋`npm run build`＋`uv run python -m unittest tests.test_repo_hygiene`
- [ ] T027 按 quickstart.md §1-§4 全流程人工复验（含与 page1-4.html 的最终并排对照），汇总偏差清单（应为空或已记录理由）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1（Setup）**：无依赖，立即可做；T002/T003/T004 与 T001 可并行
- **Phase 2（US1）**：依赖 Phase 1 全部完成（弹层选项需要三值链路）
- **Phase 3（US2）**：依赖 Phase 2（主题模式可生效才有换肤载体）；T009→T010/T011/T012/T013 可部分并行（同文件追加，建议顺序执行同文件段）；T014/T015 与 CSS 段并行
- **Phase 4（US3）**：依赖 Phase 3（光场组件与 CSS 就位）
- **Phase 5（US5）**：依赖 Phase 3；可与 Phase 4 并行
- **Phase 6（US4）**：依赖 Phase 4（动效基建）
- **Phase 7（Polish）**：依赖以上全部

### User Story Dependencies

- US1（入口）与 US2（视觉）合起来才是可演示 MVP；US1 先行时页面是空壳属预期
- US3/US5 相互独立，均建立在 US2 之上
- US4 最后做，依赖 US3 的动效基建

### Parallel Opportunities

- T002/T003/T004 三文件互不依赖，可并行
- T011/T012 与 T014 分属 CSS 与组件，可并行
- T024/T025 文档类可并行

---

## Implementation Strategy

### MVP First (Phase 1-3)

1. Phase 1 打通三值链路（含聚焦测试）
2. Phase 2 入口可用
3. Phase 3 四页视觉对齐定稿
4. **STOP AND VALIDATE**：quickstart §1/§6 逐页对照＋原交互全回归

### Incremental Delivery

- +Phase 4 动效 → +Phase 5 退出降级 → +Phase 6 转场 → Phase 7 门禁收口
- 每阶段独立可验证，随时可停在 checkpoint 向用户演示

---

## Notes

- **实施前停车点**：本 tasks.md 完成后需自审＋报告，经用户放行方可开始执行任何 T 任务（用户 2026-08-30 指令）
- 既有测试是回归底线：亮/暗相关用例一个都不许改语义，只允许扩值域用例
- 视觉争议一律以 `design/kaleido/page1-4.html` 为准（design/README 规则：图优先于代码）
- views 文案与设计稿定稿不一致时以 views 现有文案为准（FR-002"逐字一致"指主题不改文字，非改文字就主题）
