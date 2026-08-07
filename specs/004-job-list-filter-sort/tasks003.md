# Task 003：筛选/排序组件与 JobWorkspace 集成

**所属 Wave**：3（依赖 Wave 2） | **用户故事**：US1/US2/US3/US5（筛选、排序、边界、状态生命周期）

## 必读文件

- `specs/004-job-list-filter-sort/spec.md`（US1/2/3/5 验收场景）、`contracts/filter-sort.md`（§5/§6 交互与空态合同）
- `webui/src/components/JobWorkspace.vue`（集成对象；`job-list-heading` 结构、`visibleJobs`、空态 `empty-panel`、`heading-actions` slot）
- `webui/src/listFilter.ts`（Wave 2 产物，本包依赖）
- `webui/src/views/DiscoveryView.vue`（只读：`filter-groups` 的 `choice-chip` sentinel 交互先例）
- `webui/src/styles/theme.css`（深色变量：`--panel`/`--hair`/`--shadow`）、`webui/src/styles.css`（既有 `icon-button` 等类）
- `webui/src/components/__tests__/JobWorkspace.spec.ts`（回归基线）

## 写入范围（互斥）

`webui/src/components/JobFilterPanel.vue`（新建）、`JobSortMenu.vue`（新建）、`JobListToolbar.vue`（新建）、`JobWorkspace.vue`（改）、`webui/src/views/DiscoveryView.vue`（**仅**结果加载完成处递增 `resultEpoch` 一行，见 T004）、`webui/src/styles.css`（改）、`webui/src/components/__tests__/`（新增组件测试）。**禁止**修改 `types.ts`（如确需类型扩展须先更新合同）。

## 原子清单

- [ ] T001 实现 `JobFilterPanel.vue`：按 `FILTER_GROUPS` 渲染组与选项（`choice-chip` 风格）；sentinel 互斥（点非 sentinel 自动取消 sentinel，反之亦然）；草稿状态（确定才提交）；底部「重置」「确定」；福利组下方灰字「智联岗位暂不支持福利筛选」；emit `apply` / `reset` / `cancel`
- [ ] T002 实现 `JobSortMenu.vue`：按 `SORT_OPTIONS` 渲染单选列表；当前项高亮；`disabled` 项置灰 + title「数据补齐后开放」；点选 emit `select(key)` 并关闭
- [ ] T003 实现 `JobListToolbar.vue`：筛选/排序两个图标按钮（`SlidersHorizontal`/`ArrowUpDown`）；筛选按钮徽标（非 sentinel 选中数，0 不显示）；浮层锚定按钮定位、开合互斥（开一关一）、点击外部与 ESC 关闭
- [ ] T004 `JobWorkspace.vue` 集成：`visibleJobs` 改为 `sortJobs(filterJobs(props.jobs, state), sortKey).slice(0, visibleCount)`；新增内部状态 `filterState`/`sortKey` 与 `resultEpoch?: number` prop；`watch(resultEpoch)` 重置筛选/排序/`visibleCount`（**禁止**用 `props.jobs` 内容变化判断重置——切分类/切平台同样改变 jobs，违反 D3）；筛选/排序变化时 `visibleCount` 重置为 `batchSize` 且未手动选择时 `localSelectedId` 跟随过滤排序后第一项（已选则保持、被过滤掉回退第一项）；哨兵/observer 逻辑不动
- [ ] T004a `DiscoveryView.vue`：结果加载完成处（`pipelineResult` 被新 run 结果替换的赋值点）`resultEpoch++` 并传入 `JobWorkspace`；**仅此一处改动**，不改任何数据流与计数逻辑
- [ ] T005 筛选空态：过滤后为空且原列表非空 → 显示「没有符合条件的岗位」+「清除筛选」按钮（清空 `filterState`）；原列表为空维持既有空态（不显示清除按钮）
- [ ] T006 样式：浮层深色（`--panel` 底 + `--hair` 边 + `--shadow`）、窄屏（≤390px）按钮退化为纯图标保留徽标、浮层无 `overflow` 裁剪（验证 `.job-list-pane`/`heading` 无 hidden 裁剪）
- [ ] T007 组件测试：面板开合与互斥、徽标计数、重置/确定、排序点选与置灰不可点、筛选生效与空态、状态生命周期（切分类/切平台保留、`resultEpoch` 递增重置）、排序后未手动选择的选中跟随、窄屏退化
- [ ] T008 回归：既有 `JobWorkspace.spec.ts`/`DiscoveryView.spec.ts` 零回归（tab 计数不变是关键回归点）；vitest 全绿
- [ ] T009 前端构建同步：`npm run build`（pre-push 检查前端 dist 与 src 同步），如仓库规则要求提交 `webui/dist`
- [ ] T010 提交：仅本包文件，信息 `feat: add job list filter and sort toolbar UI`

## 完成定义

spec US1/2/3/5 验收场景在组件测试中全部覆盖并全绿；既有测试零回归；构建同步检查通过；卫生测试通过。

## 提交纪律

只暂存本包文件；commit email `czyooutzilas@gmail.com`；提交前 `git diff --check` 与 `git status --short`。