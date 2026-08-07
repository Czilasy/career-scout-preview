// ---------------------------------------------------------------------------
// 岗位列表筛选 / 排序纯函数层（specs/004 contracts/filter-sort.md 唯一行为权威）
// ---------------------------------------------------------------------------
// 数据管道：props.jobs（父组件已按平台+分类过滤）
//   → filterJobs(jobs, filterState)  // 条件筛选：组内 OR、组间 AND
//   → sortJobs(jobs, sortKey)        // 排序：稳定排序
//   → visibleJobs（slice(batchSize)，无限滚动不变）
// 本模块无组件依赖、无副作用；筛选/排序状态归 JobWorkspace 内部所有。

import type { JobItem } from "./types";

// ---------------------------------------------------------------------------
// 类型（contracts/filter-sort.md §7）
// ---------------------------------------------------------------------------

export type SalaryBand = "lt5" | "5-10" | "10-15" | "15up";

/** key → 选中 value 列表（不含 sentinel；sentinel 选中等价于空数组）。 */
export type FilterGroupState = Record<string, string[]>;

export interface FilterState {
  salary: SalaryBand[];
  experience: string[];
  degree: string[];
  welfare: string[];
}

export type SortKey = "default" | "salary_desc" | "salary_asc" | "published_desc" | "match_desc";

/** 全部空选：不改变列表。 */
export function emptyFilterState(): FilterState {
  return { salary: [], experience: [], degree: [], welfare: [] };
}

/** 徽标计数：所有组「非 sentinel 选中项」数量之和；为 0 时不显示徽标。 */
export function countActiveFilters(state: FilterState): number {
  return state.salary.length
    + state.experience.length
    + state.degree.length
    + state.welfare.length;
}

// ---------------------------------------------------------------------------
// 筛选组配置（§2）
// ---------------------------------------------------------------------------

export interface FilterOptionDef {
  value: string;
  label: string;
}

export interface FilterGroupDef {
  key: keyof FilterState;
  label: string;
  /** sentinel（「不限」）选中 = 该组无约束；与同组其它选项互斥，不存入状态。 */
  sentinel?: string;
  /** 组下方灰字提示（始终显示，不随选择变化）。 */
  note?: string;
  options: FilterOptionDef[];
}

export const FILTER_GROUPS: FilterGroupDef[] = [
  {
    key: "salary",
    label: "薪资范围",
    options: [
      { value: "lt5", label: "5K以下" },
      { value: "5-10", label: "5-10K" },
      { value: "10-15", label: "10-15K" },
      { value: "15up", label: "15K以上" },
    ],
  },
  {
    key: "experience",
    label: "工作经验",
    sentinel: "不限",
    options: [
      { value: "应届生", label: "应届生" },
      { value: "1-3年", label: "1-3年" },
      { value: "3-5年", label: "3-5年" },
      { value: "5-10年", label: "5-10年" },
      { value: "10年以上", label: "10年以上" },
    ],
  },
  {
    key: "degree",
    label: "学历",
    sentinel: "不限",
    options: [
      { value: "大专", label: "大专" },
      { value: "本科", label: "本科" },
      { value: "硕士", label: "硕士" },
    ],
  },
  {
    key: "welfare",
    label: "其他条件",
    note: "智联岗位暂不支持福利筛选",
    options: [
      { value: "五险一金", label: "五险一金" },
      { value: "双休", label: "双休" },
    ],
  },
];

// ---------------------------------------------------------------------------
// 排序配置（§4）
// ---------------------------------------------------------------------------

export interface SortOptionDef {
  key: SortKey;
  label: string;
  /** 数据暂缺的排序项：置灰 disabled + 提示。 */
  disabled?: boolean;
  note?: string;
}

export const SORT_OPTIONS: SortOptionDef[] = [
  { key: "default", label: "综合排序" },
  { key: "salary_desc", label: "薪资最高" },
  { key: "salary_asc", label: "薪资最低" },
  { key: "published_desc", label: "最新发布", disabled: true, note: "数据补齐后开放" },
  { key: "match_desc", label: "匹配度最高", disabled: true, note: "数据补齐后开放" },
];

// ---------------------------------------------------------------------------
// 薪资解析与判档（§3）
// ---------------------------------------------------------------------------

// 非月薪 K 单位标记：先于数字提取排除（日薪/时薪/按次计酬等形态不判档）。
const NON_K_UNIT_RE = /元\/天|元\/日|\/天|日薪|元|天薪/;

