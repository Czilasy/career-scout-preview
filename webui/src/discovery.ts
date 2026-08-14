import type {
  AdvancedSettingsState,
  ExecutionSelection,
  ExecutionSettings,
  FrozenSearchScope,
  JobItem,
  Platform,
  PlatformCityCatalog,
  PlatformFilterSchema,
  ScopePreviewResponse,
  TaskSize,
} from "./types";

export interface PipelineResult {
  ok?: boolean;
  jobs?: JobItem[];
  dropped?: JobItem[];
  total_scraped?: number;
  total_kept?: number;
  total_matched?: number;
  total_dropped?: number;
  profile_summary?: string;
  error?: string;
}

export interface PipelineGroups {
  matched: JobItem[];
  unmatched: JobItem[];
  uncertain: JobItem[];
  dropped: JobItem[];
}

export type ResultPlatformFilter = "all" | "boss" | "zhilian";

export type RoundStatusPhase = "scraping" | "screening" | "judged" | "scraped";
export type RoundStatusScope = "all" | "boss" | "zhilian" | "history";
export interface RoundStatusPayload {
  platform: Platform;
  phase: RoundStatusPhase;
  judged: number;
  scope: RoundStatusScope;
}

export function roundScopeLabel(scope: RoundStatusScope, platform: Platform): string {
  if (scope === "all") return "全部";
  if (scope === "history") return "历史轮次";
  return platform === "boss" ? "BOSS" : "智联";
}

export function historyStatusLabel(status: string, jobCount: number): string {
  const normalized = String(status || "").toLowerCase();
  if (["scraped_only"].includes(normalized)) return "已抓取，未筛选";
  if (["done", "succeeded", "completed"].includes(normalized)) return "完成";
  if (["partial", "completed_with_pending"].includes(normalized)) return "部分结果";
  return `失败但有 ${jobCount} 个岗位`;
}
/** 纯展示层平台过滤：按 job.platform 过滤 jobs/dropped；"all" 原样返回。
 *
 * 依赖后端保证每个岗位带 platform 身份（实时任务结果按任务平台回填、
 * DB 恢复路径按 screening_results.platform 读取）；缺 platform 的岗位
 * 在任何单一平台视图下都会被过滤掉，因此过滤前调用方应回填平台身份。
 */
export function filterPipelineResultByPlatform(
  result: PipelineResult,
  filter: ResultPlatformFilter,
): PipelineResult {
  if (filter === "all") return result;
  return {
    ...result,
    jobs: (Array.isArray(result.jobs) ? result.jobs : []).filter((job) => job.platform === filter),
    dropped: (Array.isArray(result.dropped) ? result.dropped : []).filter((job) => job.platform === filter),
  };
}

/** 防御性平台回填：为缺 platform 的岗位补上任务自身平台（权威来源）。 */
export function backfillJobPlatform(
  result: PipelineResult,
  platform: Platform | null | undefined,
): PipelineResult {
  if (!platform || !result) return result;
  for (const job of Array.isArray(result.jobs) ? result.jobs : []) {
    if (job && typeof job === "object" && !job.platform) job.platform = platform;
  }
  for (const job of Array.isArray(result.dropped) ? result.dropped : []) {
    if (job && typeof job === "object" && !job.platform) job.platform = platform;
  }
  return result;
}

