# Implementation Plan: 万花筒彩蛋主题模块

**Branch**: `032-kaleido-theme` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/032-kaleido-theme/spec.md`

## Summary

将 `design/kaleido/page1-4.html` 定稿的万花筒彩蛋主题实现为独立前端主题模块：新增主题容器 `webui/src/themes/`（注册口＋万花筒子模块），经既有 `data-theme`/`useTheme` 机制接入；现有主题长按弹层占位处填充「亮/暗/万花筒」选项；持久化链路（localStorage＋`/api/theme`）扩展 `kaleido` 合法值；首次进入播放轻量转场（暗幕＋光轮旋开＋瞳孔睁开，无碎裂）；`prefers-reduced-motion` 全量静态降级；设计稿未覆盖界面以暗色令牌降级。四个主流程页按钮/输入/流程逻辑零改动。

## Technical Context

**Language/Version**: Python 3.11+（后端路由域）、TypeScript + Vue 3 `<script setup>`（前端）、CSS（模块样式）

**Primary Dependencies**: 现有 `useTheme` composable（`data-theme` 属性机制）、`styles.css` 内 `.theme-toggle/.theme-picker/.theme-ripple` 既有样式与长按蓄力实现、后端 `webui/version_update_api.py` 的 `/api/theme` 路由

**Storage**: 主题偏好沿用既有双链路：localStorage（`career-scout-theme-mode`）＋后端主题文件（经 `ctx._theme_path()`），不新增存储

**Testing**: 后端 unittest（`tests/`）、前端 Vitest（`webui/src/**/__tests__/`）、`npm run build`

**Target Platform**: Career Scout 桌面应用内嵌 WebView（Windows 优先），离线环境

**Performance Goals**: 动效仅合成层属性（transform/opacity/filter）；主题启用不引入每帧布局；列表大数据量无逐行动画

**Constraints**: 离线可用（不引入网络字体/CDN）；亮/暗主题零回归；`App.vue` 改动后 ≤900 行（宪法预警线）

**Scale/Scope**: 四个主流程页换肤＋一个入口弹层＋一个模块目录；后端两处校验扩展

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 结论 |
|---|---|
| I 职责分层 | ✅ 主题为新域，全部落新文件；不向巨石文件追加主题实现（picker 选项行样式就近追加于 `.theme-picker` 既有职责块） |
| II 单文件尺寸 | ✅ 新文件均 <800/1200；`App.vue` 816 行＋≤40 行 < 900 预警线（超出即把选项列表下沉组件，已预案） |
| III 引用方向 | ✅ `App.vue → themes/registry → kaleido 模块`，单向；`useTheme` 不依赖 themes 模块（仅值域扩展） |
| IV 拆分纪律 | ✅ 不混入任何重构；不触碰既有拆分批次文件 |
| V 验证门禁 | ✅ 前端测试＋`npm run build`＋卫生检查；后端聚焦测试（theme 路由）＋全量按门禁 |
| VI 模块地图 | ✅ 新域新文件，落地批次内登记进宪法「模块地图」小节（themes/ 全部文件） |

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`.*

**放置清单已获用户确认**：模块容器＋子主题架构、长按弹层入口、先模块后切换的顺序，均为用户 2026-08-30 设计阶段拍板（`design/kaleido/direction-approved.md` 工程形态决议），无需二次停等。

