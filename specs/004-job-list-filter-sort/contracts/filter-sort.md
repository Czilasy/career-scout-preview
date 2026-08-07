# 合同：岗位列表筛选与排序（前端行为合同）

**所属功能**：`004-job-list-filter-sort` | **冻结日期**：2026-08-07

本文件是前端筛选/排序的唯一行为权威；实现与测试必须与之一一对应。后端 welfare 合同见 `http-api.md` 岗位对象补充（研究记录 1.3）。

## 1. 数据管道

```
props.jobs（父组件已按平台+分类过滤）
  → filterJobs(jobs, filterState)      // 条件筛选：组内 OR、组间 AND
  → sortJobs(jobs, sortKey)            // 排序：稳定排序
  → visibleJobs（slice(batchSize)，无限滚动不变）
```

- 管道为**纯函数**，位于 `webui/src/listFilter.ts`，无组件依赖、无副作用。
- 筛选/排序状态（`filterState`、`sortKey`）归 `JobWorkspace` 内部所有，不进父组件（D2：不联动 tab 计数）。
- 状态变更时 `visibleCount` 重置为 `batchSize`（见 §6 无限滚动）。

## 2. 筛选组配置

| key | label | 选项（value） | 匹配规则 |
|---|---|---|---|
| `salary` | 薪资范围 | `lt5` / `5-10` / `10-15` / `15up` | 解析 `salary` 文本区间（见 §3）判档；无 sentinel |
| `experience` | 工作经验 | `不限`(sentinel) / `应届生` / `1-3年` / `3-5年` / `5-10年` / `10年以上` | 文本包含匹配：`应届生`↔含「应届」；其余↔含精确档位串 |
| `degree` | 学历 | `不限`(sentinel) / `大专` / `本科` / `硕士` | 文本包含匹配（含「统招本科」类前缀串） |
| `welfare` | 其他条件 | `五险一金` / `双休`（数据补齐后按实际标签扩展） | 从 `job.extra.welfare_list: string[]` 精确匹配；智联/旧结果无此键即不满足 |

- **组内 OR、组间 AND**：命中任一所选选项即通过该组；所有非空组全部通过才显示。
- **sentinel 规则**：「不限」选中 = 该组无约束，与同组其它选项互斥（点选任一非 sentinel 选项自动取消 sentinel，反之亦然）；面板打开时 sentinel 默认选中。
- **徽标计数**：所有组「非 sentinel 选中项」数量之和；为 0 时不显示徽标。
- **福利组提示**：组下方灰字「智联岗位暂不支持福利筛选」（始终显示，不随选择变化）。

## 3. 薪资解析与判档规则

1. **单位排除**：文本含「元/天」「/天」「元」「日薪」等非月薪 K 单位标记、或为空、或为「面议」→ 解析失败 `null`，不判档。单位排除先于数字提取。
2. 对通过单位排除的文本提取全部 `\d+(\.\d+)?` 数字；首个数字 ≥ 3 视为月薪区间下限（K 为单位）；无数字 → `null`。
3. 档位判定（单侧开闭区间，**以区间下限为准**）：`lt5` <5K；`5-10` ∈[5,10)；`10-15` ∈[10,15)；`15up` ≥15K。例：「10-15K」→`10-15`；「25-35K」→`15up`；「5-8K」→`5-10`。
4. 「面议」、解析失败、元/天实习计价：**不落入任何档位**；选任档位时这些岗位隐藏（D4）。
5. 忽略薪资后缀（「·13薪」「·14薪」等），后缀数字不参与区间；排序数值 = 区间下限（首个数字，如「20K以上」→20；「10-15K·13薪」→10）；无法解析为 null（沉底，见 §4）。

## 4. 排序合同

| sortKey | label | 行为 |
|---|---|---|
| `default` | 综合排序 | 原始顺序（不排序） |
| `salary_desc` | 薪资最高 | 数值（区间上限）降序；null（面议）恒沉底 |
| `salary_asc` | 薪资最低 | 数值升序；null 恒沉底 |
| `published_desc` | 最新发布 | **置灰 disabled**；数据补齐后注册 |
| `match_desc` | 匹配度最高 | **置灰 disabled**；数据补齐后注册 |

- 排序稳定：相同数值保持原始相对顺序。
- 置灰项：`disabled` + title「数据补齐后开放」，点击无效果。
- 排序只影响顺序，不影响筛选结果与分类计数。

## 5. 浮层与交互合同

- 两个浮层（筛选面板 / 排序菜单）锚定各自按钮，打开时互斥（开一个关另一个）。
- 关闭路径：点外部 / ESC / 点按钮 / 操作完成（排序点选后、筛选「确定」后）。
- 筛选面板「确定」：应用当前草稿状态并关闭；「重置」：清空草稿为全 sentinel 并关闭。
- 筛选面板打开时修改为草稿（draft），不即时生效；排序点选即生效（无草稿）。
- 窄屏（≤390px）：按钮退化为纯图标（`SlidersHorizontal` / `ArrowUpDown`），徽标保留数字。

## 6. 空态与边界

- **筛选空态**：过滤后列表为空且原列表非空时，显示「没有符合条件的岗位」+「清除筛选」按钮（清空 filterState 并关闭一切）；原列表本身为空时维持既有空态（不显示清除按钮）。
- **状态生命周期**（D3）：切分类、切平台（`platformFilter` 变化）保留；**仅当父组件结果重新加载**（`resultEpoch` prop 递增，见下）时重置为默认（全 sentinel + `default`）。
- **resultEpoch 信号**：父组件 `DiscoveryView.vue` 在结果加载完成（`pipelineResult` 被新 run 结果替换）时递增 `resultEpoch` 并传入 `JobWorkspace`；组件 `watch(resultEpoch)` 重置筛选/排序/`visibleCount`。禁止用 `props.jobs` 内容变化判断重置——切分类/切平台同样改变 jobs 内容，会违反 D3。
- **选中跟随**：筛选/排序生效后，若用户未手动选择过岗位（`userSelectedDetail === false`），`localSelectedId` 更新为过滤排序后第一项，保证列表第一项与右侧详情一致；已手动选择时保持原选中项（若被过滤掉则回退到过滤后第一项）。
- **dropped 分类**：与其他分类同样支持筛选与排序（dropped 岗位携带相同展示快照字段）。
- **无限滚动**：筛选/排序/重置导致可见集合变化时，`visibleCount` 重置为 `batchSize`；哨兵与 observer 逻辑不变。

## 7. 类型（`webui/src/types.ts` 或 listFilter.ts 内）

```ts
type SalaryBand = "lt5" | "5-10" | "10-15" | "15up";
type FilterGroupState = Record<string, string[]>; // key → 选中 value 列表（不含 sentinel）
interface FilterState { salary: SalaryBand[]; experience: string[]; degree: string[]; welfare: string[]; }
type SortKey = "default" | "salary_desc" | "salary_asc" | "published_desc" | "match_desc";
```