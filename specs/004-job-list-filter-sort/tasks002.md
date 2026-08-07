# Task 002：前端筛选/排序纯函数层

**所属 Wave**：2（前端纯函数，并行独立） | **用户故事**：US1/US2/US3（筛选、排序、边界行为）

## 必读文件

- `specs/004-job-list-filter-sort/contracts/filter-sort.md`（本包唯一行为权威，§2/§3/§4 全部规则）
- `webui/src/types.ts`（`JobItem`，只读；`extra?: Record<string, unknown>`）
- `webui/src/discovery.ts`（同层纯函数文件，风格参考）
- `webui/src/components/__tests__/JobWorkspace.spec.ts`（测试基建参考：vitest + @vue/test-utils）

## 写入范围（互斥）

`webui/src/listFilter.ts`（新建）、`webui/src/__tests__/listFilter.spec.ts`（新建）。**禁止**修改任何组件、视图、样式文件。

## 原子清单

- [ ] T001 实现 `parseSalaryRange(salary: string): [number, number] | null`：先做单位排除（含「元/天」「/天」「元」「日薪」等非 K 标记、空、「面议」→ `null`）；再提取全部 `\d+(\.\d+)?`，首个数字≥3 视为下限、数字序列最大值为上限（K 单位）；无数字或首个数字<3 → `null`
- [ ] T002 实现 `salaryBandOf(range): SalaryBand | null`：以区间下限判档（`lt5`/`5-10`/`10-15`/`15up`），null 不落任何档
- [ ] T003 实现 `salaryValue(range): number | null`：排序数值 = 区间上限（数字序列最大值）
- [ ] T004 实现 `matchExperience(job, values)`：包含匹配（`应届生`↔含「应届」，其余↔含精确档位串）；`matchDegree`：包含匹配；`matchWelfare(job, values)`：`job.extra.welfare_list`（string[]）精确匹配，键缺失即不满足
- [ ] T005 实现 `filterJobs(jobs, filterState)`：组内 OR、组间 AND；组空（无选中）不约束；返回新数组
- [ ] T006 实现 `sortJobs(jobs, sortKey)`：`default` 原序；`salary_desc`/`salary_asc` 按 `salaryValue` 稳定排序（Array.prototype.sort 需带索引保证稳定，或先映射索引），null 恒沉底（两种方向一致）
- [ ] T007 导出配置常量：`FILTER_GROUPS`（四组：salary/experience/degree/welfare，含 sentinel 标记与选项定义）、`SORT_OPTIONS`（五项，`published_desc`/`match_desc` 标记 `disabled: true`、`note: "数据补齐后开放"`）
- [ ] T008 单测覆盖合同全部规则：单位排除（150-200元/天/日薪/面议/空）、档位判定边界（4-6K/5-8K/10-15K/25-35K/20K以上/带·13薪后缀）、经验各档文本（应届生/1-3年/3-5年/5-10年/10年以上/无经验）、学历前缀（统招本科）、福利缺失、组内 OR 组间 AND、排序稳定性与面议沉底、置灰配置
- [ ] T009 提交：仅本包文件，信息 `feat: add job list filter and sort pure functions`

## 完成定义

`listFilter.spec.ts` 全绿且逐条对应合同 §2/§3/§4；既有前端测试零回归（`npm test` 或仓库既有 vitest 命令）；无组件依赖、无副作用。

## 提交纪律

只暂存本包文件；commit email `czyooutzilas@gmail.com`；提交前 `git diff --check` 与 `git status --short`。