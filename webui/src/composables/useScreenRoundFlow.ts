import { computed, ref } from "vue";
import type { Ref } from "vue";
import { apiRequest, errorMessage } from "../api";
import {
  deriveScreenPrimaryAction,
  continueTargets,
  isResumableStatus,
  normalizeRoundContext,
  roundConditionsRestored,
  type ScreenPrimaryAction,
} from "../screenFlow";
import type { Notice, Platform, RoundContext, TaskSnapshot } from "../types";

export interface ScreenRoundFlowDeps {
  refs: {
    filterValues: Ref<Record<Platform, Record<string, string[]>>>;
    keywords: Ref<Array<{ word: string; recommended: boolean }>>;
    selectedKeywords: Ref<string[]>;
    cityText: Ref<string>;
    profileSummary: Ref<string>;
    profileFacts: Ref<Record<string, unknown>>;
    profileConfirmed: Ref<boolean>;
    scrapeTaskId: Ref<string>;
    screenTaskId: Ref<string>;
    pausedRunId: Ref<string>;
    interruptedRunId: Ref<string>;
    screenBusy: Ref<boolean>;
    screenSnapshot: Ref<TaskSnapshot | null>;
    recrawlBusy: Ref<boolean>;
    recrawlTaskId: Ref<string>;
    recrawlSnapshot: Ref<TaskSnapshot | null>;
    finishedPartial: Ref<boolean>;
    activeStep: Ref<string>;
    currentRoundStatus: Ref<string>;
    resultPlatformFilter: Ref<"all" | Platform>;
    uncertainCount: Ref<number>;
  };
  api: {
    startAiScreen: (opts?: {
      consumeAutoScreen?: boolean;
      fields?: Record<string, string[]>;
      profile?: string;
    }) => Promise<void>;
    continueAiScreen: (platform?: Platform) => Promise<void>;
    recrawlUncertain: (platform?: Platform) => Promise<void>;
    continueRecrawl: () => Promise<void>;
    finishPausedTask: (runId: string) => Promise<void>;
    resetWorkflow: () => Promise<void>;
    loadLatestResult: () => Promise<void>;
    notify: (message: string, tone?: Notice["tone"]) => void;
  };
}

const TERMINAL_POLL_STATUSES = new Set([
  "paused", "failed", "cancelled", "interrupted", "completed", "completed_with_pending",
]);

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function snapshotWithProgress(
  current: TaskSnapshot | null,
  message: string,
): TaskSnapshot {
  return {
    ...(current || {}),
    status: "running",
    progress: { ...((current || {}).progress || {}), message },
    error: "",
  };
}

