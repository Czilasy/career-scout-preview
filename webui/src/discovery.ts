import type { JobItem } from "./types";

export interface PipelineResult {
  ok?: boolean;
  jobs?: JobItem[];
  dropped?: JobItem[];
  total_scraped?: number;
  total_kept?: number;
  total_matched?: number;
  total_priority?: number;
  total_considered?: number;
  total_not_recommended?: number;
  total_dropped?: number;
  profile_summary?: string;
  error?: string;
}

export interface PipelineGroups {
  priority: JobItem[];
  considered: JobItem[];
  notRecommended: JobItem[];
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
    priority: [],
    considered: [],
    notRecommended: [],
    uncertain: [],
    dropped: Array.isArray(result.dropped) ? result.dropped : [],
  };
  for (const job of Array.isArray(result.jobs) ? result.jobs : []) {
    if (job.verdict === "priority") groups.priority.push(job);
    else if (job.verdict === "consider" || job.verdict === "match") groups.considered.push(job);
    else if (job.verdict === "not_match") groups.notRecommended.push(job);
    else groups.uncertain.push(job);
  }
  return groups;
}
