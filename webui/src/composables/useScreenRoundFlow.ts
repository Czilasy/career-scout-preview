import { computed, ref, watch } from "vue";
import type { Ref } from "vue";
import { apiRequest, errorMessage } from "../api";
import {
  deriveScreenPrimaryAction,
  continueTargets,
  isRoundClosedSaved,
  normalizeRoundContext,
  roundConditionsRestored,
  type ScreenPrimaryAction,
} from "../screenFlow";
import { deriveLiveTaskStep } from "./useDiscoveryState";
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
    // 035：抓取侧判定面（confirmNewRound 顶部守卫用——抓取活也属未结束任务）。
    scrapeBusy: Ref<boolean>;
    scrapeSnapshot: Ref<TaskSnapshot | null>;
    /** 035：历史模式标记（历史模式 04 页触发守卫时需完整退出历史再跳回）。 */
    historyRound?: Ref<{ runId: string; platform: Platform; status: string; jobCount: number } | null>;
    screenTaskId: Ref<string>;
    pausedRunId: Ref<string>;
    interruptedRunId: Ref<string>;
    screenBusy: Ref<boolean>;
    pausingScreen: Ref<boolean>;
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
    /** 035：历史模式触发守卫时完整退出历史（含平台还原/展示清理/落进度页）。 */
    returnToLatest: () => Promise<void>;
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
  // 025 B076：批中暂停二选一弹窗状态（批内信号 → 弹窗；任务状态变化自动关闭）
  const pauseDialogOpen = ref(false);
  const pauseBatchInfo = ref<{ current: number; total: number } | null>(null);

  const screenStatus = computed(() => {
    const ctx = roundContext.value;
    const ctxStatus = ctx ? String(ctx.status) : "";
    // 已结束保存：内存 finishedPartial 或持久化 closed round_context 都视为
    // 阶段性完成（partial），03 不再出现继续/结束动作按钮。
    const finishedSave = Boolean(deps.refs.finishedPartial.value) && Boolean(
      deps.refs.screenTaskId.value
      || deps.refs.pausedRunId.value
      || deps.refs.interruptedRunId.value
      || roundContext.value?.screen_run_id,
    );
    if (finishedSave || (ctx && isRoundClosedSaved(ctx))) return "partial";
    if (
      ctxStatus
      && ["running", "queued", "paused", "failed", "interrupted", "partial", "succeeded"].includes(ctxStatus)
    ) {
      return ctxStatus;
    }
    const raw = String(deps.refs.screenSnapshot.value?.status || "");
    // 运行态优先于「已抓取未筛选」次级状态（020 US5）：scraped_only 轮
    // 发起筛选进入运行期必须显示运行/暂停，不得残留 start。
    if (raw === "running" || deps.refs.screenBusy.value) return "running";
    if (deps.refs.currentRoundStatus.value === "scraped_only") return "scraped_only";
    if (raw === "completed_with_pending") return "partial";
    if (raw === "completed") return "succeeded";
    if (raw === "cancelled") return "interrupted";
    if (deps.refs.interruptedRunId.value) return "interrupted";
    if (deps.refs.pausedRunId.value) return "paused";
    return raw;
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
    // 已完成/不可续跑的结果快照不应因历史筛选字段缺失阻塞结果页，
    // 只有确实需要续跑的任务才提示恢复失败。
    if (ctx.resumable && !roundConditionsRestored(ctx)) {
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

  // 025 B076：判定「正处抓 JD 批次中」——stage=fetch_jd 且批内信号 jd_batch 非空，
  // 且任务未进入暂停/终态（弹窗竞态边界：批完成或任务已停视为不在批内）。
  function inJdBatch(): boolean {
    const snapshot = deps.refs.screenSnapshot.value;
    const status = String(snapshot?.status || "");
    if (["paused", "failed", "cancelled", "interrupted"].includes(status)) return false;
    const progress = snapshot?.progress as Record<string, unknown> | undefined;
    if (String(progress?.stage) !== "fetch_jd") return false;
    const batch = progress?.jd_batch;
    return Boolean(batch && typeof batch === "object");
  }

  function openPauseDialog(): void {
    const progress = deps.refs.screenSnapshot.value?.progress as Record<string, unknown> | undefined;
    const batch = (progress?.jd_batch ?? {}) as { current?: number; total?: number };
    pauseBatchInfo.value = {
      current: Number(batch.current || 0),
      total: Number(batch.total || 0),
    };
    pauseDialogOpen.value = true;
  }

  function closePauseDialog(): void {
    pauseDialogOpen.value = false;
  }

  // 弹窗打开期间任务状态变化（批次完成/任务已暂停等）→ 自动关闭（竞态边界）
  watch(
    () => deps.refs.screenSnapshot.value,
    () => {
      if (pauseDialogOpen.value && !inJdBatch()) closePauseDialog();
    },
    { deep: true },
  );

  async function doPause(runId: string, mode: "immediate" | "graceful"): Promise<void> {
    if (busyAction.value) return;
    busyAction.value = "pause";
    const isImmediate = mode === "immediate";
    const pauseMessage = isImmediate ? "正在立即停止…" : "正在暂停…";
    // immediate 覆盖一个岗位收尾（逐条检查点中断，可能数十秒）；graceful 本就
    // 等批边界，短轮询后转提示文案即可。
    const maxPolls = isImmediate ? 240 : 15;
    deps.refs.screenBusy.value = true;
    deps.refs.pausingScreen.value = true;
    deps.refs.screenSnapshot.value = {
      ...(deps.refs.screenSnapshot.value || {}),
      status: "pausing",
      progress: { ...((deps.refs.screenSnapshot.value || {}).progress || {}), message: pauseMessage },
      error: "",
    };
    try {
      await apiRequest(`/api/task/pause/${encodeURIComponent(runId)}`, {
        method: "POST",
        json: { mode },
      });
      let paused = false;
      let terminalOther = false;
      for (let i = 0; i < maxPolls; i += 1) {
        await delay(300);
        const data = await apiRequest<TaskSnapshot>(
          `/api/task-state/${encodeURIComponent(runId)}`,
        );
        if (String(data.status) === "paused") {
          deps.refs.pausedRunId.value = runId;
          deps.refs.pausingScreen.value = false;
          deps.refs.screenSnapshot.value = data;
          paused = true;
          break;
        }
        if (TERMINAL_POLL_STATUSES.has(String(data.status))) {
          deps.refs.pausingScreen.value = false;
          deps.refs.screenSnapshot.value = data;
          terminalOther = true;
          break;
        }
        deps.refs.screenSnapshot.value = {
          ...data,
          status: "pausing",
          progress: { ...(data.progress || {}), message: pauseMessage },
        };
      }
      deps.refs.screenBusy.value = false;
      if (paused) {
        await deps.api.loadLatestResult();
        deps.api.notify("任务已暂停，结果已保留", "success");
      } else {
        if (!terminalOther) {
          deps.refs.screenBusy.value = true;
          deps.api.notify(
            isImmediate
              ? "仍在停止当前批次，完成后会自动暂停"
              : "正在等待当前批次结束，完成后会自动暂停",
            "warning",
          );
        }
      }
    } catch (error) {
      deps.refs.screenBusy.value = false;
      deps.refs.pausingScreen.value = false;
      deps.api.notify(errorMessage(error, "暂停失败，请重试"), "error");
    } finally {
      busyAction.value = "";
    }
  }

  async function pauseScreen(): Promise<void> {
    if (busyAction.value) return;
    const runId = deps.refs.screenTaskId.value;
    if (!runId) return;
    // 025 B076：正处抓 JD 批次中 → 弹二选一（立即停止 / 等这批抓完），暂停动作挂起；
    // 粗筛/精筛/批间（无批内信号）→ 不弹窗，直接暂停（graceful 现状行为）。
    if (inJdBatch()) {
      openPauseDialog();
      return;
    }
    await doPause(runId, "graceful");
  }

  async function confirmPauseChoice(mode: "immediate" | "graceful"): Promise<void> {
    closePauseDialog();
    const runId = deps.refs.screenTaskId.value;
    if (!runId) return;
    await doPause(runId, mode);
  }

  async function continueScreen(platform?: Platform): Promise<void> {
    if (busyAction.value) return;
    const targetCtx = platform ? roundContexts.value[platform] : roundContext.value;
    if (targetCtx?.resumable && !roundConditionsRestored(targetCtx)) {
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
      // 显式平台（选平台后/单平台视图）：重抓开始即切 03 展示进度。
      // 无平台（"全部"视图）：由 recrawlUncertain 决定——多平台先弹选择
      // 引导（留在 04），单平台直接重抓并在内部切 03。
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
    // 035（真机问题②，FR-011）：未结束任务存在（含抓取运行中/暂停/中断）时，
    // 一律跳回该任务的真实进度页（抓取→02、筛选/重抓→03），不 reset、不取消、不弹窗。
    // 守卫先于 resumable 计算——历史模式 04 页入口同样被此覆盖。
    const liveStep = deriveLiveTaskStep({
      scrapeBusy: deps.refs.scrapeBusy.value,
      scrapeSnapshot: deps.refs.scrapeSnapshot.value,
      screenBusy: deps.refs.screenBusy.value,
      screenSnapshot: deps.refs.screenSnapshot.value,
      recrawlBusy: deps.refs.recrawlBusy.value,
      recrawlSnapshot: deps.refs.recrawlSnapshot.value,
      pausedRunId: deps.refs.pausedRunId.value,
      interruptedRunId: deps.refs.interruptedRunId.value,
    });
    if (liveStep) {
      // 历史模式下先完整退出历史（还原平台、清理历史展示），再落到任务进度页。
      if (deps.refs.historyRound?.value) await deps.api.returnToLatest();
      deps.refs.activeStep.value = liveStep;
      deps.api.notify("当前还有任务在跑，已回到任务进度", "info");
      return false;
    }
    const snapshotStatus = String(deps.refs.screenSnapshot.value?.status || "");
    const resumable = Boolean(
      deps.refs.pausedRunId.value
      || deps.refs.interruptedRunId.value
      || deps.refs.screenBusy.value
      || (!deps.refs.finishedPartial.value
        && ["paused", "failed", "interrupted"].includes(snapshotStatus))
      || anyResumableTarget.value
      || Boolean(roundContext.value?.resumable),
    );
    if (!resumable) {
      busyAction.value = "new-round";
      try {
        await deps.api.resetWorkflow();
      } finally {
        busyAction.value = "";
      }
      return true;
    }
    // 035：未结束任务存在时，一律跳回未完成的任务视图，不弹确认、不开始新一轮、不取消任务。
    deps.refs.activeStep.value = "screen";
    deps.api.notify("当前还有任务在跑，已回到任务进度", "info");
    return false;
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
    doPause,
    confirmPauseChoice,
    closePauseDialog,
    pauseDialogOpen,
    pauseBatchInfo,
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
