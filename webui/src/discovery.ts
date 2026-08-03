import type {
  AdvancedSettingsState,
  ExecutionSelection,
  ExecutionSettings,
  FrozenSearchScope,
  JobItem,
  Platform,
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
