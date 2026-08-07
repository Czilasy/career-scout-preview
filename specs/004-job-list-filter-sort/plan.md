# 实施计划：岗位列表筛选与排序

**功能目录**：`004-job-list-filter-sort` | **创建日期**：2026-08-07

## 目标与约束

在结果页岗位列表顶部新增「筛选」「排序」悬浮弹窗能力，收窄当前分类列表显示范围、调整排列顺序；BOSS 福利数据顺带补齐为 `extra.welfare_list`。筛选/排序为 `JobWorkspace` 内部能力，不改动父组件 `DiscoveryView.vue` 的数据流与 tab 计数语义。

## 阶段划分

### Wave 1 — 后端 welfare 补齐（并行独立）

**依赖**：无。**产出**：`extra.welfare_list` 进入结果快照与 API 岗位对象。

- `webui/source.py`（或 `pipeline_exec.py` 展示快照组装处）：BOSS 列表岗位的 `welfare`（「五险一金 | 双休」）拆为 `string[]` 写入 `extra["welfare_list"]`；缺失/为空不写入
- `specs/001-add-zhilian-platform/contracts/http-api.md`：岗位对象合同 `extra` 补充 `welfare_list` 说明（BOSS 专属，智联无此键）
- 测试：归一化/持久化链路单测（`tests/` 既有 workbench 测试体系）

**完成定义**：BOSS 岗位结果快照含 `welfare_list`；智联与旧结果不含；不报错、不编造。

### Wave 2 — 前端纯函数层（并行独立）

**依赖**：无（可先于 Wave 1 完成，仅读 `JobItem` 字段）。**产出**：`webui/src/listFilter.ts` + 单测。

- 薪资解析（§3 合同）、档位判定、经验/学历/福利匹配谓词、`filterJobs`、`sortJobs`（稳定排序、面议沉底）
- 组配置常量（`FILTER_GROUPS`、`SORT_OPTIONS`），置灰项配置位预留
- 测试：`webui/src/__tests__/listFilter.spec.ts`（合同 §2/§3/§4 逐条覆盖）

**完成定义**：纯函数单测全绿，覆盖合同全部匹配规则与排序边界。

### Wave 3 — 组件与集成（依赖 Wave 2）

**依赖**：Wave 2。**产出**：筛选/排序 UI + JobWorkspace 集成。

- `JobFilterPanel.vue`（筛选面板：组渲染、sentinel 互斥、草稿、重置/确定、福利提示）
- `JobSortMenu.vue`（排序菜单：单选、置灰项）
- `JobListToolbar.vue`（按钮 + 徽标 + 浮层定位/开合/外部点击/ESC/互斥）
- `JobWorkspace.vue` 集成：管道接入、`visibleCount` 重置、选中跟随、筛选空态、「清除筛选」
- `DiscoveryView.vue`：结果加载完成处递增 `resultEpoch`（仅此一处，见合同 §6）
- `webui/src/styles.css`：浮层样式（深色变量），窄屏退化为纯图标
- 测试：组件测试（开合、徽标计数、互斥、空态、状态生命周期）

**完成定义**：spec 用户故事 1/2/3/5 验收场景在组件测试中全部覆盖；既有 `JobWorkspace.spec.ts` / `DiscoveryView.spec.ts` 零回归。

### Wave 4 — 联调与验收

- 后端产物与前端 `welfare` 组联调（BOSS 真实/构造数据）
- 窄屏（390px）与最小窗口布局检查；浮层无裁剪
- 全量回归：`uv run python -m unittest`（后端）+ 前端 vitest + `npm run build`（pre-push 前端构建同步检查）

**完成定义**：spec 全部验收场景可执行；卫生测试通过。

## 提交计划（Conventional Commits）

1. `feat: persist boss welfare into job extra`（Wave 1：source + 合同 + 测试）
2. `feat: add job list filter and sort pure functions`（Wave 2）
3. `feat: add job list filter/sort toolbar UI`（Wave 3）
4. `chore: bump frontend dist`（若构建产物需同步，由 ensure_frontend_sync 规则驱动）

每次提交前：`uv run python -m unittest tests.test_repo_hygiene` + `git diff --cached` 审查；提交身份 `czyooutzilas@gmail.com`。