/**
 * 解析月薪文本为 K 单位区间 [下限, 上限]：
 * - 含非 K 单位标记、为空、为「面议」→ null（单位排除先于数字提取）
 * - 提取全部 `\d+(\.\d+)?` 数字：首个数字 ≥ 3 视为下限（K），序列最大值为上限
 * - 「·13薪」等后缀数字不参与区间判定（只参与上限取最大）
 * 例：「10-15K」→ [10,15]；「20K以上」→ [20,20]；「10-15K·13薪」→ [10,15]
 */
export function parseSalaryRange(salary: string | undefined): [number, number] | null {
  const text = String(salary || "").trim();
  if (!text || NON_K_UNIT_RE.test(text)) return null;
  const nums = [...text.matchAll(/\d+(?:\.\d+)?/g)].map((m) => Number(m[0]));
  if (!nums.length || nums[0] < 3) return null;
  return [nums[0], Math.max(...nums)];
}

/** 以区间下限判档（单侧开闭）：lt5 <5K；5-10 ∈[5,10)；10-15 ∈[10,15)；15up ≥15K。 */
export function salaryBandOf(range: [number, number] | null): SalaryBand | null {
  if (!range) return null;
  const low = range[0];
  if (low < 5) return "lt5";
  if (low < 10) return "5-10";
  if (low < 15) return "10-15";
  return "15up";
}

/** 排序数值 = 区间下限（范围薪资取最低，与判档同源；无法解析为 null 沉底）。 */
export function salaryValue(salary: string | undefined): number | null {
  const range = parseSalaryRange(salary);
  return range ? range[0] : null;
}

// ---------------------------------------------------------------------------
// 组匹配（§2）：组内 OR、组间 AND；组空（无选中）不约束
// ---------------------------------------------------------------------------

function matchSalary(salary: string | undefined, bands: SalaryBand[]): boolean {
  if (!bands.length) return true;
  const band = salaryBandOf(parseSalaryRange(salary));
  return band !== null && bands.includes(band);
}

function matchExperience(job: JobItem, values: string[]): boolean {
  if (!values.length) return true;
  const exp = String(job.experience || "").trim();
  if (!exp) return false;
  return values.some((value) => {
    if (value === "应届生") return exp.includes("应届");
    return exp.includes(value);
  });
}

function matchDegree(job: JobItem, values: string[]): boolean {
  if (!values.length) return true;
  const deg = String(job.degree || "").trim();
  if (!deg) return false;
  return values.some((value) => deg.includes(value));
}

/**
 * 福利：从 job.extra.welfare_list（string[]）精确匹配；
 * 键缺失（智联 / 旧结果）即不满足，不编造。
 */
function matchWelfare(job: JobItem, values: string[]): boolean {
  if (!values.length) return true;
  const list = job.extra?.welfare_list;
  if (!Array.isArray(list)) return false;
  return values.some((value) => (list as unknown[]).includes(value));
}

// ---------------------------------------------------------------------------
// 过滤 / 排序
// ---------------------------------------------------------------------------

export function filterJobs(jobs: JobItem[], state: FilterState): JobItem[] {
  if (!jobs.length || countActiveFilters(state) === 0) return jobs;
  return jobs.filter((job) =>
    matchSalary(job.salary, state.salary)
    && matchExperience(job, state.experience)
    && matchDegree(job, state.degree)
    && matchWelfare(job, state.welfare),
  );
}

/**
 * 排序（不修改入参数组）：
 * - default：原始顺序（不排序）；published_desc / match_desc：置灰，不注册排序逻辑
 * - salary_desc / salary_asc：按 salaryValue（区间上限）稳定排序；null（面议）恒沉底
 */
export function sortJobs(jobs: JobItem[], sortKey: SortKey): JobItem[] {
  // default 与置灰项（published_desc / match_desc，数据补齐前未注册排序逻辑）均返回原序。
  if (sortKey === "default" || sortKey === "published_desc" || sortKey === "match_desc") {
    return jobs;
  }
  const withValue = jobs.map((job, index) => ({
    job,
    index,
    value: salaryValue(job.salary),
  }));
  const direction = sortKey === "salary_desc" ? -1 : 1;
  // 带索引比较保证稳定（相同数值保持原始相对顺序）；null 两种方向均沉底
  return withValue
    .sort((a, b) => {
      if (a.value !== null && b.value !== null) {
        const byValue = (a.value - b.value) * direction;
        return byValue !== 0 ? byValue : a.index - b.index;
      }
      if (a.value !== null) return -1;
      if (b.value !== null) return 1;
      return a.index - b.index;
    })
    .map((item) => item.job);
}