import type { SelectOption } from "./types";

export type ScreeningOptions = Record<string, SelectOption[]>;

export function isTerminalScreeningStatus(status: string): boolean {
  return ["succeeded", "partial", "failed", "interrupted"].includes(status);
}

export function buildFilterSummary(
  filters: Record<string, string>,
  options: ScreeningOptions,
  keyword: string,
  pages: number,
  maxDetails: number,
): string {
  const selected: string[] = [];
  const trimmedKeyword = keyword.trim();
  if (trimmedKeyword) selected.push(`关键词：${trimmedKeyword}`);
  for (const [key, value] of Object.entries(filters)) {
    if (!value) continue;
    const label = options[key]?.find((option) => option.value === value)?.label;
    if (label) selected.push(label);
  }
  if (!selected.length) selected.push("全部条件");
  return `${selected.join(" · ")} · ${pages} 页 · 核验 ${maxDetails} 条详情`;
}
