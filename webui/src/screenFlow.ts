import type { Platform, RoundContext } from "./types";

export type ScreenPrimaryAction =
  | { kind: "pause"; label: "暂停筛选" }
  | { kind: "continue"; label: "继续 AI 筛选" }
  // SPEC 046 v2：kind:"start" 有两个说法——新轮开始与「服务重启打断后重新开始」。
  // 后者由后端同一入口继承断点（ai_screen_api.py:350-351），不是就地继续。
  | { kind: "start"; label: "开始 AI 筛选" | "重新开始 AI 筛选" }
  | { kind: "recrawl"; label: "全部重抓" }
  | { kind: "pause-recrawl"; label: "暂停重抓" }
  | { kind: "continue-recrawl"; label: "继续重抓" }
  | { kind: "none" };

/** 抓取任务沿用 ScreenRoundActions 的公共动作外观，但文案不带 AI/筛选语义。 */
export type ScrapePrimaryAction =
  | { kind: "pause-scrape"; label: "暂停" }
  | { kind: "continue-scrape"; label: "继续" }
  | { kind: "none" };

/**
 * 主动作的唯一联合口径：抓取段与 AI 段共用同一个动作条外观，
 * 类型也在这一个地方并起来——动作条本体与呈现层都引这一个，不再各自声明一份。
 */
export type SharedPrimaryAction = ScreenPrimaryAction | ScrapePrimaryAction;

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

/** 抓取段还活着、可以给暂停出口的状态（与后端在飞口径同一份）。 */
const SCRAPE_LIVE_STATUSES = ["running", "queued", "pausing"];

/** 这一轮跑过但已经停下来的抓取段：没有活体 worker，只剩「结束并保存」这条收口出口。 */
const RUN_CLOSE_OUT_STATUSES = ["failed", "interrupted"];

/** 轨道级「放弃本轮」的说法：一条线只管自己，另一条线不受影响（FR-015）。 */
const TRACK_CANCEL_LABEL = "终止本轨";

export function isResumableStatus(status?: string): boolean {
  return Boolean(status && RESUME_STATUSES.has(status));
}