export function useScreenRoundFlow(deps: ScreenRoundFlowDeps) {
  const roundContext = ref<RoundContext | null>(null);
  const roundContexts = ref<Partial<Record<Platform, RoundContext | null>>>({});
  const continueGuide = ref<{ boss: boolean; zhilian: boolean } | null>(null);
  const busyAction = ref("");
  const suppressProfileWatch = ref(false);

  const screenStatus = computed(() => {
    const ctxStatus = roundContext.value?.status;
    if (
      ctxStatus
      && ["running", "queued", "paused", "failed", "interrupted", "partial", "succeeded"].includes(ctxStatus)
    ) {
      return ctxStatus;
    }
    const raw = String(deps.refs.screenSnapshot.value?.status || "");
    if (deps.refs.currentRoundStatus.value === "scraped_only") return "scraped_only";
    if (raw === "completed_with_pending") return "partial";
    if (raw === "completed") return "succeeded";
    if (raw === "cancelled") return "interrupted";
    if (deps.refs.finishedPartial.value && (
      deps.refs.screenTaskId.value || deps.refs.pausedRunId.value
      || deps.refs.interruptedRunId.value || roundContext.value?.screen_run_id
    )) return "interrupted";
    if (deps.refs.interruptedRunId.value) return "interrupted";
    if (deps.refs.pausedRunId.value) return "paused";
    return raw || (deps.refs.screenBusy.value ? "running" : "");
  });

  const recrawlStatus = computed(() => {
    const raw = String(deps.refs.recrawlSnapshot.value?.status || "");
    if (raw === "completed" || raw === "completed_with_pending") return "";
    if (deps.refs.recrawlBusy.value && raw !== "paused" && raw !== "failed") {
      return "running";
    }
    return raw;
  });

  const hasScreenRun = computed(() => Boolean(
    deps.refs.screenTaskId.value
    || deps.refs.pausedRunId.value
    || deps.refs.interruptedRunId.value
    || roundContext.value?.screen_run_id
    || deps.refs.screenSnapshot.value,
  ));
  const allContinueTargetList = computed(() => continueTargets(
    Object.values(roundContexts.value),
    "all",
  ));
  const anyResumableTarget = computed(() => allContinueTargetList.value.length > 0);

  const screenAction = computed<ScreenPrimaryAction>(() => {
    const action = deriveScreenPrimaryAction({
      screenStatus: screenStatus.value,
      recrawlStatus: recrawlStatus.value,
      hasScreenRun: hasScreenRun.value,
      hasUncertain: Number(deps.refs.uncertainCount.value) > 0,
    });
    if (anyResumableTarget.value && ["none", "start", "recrawl"].includes(action.kind)) {
      return { kind: "continue", label: "继续 AI 筛选" };
    }
    return action;
  });

  const recrawlAction = computed<ScreenPrimaryAction>(() => {
    const status = recrawlStatus.value;
    if (status === "running" || status === "queued") {
      return { kind: "pause-recrawl", label: "暂停重抓" };
    }
    if (status === "paused" || status === "failed" || status === "interrupted") {
      return { kind: "continue-recrawl", label: "继续重抓" };
    }
    return { kind: "none" };
  });
  const continueTargetList = computed(() => continueTargets(
    Object.values(roundContexts.value),
    deps.refs.resultPlatformFilter.value,
  ));

  function restoreRoundContext(
    payload: Partial<RoundContext> | null | undefined,
  ): boolean {
    const ctx = normalizeRoundContext(payload);
    if (!ctx) return false;
    roundContext.value = ctx;
    roundContexts.value = { ...roundContexts.value, [ctx.platform]: ctx };
    suppressProfileWatch.value = true;
    try {
      deps.refs.keywords.value = ctx.keywords.map((word) => ({ word, recommended: false }));
      deps.refs.selectedKeywords.value = [...ctx.keywords];
      deps.refs.cityText.value = ctx.cities.join(",");
      if (ctx.platform) {
        deps.refs.filterValues.value = {
          ...deps.refs.filterValues.value,
          [ctx.platform]: { ...ctx.screening_fields },
        };
      }
      deps.refs.profileSummary.value = ctx.profile_summary;
      deps.refs.profileFacts.value = { ...ctx.profile_facts };
      deps.refs.profileConfirmed.value = true;
      if (ctx.scrape_task_id) deps.refs.scrapeTaskId.value = ctx.scrape_task_id;
    } finally {
      suppressProfileWatch.value = false;
    }
    if (!roundConditionsRestored(ctx)) {
      deps.api.notify("本轮筛选条件未能恢复，无法继续 AI 筛选", "warning");
      return false;
    }
    return true;
  }

  function registerRoundContext(
    platform: Platform,
    payload: Partial<RoundContext> | null | undefined,
  ): void {
    const ctx = normalizeRoundContext(payload);
    if (!ctx) return;
    roundContexts.value = { ...roundContexts.value, [platform]: ctx };
    roundContext.value = ctx;
  }

  function clearRoundContext(): void {
    roundContext.value = null;
    roundContexts.value = {};
    continueGuide.value = null;
  }

  async function pauseScreen(): Promise<void> {
    if (busyAction.value) return;
    const runId = deps.refs.screenTaskId.value;
    if (!runId) return;
    busyAction.value = "pause";
    deps.refs.screenBusy.value = true;
    deps.refs.screenSnapshot.value = snapshotWithProgress(
      deps.refs.screenSnapshot.value, "正在暂停…",
    );
    try {
      await apiRequest(`/api/task/pause/${encodeURIComponent(runId)}`, { method: "POST" });
      for (let i = 0; i < 15; i += 1) {
        await delay(300);
        const data = await apiRequest<TaskSnapshot>(
          `/api/task-state/${encodeURIComponent(runId)}`,
        );
        deps.refs.screenSnapshot.value = data;
        if (TERMINAL_POLL_STATUSES.has(String(data.status))) break;
      }
      deps.refs.screenBusy.value = false;
      await deps.api.loadLatestResult();
      deps.api.notify("任务已暂停，结果已保留", "success");
    } catch (error) {
      deps.refs.screenBusy.value = false;
      deps.api.notify(errorMessage(error, "暂停失败，请重试"), "error");
    } finally {
      busyAction.value = "";
    }
  }

  async function continueScreen(platform?: Platform): Promise<void> {
    if (busyAction.value) return;
    const targetCtx = platform ? roundContexts.value[platform] : roundContext.value;
    if (targetCtx && !roundConditionsRestored(targetCtx)) {
      deps.api.notify("本轮筛选条件未能恢复，无法继续 AI 筛选", "warning");
      return;
    }
    if (!platform) {
      let targets = continueTargetList.value;
      if (targets.length === 0) targets = allContinueTargetList.value;
      if (targets.length > 1) {
        continueGuide.value = {
          boss: targets.includes("boss"),
          zhilian: targets.includes("zhilian"),
        };
        return;
      }
      platform = targets[0];
      if (!platform) {
        const hasRunIdentity = Boolean(
          deps.refs.screenTaskId.value
          || deps.refs.pausedRunId.value
          || deps.refs.interruptedRunId.value
          || roundContext.value?.screen_run_id,
        );
        if (!hasRunIdentity) {
          deps.api.notify("没有可续跑的筛选任务", "warning");
          return;
        }
      }
    }
    continueGuide.value = null;
    busyAction.value = "continue";
    try {
      const ctx = roundContexts.value[platform] || roundContext.value;
      if (ctx) restoreRoundContext(ctx);
      await deps.api.continueAiScreen(platform);
    } catch (error) {
      deps.refs.screenBusy.value = false;
      deps.api.notify(errorMessage(error, "继续 AI 筛选失败，请重试"), "error");
    } finally {
      busyAction.value = "";
    }
  }

  function chooseContinuePlatform(platform: Platform): void {
    continueGuide.value = null;
    void continueScreen(platform);
  }

  function cancelContinueGuide(): void {
    continueGuide.value = null;
  }

  async function startScreen(): Promise<void> {
    if (busyAction.value) return;
    busyAction.value = "start";
    deps.refs.activeStep.value = "screen";
    try {
      await deps.api.startAiScreen();
    } finally {
      busyAction.value = "";
    }
  }

  async function startRecrawl(platform?: Platform): Promise<void> {
    if (busyAction.value || deps.refs.recrawlBusy.value) return;
    busyAction.value = "recrawl";
    try {
      if (platform) deps.refs.activeStep.value = "screen";
      await deps.api.recrawlUncertain(platform);
    } finally {
      busyAction.value = "";
    }
  }

  async function pauseRecrawl(): Promise<void> {
    const runId = deps.refs.recrawlTaskId.value;
    if (!runId || busyAction.value) return;
    busyAction.value = "pause-recrawl";
    deps.refs.recrawlBusy.value = true;
    deps.refs.recrawlSnapshot.value = snapshotWithProgress(
      deps.refs.recrawlSnapshot.value, "正在暂停重抓…",
    );
    try {
      await apiRequest(`/api/task/pause/${encodeURIComponent(runId)}`, { method: "POST" });
      for (let i = 0; i < 15; i += 1) {
        await delay(300);
        const data = await apiRequest<TaskSnapshot>(
          `/api/task-state/${encodeURIComponent(runId)}`,
        );
        deps.refs.recrawlSnapshot.value = data;
        if (TERMINAL_POLL_STATUSES.has(String(data.status))) break;
      }
      deps.refs.recrawlBusy.value = false;
      deps.api.notify("重抓已暂停，结果已保留", "success");
    } catch (error) {
      deps.refs.recrawlBusy.value = false;
      deps.api.notify(errorMessage(error, "暂停重抓失败，请重试"), "error");
    } finally {
      busyAction.value = "";
    }
  }

  async function continueRecrawl(): Promise<void> {
    if (busyAction.value) return;
    busyAction.value = "continue-recrawl";
    try {
      await deps.api.continueRecrawl();
    } finally {
      busyAction.value = "";
    }
  }

  async function finishRecrawl(): Promise<void> {
    const runId = deps.refs.recrawlTaskId.value || deps.refs.pausedRunId.value;
    if (!runId || busyAction.value) return;
    busyAction.value = "finish";
    try {
      await deps.api.finishPausedTask(runId);
    } finally {
      busyAction.value = "";
    }
  }

  async function confirmNewRound(): Promise<boolean> {
    const resumable = isResumableStatus(screenStatus.value)
      || Boolean(roundContext.value?.resumable)
      || anyResumableTarget.value;
    if (!resumable) {
      busyAction.value = "new-round";
      try {
        await deps.api.resetWorkflow();
      } finally {
        busyAction.value = "";
      }
      return true;
    }
    const confirmed = window.confirm(
      "当前仍有可续跑的筛选任务，开始新一轮会先归档 BOSS 和智联当前结果，断点将不可再续。确定开始新一轮吗？",
    );
    if (!confirmed) return false;
    busyAction.value = "new-round";
    try {
      await deps.api.resetWorkflow();
    } finally {
      busyAction.value = "";
    }
    return true;
  }

  return {
    roundContext,
    roundContexts,
    continueGuide,
    continueTargetList,
    busyAction,
    suppressProfileWatch,
    screenStatus,
    recrawlStatus,
    screenAction,
    recrawlAction,
    restoreRoundContext,
    registerRoundContext,
    clearRoundContext,
    pauseScreen,
    continueScreen,
    chooseContinuePlatform,
    cancelContinueGuide,
    startScreen,
    startRecrawl,
    pauseRecrawl,
    continueRecrawl,
    finishRecrawl,
    confirmNewRound,
  };
}
