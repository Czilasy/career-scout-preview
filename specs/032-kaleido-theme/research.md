# Research: 万花筒彩蛋主题模块

**Date**: 2026-08-30 | **Status**: 全部决议完成，无遗留 NEEDS CLARIFICATION

## D1 主题接入机制：复用 `data-theme` 属性

- **Decision**: 万花筒作为 `data-theme` 的第三合法值（`kaleido`），不改用独立属性或叠加层。
- **Rationale**: `useTheme.ts` 与 `styles/theme.css` 全部键在 `<html data-theme>` 上；弹层、持久化、平台正交逻辑均零改动即可承接第三值。独立属性会造成双真相源。
- **Alternatives**: 独立 `data-kaleido` 叠加属性（拒绝：亮/暗与彩蛋主题互斥关系需要额外状态机）；替换整个主题系统（拒绝：越权重构）。

## D2 模块边界：样式全走模块文件，`theme.css` 零改动

- **Decision**: `kaleido.css` 内以 `[data-theme="kaleido"]` 作用域自带令牌；亮/暗令牌文件 `styles/theme.css` 不动。
- **Rationale**: ①宪法 I/II——不向既有文件追加；②可插拔——删模块目录即卸载主题；③零回归——亮暗令牌物理隔离。令牌降级基座：kaleido 令牌以暗色令牌值为底（未覆盖界面自动获得暗色观感），再叠主题覆盖层。
- **Alternatives**: 在 theme.css 加第五套令牌（拒绝：破坏亮暗零回归保证，模块不可插拔）。

## D3 光场组件挂载点：App.vue 根部单实例

- **Decision**: `KaleidoField.vue` 在 `App.vue` 根部挂载一次（`v-if="mode === 'kaleido'"`），fixed 定位衬于内容层之下。
- **Rationale**: 设计定稿中光场是"整站背景层"，跨四页持续存在；单实例避免每页重复挂载与动画重启。App.vue 是组合根，挂载点改动约 3 行。
- **Alternatives**: 每个 view 各自挂载（拒绝：四份重复、切换页面动画重启）。

## D4 弹层选项：填充既有占位 + 抽出选项组件

- **Decision**: `App.vue` 的 `theme-picker` 占位注释替换为 `<ThemePickerOptions>` 组件（themes 容器所有），App.vue 仅增 import＋组件标签。
- **Rationale**: App.vue 816 行逼近宪法 900 预警线；选项列表（三行标本＋当前态）约 100 行，必须走组件才守得住行数门禁。选项行样式追加于 `styles.css` 的 `.theme-picker` 既有块附近（亮暗主题下弹层也要正常显示，属既有职责块）。
- **Alternatives**: 直接在 App.vue 内联三行选项（拒绝：破预警线）。

## D5 持久化链路：三处值域校验同步扩展

- **Decision**: `ThemeMode = "light" | "dark" | "kaleido"`；同步扩展三处校验——`useTheme.ts` 的 `initFromStorage` 与 `loadFromBackend`、后端 `/api/theme` GET/PUT 的 `mode in (...)` 元组。
- **Rationale**: 已核实三处各自硬编码 `light|dark`（useTheme.ts L42/L69-71、version_update_api.py L135/L144）；漏一处则该链路会把 kaleido 静默回落 light。后端 GET 回读校验也要放行，否则重启后主题被打回。
- **Alternatives**: 后端不做校验直接透传（拒绝：违反既有防御风格，脏数据落盘）。

## D6 换肤转场：光轮睁眼版，碎裂否决

- **Decision**: 首次进入＝模块挂载自带一次性入场动画（暗幕 scrim 淡落→光轮旋开→瞳孔睁开，0.7~1s，会话内 sessionStorage 标记只播一次）；退出与后续切换＝0.2s 过渡。转场期间 `pointer-events: none` 于覆盖层。
- **Rationale**: 入场动画复用光场组件既有素材（自转/睁眼），近乎零额外成本；碎裂需对真实 DOM 快照切片，工程贵、性能险、真实列表页易翻车（用户已拍板"轻量版，碎裂不做"）。
- **Alternatives**: DOM 快照碎片化转场（否决，见上）；无转场硬切（拒绝：丢失彩蛋仪式感，用户已确认要轻量版）。

## D7 未覆盖界面降级：暗色令牌底座

- **Decision**: `kaleido.css` 令牌块以 `theme.css` 暗色令牌值为底色复制（弹窗/抽屉/通知等未主题化组件使用共享令牌，自动获得暗色观感），主题覆盖层只作用于四页主视觉与共享 chrome。
- **Rationale**: 未覆盖组件全部消费令牌变量，令牌底座即兜底；不逐组件打补丁，不破版。
- **Alternatives**: 逐组件补万花筒样式（拒绝：范围失控，spec 已明确降级策略）。

## D8 字体：系统字体栈，不打包

- **Decision**: 标题衬线走系统栈（`"Noto Serif SC", "Source Han Serif SC", "Songti SC", SimSun, serif`），正文沿用现有无衬线令牌；不打包字体文件。
- **Rationale**: 离线桌面环境；Windows 自带宋体系可呈现铭牌气质；打包字体属增量资产，视觉对照若发现缺口再立项。
- **Alternatives**: 随包分发 Noto Serif SC（拒绝：本批次不引入资产，留待视觉验收后决定）。

## D9 动效性能：合成层属性 + 静态降级

- **Decision**: 全部动画限定 transform/opacity/filter；动画元素 `will-change`；常驻动效挂于固定光场层与 chrome 元素，禁止逐行动画；`prefers-reduced-motion` 下动画全关。
- **Rationale**: 与设计定稿实现完全一致（已在 page1-4.html 验证 60fps）；列表大数据不参与动画层。
- **Alternatives**: IntersectionObserver 逐行动效（拒绝：范围外且有性能险）。

## 附：现有实现事实核查（已读源码）

- `useTheme.ts` L18 `ThemeMode = "light" | "dark"`；L42 后端回读校验；L69-71 localStorage 校验；L84-96 toggle（`light↔dark`，kaleido 下自然落 light）
- `App.vue` L295-333 普通点击 ripple＋长按蓄力（`THEME_CHARGE_MS = 1000`，抖动/发光契约，reduced-motion 去抖动）；L663-667 `theme-picker` 占位注释「彩蛋主题列表下一阶段填充」
- `version_update_api.py` L129-155 `/api/theme` GET/PUT，两处 `mode in ("light","dark")` 校验
- `styles.css` L2910+ `.theme-toggle.charging`、`.theme-picker` 及过渡样式已存在
- `styles/theme.css` 311 行令牌系统，`data-theme`×`data-platform` 四态
