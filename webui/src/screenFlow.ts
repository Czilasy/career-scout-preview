import type { Platform, RoundContext } from "./types";

export type ScreenPrimaryAction =
  | { kind: "pause"; label: "暂停筛选" }
  | { kind: "continue"; label: "继续 AI 筛选" }
  | { kind: "start"; label: "开始 AI 筛选" }
  | { kind: "recrawl"; label: "全部重抓" }
  | { kind: "pause-recrawl"; label: "暂停重抓" }
  | { kind: "continue-recrawl"; label: "继续重抓" }
  | { kind: "none" };

export interface ScreenRoundState {
  /** AI 筛选 run 状态：running/queued/paused/failed/interrupted/partial/succeeded/scraped_only。 */
  screenStatus: string;
  /** 重抓 run 状态：running/queued/paused/failed/interrupted/none。 */
  recrawlStatus: string;
  /** 本轮是否已存在 AI 筛选 run。 */
  hasScreenRun: boolean;
  /** 当前结果是否仍有待确认岗位。 */
  hasUncertain: boolean;
}

const RESUME_STATUSES = new Set(["paused", "failed", "interrupted", "partial"]);

export function isResumableStatus(status?: string): boolean {
  return Boolean(status && RESUME_STATUSES.has(status));
}

/** 用户已「结束并保存结果」的轮次：round_context 持久化为非可续终态。 */
export function isRoundClosedSaved(
  ctx: Pick<RoundContext, "status" | "resumable"> | null | undefined,
): boolean {
  return Boolean(ctx && ctx.status === "interrupted" && !ctx.resumable);
}

export function normalizeRoundContext(
  payload: Partial<RoundContext> | null | undefined,
): RoundContext | null {
  if (!payload || typeof payload !== "object") return null;
  return {
    platform: payload.platform || "boss",
    keywords: Array.isArray(payload.keywords)
      ? payload.keywords.map((item) => String(item))
      : [],
    cities: Array.isArray(payload.cities)
      ? payload.cities.map((item) => String(item))
      : [],
    screening_fields:
      payload.screening_fields && typeof payload.screening_fields === "object"
        ? payload.screening_fields as Record<string, string[]>
        : {},
    profile_summary: String(payload.profile_summary || ""),
    profile_facts:
      payload.profile_facts && typeof payload.profile_facts === "object"
        ? payload.profile_facts as Record<string, unknown>
        : {},
    scrape_task_id: String(payload.scrape_task_id || ""),
    screen_run_id: String(payload.screen_run_id || ""),
    status: String(payload.status || ""),
    resumable: Boolean(payload.resumable),
    has_frozen_filters: Boolean(payload.has_frozen_filters),
  };
}

export function deriveScreenPrimaryAction(
  state: ScreenRoundState,
): ScreenPrimaryAction {
  const recrawl = state.recrawlStatus;
  if (recrawl === "running" || recrawl === "queued") {
    return { kind: "pause-recrawl", label: "暂停重抓" };
  }
  if (recrawl === "paused" || recrawl === "failed" || recrawl === "interrupted") {
    return { kind: "continue-recrawl", label: "继续重抓" };
  }
  const status = state.screenStatus;
  if (status === "running" || status === "queued") {
    return { kind: "pause", label: "暂停筛选" };
  }
  if (status === "paused" || status === "failed" || status === "interrupted") {
    return { kind: "continue", label: "继续 AI 筛选" };
  }
  if (status === "partial" || status === "succeeded") {
    return state.hasUncertain
      ? { kind: "recrawl", label: "全部重抓" }
      : { kind: "none" };
  }
  if (status === "scraped_only" || !state.hasScreenRun) {
    return { kind: "start", label: "开始 AI 筛选" };
  }
  return { kind: "none" };
}

export function primaryActionLabel(action: ScreenPrimaryAction): string {
  return action.kind === "none" ? "" : action.label;
}

export function withoutRecrawl(action: ScreenPrimaryAction): ScreenPrimaryAction {
  return action.kind === "recrawl" ? { kind: "none" } : action;
}

export function continueTargets(
  contexts: Array<Partial<RoundContext> | null | undefined>,
  filter: "all" | Platform,
): Platform[] {
  const platforms = new Set<Platform>();
  for (const ctx of contexts) {
    const normalized = normalizeRoundContext(ctx);
    if (!normalized || !normalized.resumable || !normalized.platform) continue;
    if (filter === "all" || normalized.platform === filter) {
      platforms.add(normalized.platform);
    }
  }
  return Array.from(platforms);
}

/** 2993 回归：已有 AI 筛选轮但冻结条件恢复为空时视为未恢复。 */
export function roundConditionsRestored(ctx: RoundContext | null): boolean {
  if (!ctx) return true;
  if (!ctx.has_frozen_filters) return true;
  return Object.keys(ctx.screening_fields || {}).length > 0;
}
