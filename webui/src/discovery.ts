import type {
  AdvancedSettingsState,
  ExecutionSelection,
  ExecutionSettings,
  FrozenSearchScope,
  JobItem,
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
  if (!Number.isInteger(plannedPages) || plannedPages < 1 || plannedPages > 30) {
    throw new RangeError("planned pages must be an integer from 1 to 30");
  }
  if (plannedPages <= 9) return "small";
  if (plannedPages <= 19) return "medium";
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