function uniqueNonEmpty(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

export function buildSearchScriptParams(keywords: string[], cities: string[]) {
  return {
    keyword: uniqueNonEmpty(keywords).join(","),
    city: uniqueNonEmpty(cities),
    filters: {},
  };
}

/** B042：关键词有、城市空时两个开始入口需要确认后按全国继续。 */
export function shouldConfirmNationalScope(
  keywords: string[],
  cities: string[],
): boolean {
  const hasKeyword = keywords.some((item) => item.trim());
  const hasCity = cities.some((item) => item.trim());
  return hasKeyword && !hasCity;
}

export function partitionPipelineResult(result: PipelineResult): PipelineGroups {
  const groups: PipelineGroups = {
    matched: [],
    unmatched: [],
    uncertain: [],
    dropped: Array.isArray(result.dropped) ? result.dropped : [],
  };
  for (const job of Array.isArray(result.jobs) ? result.jobs : []) {
    if (job.verdict === "match") groups.matched.push(job);
    else if (job.verdict === "not_match") groups.unmatched.push(job);
    else groups.uncertain.push(job);
  }
  return groups;
}

export function classifyTaskSize(plannedPages: number): TaskSize {
  if (!Number.isInteger(plannedPages) || plannedPages < 1 || plannedPages > 200) {
    throw new RangeError("planned pages must be an integer from 1 to 200");
  }
  if (plannedPages <= 9) return "small";
  if (plannedPages <= 49) return "medium";
  return "large";
}

export function normalizeScopePreview(response: ScopePreviewResponse): FrozenSearchScope {
  if (!response.ok || !response.scope?.scope_digest) {
    throw new TypeError("backend scope preview is incomplete");
  }
  return {
    ...response.scope,
    keywords: [...response.scope.keywords],
    cities: [...response.scope.cities],
  };
}

export function projectResumeSuggestionToSchema(
  semantic: Record<string, string[]>,
  schema: PlatformFilterSchema,
): Record<string, string[]> {
  const projected: Record<string, string[]> = {};
  for (const field of schema.fields) {
    const labels = semantic[field.key] || [];
    const selected: string[] = [];
    for (const label of labels) {
      const option = field.options.find((opt) => opt.label === label);
      if (option) selected.push(option.value);
    }
    if (selected.length) projected[field.key] = selected;
  }
  return projected;
}

export function recoverSelectionSettings(
  state: AdvancedSettingsState,
  selection: ExecutionSelection,
): ExecutionSettings {
  if (selection === "custom") {
    if (!state.last_custom) throw new Error("recent custom settings are unavailable");
    return { ...state.last_custom.settings };
  }
  return { ...state.settings };
}

// ---------------------------------------------------------------------------
// T502：平台身份状态容器（contracts/platform-schema.md L142-159 + tasks006 L27）
// ---------------------------------------------------------------------------
// 三身份独立：草稿平台 / 任务平台 / 最近结果平台。
// 不引入 Vue reactivity，保持 discovery.ts 纯函数 + 闭包风格；T503 起由组件接到 ref。
// mock 阶段：DEFAULT_PLATFORM 硬编码 "boss"；T515 真实联调由组件从 /api/platforms.default_platform
// 读取后传入 createPlatformState(initial)。task / result 初始为 null（无运行任务、无最近结果）。

/** mock 阶段的默认草稿平台。真实联调阶段由组件从后端读取后传入工厂。 */
export const DEFAULT_PLATFORM: Platform = "boss";

/**
 * 三身份独立平台状态容器。
 * 不变式（platform-schema.md L142-159）：
 *  1. setDraftPlatform 不改 task/result
 *  2. setTaskPlatform 不改 draft/result（任务恢复）
 *  3. setResultPlatform 不改 draft/task；不触发草稿切换
 *  4. 不用 watcher 相互覆盖
 */
export interface PlatformState {
  readonly draft: Platform;
  readonly task: Platform | null;
  readonly result: Platform | null;
  /** 草稿切换：只作用于新任务草稿，不改 task/result。 */
  setDraftPlatform: (platform: Platform) => void;
  /** 任务恢复：从任务响应设置任务平台，不改 draft/result；null 表示无运行任务。 */
  setTaskPlatform: (platform: Platform | null) => void;
  /** 结果加载：从结果 snapshot 设置结果平台，不改 draft/task；不触发草稿切换。null 表示无最近结果。 */
  setResultPlatform: (platform: Platform | null) => void;
}

export function createPlatformState(initial: Platform = DEFAULT_PLATFORM): PlatformState {
  let draft = initial;
  let task: Platform | null = null;
  let result: Platform | null = null;
  return {
    get draft() {
      return draft;
    },
    get task() {
      return task;
    },
    get result() {
      return result;
    },
    setDraftPlatform(platform: Platform) {
      draft = platform;
    },
    setTaskPlatform(platform: Platform | null) {
      task = platform;
    },
    setResultPlatform(platform: Platform | null) {
      result = platform;
    },
  };
}

// ---------------------------------------------------------------------------
// T504/T505：异步平台资源加载器（contracts/platform-schema.md L151-156）
// ---------------------------------------------------------------------------
// platform-schema.md L151-156 要求：发出请求时捕获目标平台和请求版本；
// 响应返回后同时校验请求仍为该资源的最新请求、响应平台与目标平台一致；
// 任一校验失败时丢弃响应，不更新 schema、城市、筛选草稿、加载状态或错误状态；
// 快速切换导致旧请求被取消不显示为当前平台错误。
//
// 用于 /api/filter-labels?platform=（PlatformFilterSchema）和
// /api/options?platform=（PlatformCityCatalog）。两者结构都带 platform 字段，
// 故用泛型 T extends { platform: Platform } 复用同一份序号 + 取消 + 校验逻辑。

export interface AsyncResourceLoader<T extends { platform: Platform }> {
  /** 当前已加载平台（仅成功响应后才更新；旧响应被丢弃时不更新）。 */
  readonly loadedPlatform: Platform | null;
  /** 当前已加载数据；仅当 loadedPlatform !== null 时有效。 */
  readonly data: T | null;
  /** 当前正在请求的平台（发出请求即设；请求结束清空）。 */
  readonly pendingPlatform: Platform | null;
  /** 上次错误（仅当最新请求失败时写入；旧请求被取消或被覆盖不写错误）。 */
  readonly error: string | null;
  /**
   * 发起请求；若已有请求在跑会取消旧的。
   * 返回 true 当且仅当响应被采纳（仍是最新请求 + 响应平台匹配）。
   * fetcher 应尊重 signal.aborted 并抛 AbortError 以释放资源；
   * 但即使 fetcher 忽略 signal，load 内部仍会通过 reqId 校验丢弃旧响应。
   */
  load(
    platform: Platform,
    fetcher: (platform: Platform, signal: AbortSignal) => Promise<T>,
  ): Promise<boolean>;
  /** 取消任何在途请求；后续 load 仍可正常发起。 */
  cancel(): void;
}

export function createAsyncResourceLoader<T extends { platform: Platform }>(): AsyncResourceLoader<T> {
  let loadedPlatform: Platform | null = null;
  let data: T | null = null;
  let pendingPlatform: Platform | null = null;
  let error: string | null = null;
  let reqId = 0;
  let activeController: AbortController | null = null;
  return {
    get loadedPlatform() {
      return loadedPlatform;
    },
    get data() {
      return data;
    },
    get pendingPlatform() {
      return pendingPlatform;
    },
    get error() {
      return error;
    },
    async load(platform, fetcher) {
      // 取消旧请求（不变式：旧请求的 signal 被 abort，旧 fetcher 应据此释放）
      if (activeController) activeController.abort();
      reqId += 1;
      const myReqId = reqId;
      pendingPlatform = platform;
      const myController = new AbortController();
      activeController = myController;
      try {
        const result = await fetcher(platform, myController.signal);
        // 校验 1：仍是最新请求（被后续 load 覆盖则丢弃）
        if (myReqId !== reqId) return false;
        // 校验 2：响应平台匹配目标平台（防后端串台）
        if (result.platform !== platform) return false;
        loadedPlatform = platform;
        data = result;
        error = null;
        pendingPlatform = null;
        return true;
      } catch (err) {
        // 旧请求的错误不污染 error 状态（platform-schema.md L156）
        if (myReqId !== reqId) return false;
        error = err instanceof Error ? err.message : String(err);
        pendingPlatform = null;
        return false;
      }
    },
    cancel() {
      if (activeController) activeController.abort();
      reqId += 1; // 让在途请求的 myReqId 不再匹配，使其响应被丢弃
      pendingPlatform = null;
    },
  };
}

/** 便于 DiscoveryView 区分 schema / 城市两类资源加载器实例的别名。 */
export type SchemaLoader = AsyncResourceLoader<PlatformFilterSchema>;
export type CityCatalogLoader = AsyncResourceLoader<PlatformCityCatalog>;
export function createSchemaLoader(): SchemaLoader {
  return createAsyncResourceLoader<PlatformFilterSchema>();
}
export function createCityCatalogLoader(): CityCatalogLoader {
  return createAsyncResourceLoader<PlatformCityCatalog>();
}