- **Allowed files**:
  - 新增 `webui/src/themes/`（见 New files）
  - 修改 `webui/src/composables/useTheme.ts`（mode 值域与两处校验扩展）
  - 修改 `webui/src/App.vue`（import＋挂载光场组件＋占位处替换为选项组件，≤40 行）
  - 修改 `webui/src/styles.css`（`.theme-picker` 既有块附近追加选项行样式，≤50 行）
  - 修改 `webui/version_update_api.py`（GET/PUT 两处 mode 校验元组加 `kaleido`，±4 行）
  - 修改/新增测试：`webui/src/composables/__tests__/useTheme.spec.ts`、`tests/`（theme 路由聚焦用例）、`webui/src/themes/__tests__/registry.spec.ts`
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`webui/src/styles/theme.css`（亮暗令牌不动）、`webui/src/views/*`（零逻辑零结构改动，主题仅经模块样式与光场组件作用）
- **New files**:
  - `webui/src/themes/registry.ts` — 主题注册口：登记 light/dark/kaleido 三态与模块入口（约 60 行）
  - `webui/src/themes/ThemePickerOptions.vue` — 长按弹层选项列表：三行主题标本＋当前态标识（约 100 行）
  - `webui/src/themes/kaleido/kaleido.css` — 万花筒主题样式：`[data-theme="kaleido"]` 令牌（暗色令牌降级基座）＋四页切面/光谱/流动层（约 800 行）
  - `webui/src/themes/kaleido/KaleidoField.vue` — 光场组件：三层光轮＋碎玻璃＋暗角＋注视之眼 SVG（约 280 行）
  - `webui/src/themes/kaleido/useKaleidoMotion.ts` — 转筒/瞳孔追踪/苏醒/逃生舱/首启转场（约 130 行）
  - `webui/src/themes/__tests__/registry.spec.ts` — 注册口与模式切换聚焦测试（约 80 行）
- **Reference direction**: `App.vue → themes/registry → kaleido 模块`；`useTheme` 仅扩值域、不 import themes；模块不反向依赖 App/views；后端 `version_update_api.py` 为既有路由域，仅扩校验值
- **Line gate**: `App.vue` ≤900（预警线，超出则选项列表必须走组件——已预案）；全部新文件在 Python/Vue 红线内
- **Rationale**: 用户拍板的模块架构（彩蛋可整体插拔）；宪法 I/II/VI——新域新文件、禁入门面；主题样式不进 `theme.css` 以保证亮暗零回归与模块可插拔

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试（useTheme/registry/theme 路由）、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务（版本提升、打包、提交、推送、Release）：不要求全量测试；按根 `AGENTS.md` 收口规则执行。
- 本 Spec 未要求收口阶段全量测试；实现批次按门禁执行。

## Project Structure

### Documentation (this feature)

```text
specs/032-kaleido-theme/
├── plan.md              # 本文件
├── research.md          # Phase 0 技术决策
├── data-model.md        # Phase 1 数据与状态
├── quickstart.md        # Phase 1 验证指南
├── contracts/           # Phase 1 接口契约（/api/theme）
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks 输出（下一阶段）
```

### Source Code (repository root)

```text
webui/
├── version_update_api.py          # [修改] /api/theme GET/PUT 校验值域 +kaleido
└── src/
    ├── composables/useTheme.ts    # [修改] ThemeMode +kaleido、两处校验、持久化放行
    ├── App.vue                    # [修改] 挂 KaleidoField、占位处换 ThemePickerOptions
    ├── styles.css                 # [修改] .theme-picker 块附近 +选项行样式
    └── themes/                    # [新增] 主题模块容器
        ├── registry.ts            # 主题注册口（light/dark/kaleido）
        ├── ThemePickerOptions.vue # 弹层选项列表组件
        ├── __tests__/registry.spec.ts
        └── kaleido/
            ├── kaleido.css        # 主题样式（令牌降级基座+四页视觉+流动层）
            ├── KaleidoField.vue   # 光场组件
            └── useKaleidoMotion.ts# 交互动效

tests/                             # [修改] theme 路由聚焦用例（+kaleido 合法值）
```

**Structure Decision**: 主题域全新落位 `webui/src/themes/`，符合宪法 VI「新领域开新文件并登记模块地图」；复用既有 `data-theme` 属性机制与 `/api/theme` 持久化链路，不新建存储、不新建路由。

## Complexity Tracking

> 无宪法违规需要豁免。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| （无） | — | — |