/** 已保存为非可续终态的轮次：round_context 持久化后不再自动恢复。 */
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
  if (recrawl === "paused" || recrawl === "failed") {
    return { kind: "continue-recrawl", label: "继续重抓" };
  }
  // 状态词表（v2 规格）：中断＝服务重启或人为停止，没有活体 worker，不可「继续」，
  // 只能开新一轮。重抓中断同理：这里不再落回 AI 段给一个不属于它的继续出口。
  if (recrawl === "interrupted") {
    return { kind: "none" };
  }
  const status = state.screenStatus;
  if (status === "running" || status === "queued") {
    return { kind: "pause", label: "暂停筛选" };
  }
  if (status === "paused" || status === "failed") {
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

/** 抓取主动作（树干唯一一份）：单平台现场与 Flow 轨道各算一次，不许第三份。 */
export interface ScrapeActionState {
  /** 抓取任务/轨道当前状态（单平台取快照，轨道取这条线自己的状态）。 */
  status: string;
  /** 这一条线是否已经有可操作的主体：单平台看有没有 scrapeTaskId，轨道恒为真。 */
  hasTask: boolean;
  /** 有独立的暂停 run（单平台的 pausedRunId）。 */
  hasPausedRun: boolean;
  /** 本地下发的开始/继续请求还在路上。 */
  isBusy: boolean;
  /** 点下去的那一个动作，保持它可见，不把人问成「再暂停一次」。 */
  busyAction?: string;
}

export function deriveScrapePrimaryAction(
  state: ScrapeActionState,
): ScrapePrimaryAction {
  if (state.busyAction === "continue-scrape") {
    return { kind: "continue-scrape", label: "继续" };
  }
  if (state.busyAction === "pause-scrape") {
    return { kind: "pause-scrape", label: "暂停" };
  }
  const closedOut = RUN_CLOSE_OUT_STATUSES.includes(state.status);
  if (state.hasTask && !closedOut && (state.hasPausedRun || state.status === "paused")) {
    return { kind: "continue-scrape", label: "继续" };
  }
  if (state.hasTask && (state.isBusy || SCRAPE_LIVE_STATUSES.includes(state.status))) {
    return { kind: "pause-scrape", label: "暂停" };
  }
  return { kind: "none" };
}

/** 跑过又有岗位可救的那一轮才谈得上「结束并保存结果」。 */
export function canFinishRunByStatus(runId: string, status: string): boolean {
  return Boolean(runId) && RUN_CLOSE_OUT_STATUSES.includes(status);
}

export interface TrackActionBarFacts {
  /** 这一行代表哪一段：抓取段用抓取口径，AI 段用筛选口径。 */
  stage: "scrape" | "screen";
  /** 这条线自己的状态（不是别段的快照）。 */
  status: string;
  /** 本段自己的 run id（AI 段在交接窗口里可能还没有）。 */
  runId: string;
  /**
   * 「结束并保存」实际要交给既有 run 级收尾路径的那个 run：本段没有就退到
   * 这条线已有的抓取 run。缺省同 runId。
   */
  finishRunId?: string;
}

export interface TrackActionBar {
  action: SharedPrimaryAction;
  showFinishSave: boolean;
  showCancel: boolean;
  cancelLabel: string;
}

/**
 * 轨道级动作条事实：一平台一行，各算一份，用的还是上面那两份既有派生。
 * 轨道没有「开始 AI 筛选」和重抓这两个出口——那两条属于整轮入口，
 * 出现在行上就会变成一条点不动的假按钮。
 */
export function deriveTrackActionBar(facts: TrackActionBarFacts): TrackActionBar {
  const cancelLabel = TRACK_CANCEL_LABEL;
  const finishRunId = facts.finishRunId ?? facts.runId;
  if (facts.stage === "scrape") {
    // 轨道本身是可操作主体（Flow 轨道行），不要求它已经拿到 run 身份：
    // 刚提交的排队轨也要能暂停与终止。
    const action = deriveScrapePrimaryAction({
      status: facts.status,
      hasTask: true,
      hasPausedRun: facts.status === "paused",
      isBusy: false,
    });
    return {
      action,
      // 收尾要有真的 run 才谈得上：刚提交、还没有 run 身份的排队轨只给暂停与终止，
      // 不做成点下去没有对象的假按钮。
      showFinishSave: Boolean(finishRunId)
        && (canFinishRunByStatus(finishRunId, facts.status) || action.kind !== "none"),
      showCancel: action.kind !== "none",
      cancelLabel,
    };
  }
  const derived = deriveScreenPrimaryAction({
    screenStatus: facts.status,
    recrawlStatus: "",
    hasScreenRun: Boolean(facts.runId),
    hasUncertain: false,
  });
  const action: ScreenPrimaryAction = derived.kind === "start" ? { kind: "none" } : derived;
  const actionable = action.kind === "pause" || action.kind === "continue";
  // 中断不给继续，但这一轮的两条出口必须留着：已判定的岗位还能保存，这条线还能终止。
  const closeOut = canFinishRunByStatus(finishRunId, facts.status);
  return {
    action,
    showFinishSave: (actionable || closeOut) && Boolean(finishRunId),
    showCancel: actionable || closeOut,
    cancelLabel,
  };
}


export function withoutRecrawl(action: ScreenPrimaryAction): ScreenPrimaryAction {
  // 03 页 AI 筛选卡片只显示 AI 筛选自身的动作；重抓（含暂停/继续重抓）
  // 由 ScreenRecrawlProgress 单独展示，避免重复按钮且无事件绑定的假按钮。
  if (
    action.kind === "recrawl"
    || action.kind === "pause-recrawl"
    || action.kind === "continue-recrawl"
  ) {
    return { kind: "none" };
  }
  return action;
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
