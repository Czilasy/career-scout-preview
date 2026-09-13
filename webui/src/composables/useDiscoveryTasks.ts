// 021 B8 T027：DiscoveryView tasks 动作层（自 DiscoveryView.vue script 原样搬运，函数体零改动，跨域引用经 deps 调用时解析）。
// 031 B8：deps 形参类型 = discoveryDeps.ts 的 TasksNeeds（跨域依赖契约）。
import type { Ref } from "vue";
import type { DiscoveryState } from "./useDiscoveryState";
import type { TasksNeeds } from "./discoveryDeps";
import { ApiError, apiRequest, errorMessage, settingsApi, userFacingMessage } from "../api";
import type {
  AdvancedSettingsState,
  CandidateProfile,
  ExecutionSelection,
  ExecutionSettings,
  FrozenSearchScope,
  JobItem,
  LocationCondition,
  Notice,
  Platform,
  PlatformCityCatalog,
  PlatformFilterSchema,
  RoundContext,
  IntegritySnapshot,
  TaskSnapshot as ApiTaskSnapshot,
} from "../types";
import JobLifecycleActions from "../components/JobLifecycleActions.vue";
import JobWorkspace from "../components/JobWorkspace.vue";
import type { PipelineResult, RoundStatusPayload } from "../discovery";
import {
  Bookmark,
  Check,
  Download,
  FileText,
  Filter,
  History,
  LoaderCircle,
  Play,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
  Square,
  UploadCloud,
  X,
} from "@lucide/vue";
import { hasLiveTaskState, MODE_DEFAULT_PAGES } from "./useDiscoveryState";
import type { MergedLatestResult, TaskSnapshot } from "./useDiscoveryState";
import { setThemePlatform } from "./useTheme";
import { useDiscoverySceneState } from "./useDiscoverySceneState";
import type { SceneIdentity } from "../types";

type CleanupActionResponse = {
  error?: string;
  cleanup_error?: string;
  cleanup?: { ok?: boolean } | null;
};

function hasCleanupFailure(data: CleanupActionResponse): boolean {
  return data.error === "browser_cleanup_failed"
    || data.cleanup_error === "browser_cleanup_failed"
    || data.cleanup?.ok === false;
}

// 只有真正没有待处理结果的终态，才允许刷新后自动开启新一轮。
// completed_with_pending/partial 仍有待确认岗位，scraped_only 也只是抓取完成。
const FULLY_COMPLETED_TASK_STATUSES = new Set(["done", "completed", "succeeded"]);

export function useDiscoveryTasks(state: DiscoveryState, deps: TasksNeeds) {
  const { COMPLETED_TASK_STATUSES, POLL_BASE_DELAY, POLL_MAX_DELAY, POLL_MAX_RETRIES, activeCategory, activeStep, activeTaskRestored, advancedSettings, aiConsent, analysisReady, appliedResumePlatforms, autoScreenArmed, autoScreenFields, autoScreenProfile, cancelBusy, cityText, currentRoundStatus, customCity, customKeyword, draftPlatform, executionSelection, filterValues, finishedPartial, groups, historyBackToLatest, historyMode, historyRound, historyStore, interruptedRunId, isScrapedOnly, taskCompletedToast, keywords, locationDraft, oneClickOpen, pausedRunId, pausingScreen, pipelineResult, pipelineResultRunId, pollRetryCount, pollTimer, profileError, profileFacts, profileId, profileSummary, recrawlBusy, recrawlPlatformGuide, recrawlRetryCount, recrawlSnapshot, recrawlTaskId, rejectedIds, restoredTaskHint, resultLoaded, resultPlatformFilter, resultRunIds, resultsBootstrapPending, resultsPageSeen, resumeAnalysis, resumeAnalysisLandOnReturn, resumeAnalysisPhase, resumeAnalysisReset, schemaLoader, scopePreview, scopePreviewBusy, scrapeActionBusy, scrapeBusy, scrapeCompleted, scrapeSnapshot, scrapeTaskId, screenBusy, screenPanelOpen, screenSnapshot, screenTaskId, selectedFile, selectedKeywords, uncertainByPlatform, unfinishedWorkflowRestored, workflowEpoch } = state;
  const { platformState } = state;
  const sceneStore = useDiscoverySceneState();
  const { cancelScrape, clearLatestResult, clearWorkflowState, continueAiScreen, enterScreenStep, fetchMergedLatestResult, finishPausedTask, isLoginErrorCode, jobId, loadLatestResult, notify, restoreRunningTask, setPipelineResult, showLoginGuide, startAiScreen } = deps;
  let lastCancellationCleanupFailed = false;

  function clearScrapeRecoveryMarkers(): void {
    pausedRunId.value = "";
    interruptedRunId.value = "";
  }

  /**
   * Spec041 返工：任务状态查询一律带当前画像，后端据此校验任务归属；
   * 缺画像时只做旧口径兼容（产品前端不会走到这条）。
   */
  function taskStateUrl(runId: string): string {
    const profile = profileId.value ? `?profile_id=${encodeURIComponent(profileId.value)}` : "";
    return `/api/task-state/${encodeURIComponent(runId)}${profile}`;
  }


async function pollTask(taskId: string, kind: "scrape" | "screen") {
  const roundEpoch = workflowEpoch.value;
  try {
    const data = await apiRequest<TaskSnapshot>(taskStateUrl(taskId));
    if (roundEpoch !== workflowEpoch.value) return;
    // 暂停请求已发出但后端仍在等当前批次结束：保持“正在暂停”，
    // 不被旧的运行态轮询覆盖，也不提前进入完成态。
    if (kind === "screen" && pausingScreen.value && data.status !== "paused"
        && data.status !== "failed" && data.status !== "cancelled"
        && data.status !== "interrupted" && !isCompletedTaskStatus(data.status)) {
      screenSnapshot.value = {
        ...data,
        status: "pausing",
        progress: { ...(data.progress || {}), message: "正在暂停…" },
      };
      pollTimer.value = window.setTimeout(() => void pollTask(taskId, kind), 1800);
      return;
    }
    // 抓取暂停同型保护：暂停请求已受理、后端仍在等当前组合结束时，保持
    // “正在暂停”显示（按钮持续变灰）；任务到达终态时才释放按钮占用。
    if (kind === "scrape" && scrapeActionBusy.value === "pause-scrape") {
      const pauseSettled = data.status === "paused" || data.status === "failed"
        || data.status === "cancelled" || data.status === "interrupted"
        || isCompletedTaskStatus(data.status);
      if (!pauseSettled) {
        scrapeSnapshot.value = {
          ...data,
          status: "pausing",
          progress: { ...(data.progress || {}), message: "正在暂停…" },
        };
        pollTimer.value = window.setTimeout(() => void pollTask(taskId, kind), 1800);
        return;
      }
      scrapeActionBusy.value = "";
    }
    // 016：恢复首拍防御——轮询响应未带整体进度/计数时沿用上一拍断点值，
    // 避免续跑/刷新后进度条"归零再跳变"；新任务起步快照本身无进度，不受影响。
    const applyProgressFloor = (incoming: TaskSnapshot, previous?: TaskSnapshot | null): TaskSnapshot => {
      const prev = (previous?.progress || {}) as Record<string, unknown>;
      const next = (incoming.progress || {}) as Record<string, unknown>;
      const patch: Record<string, unknown> = {};
      for (const key of ("overall_percent current total" as const).split(" ")) {
        if (next[key] == null && typeof prev[key] === "number") {
          patch[key] = prev[key];
        }
      }
      return Object.keys(patch).length
        ? { ...incoming, progress: { ...next, ...patch } }
        : incoming;
    };
    if (kind === "scrape") scrapeSnapshot.value = applyProgressFloor(data, scrapeSnapshot.value);
    else screenSnapshot.value = applyProgressFloor(data, screenSnapshot.value);

    const integrityConclusion = data.integrity?.conclusion || "";
    // The public status keeps completed_with_pending for compatibility, but
    // an unverifiable/failed/interrupted whitebox conclusion is authoritative.
    if (["unverifiable", "failed", "interrupted"].includes(integrityConclusion)) {
      if (kind === "scrape") {
        scrapeBusy.value = false;
        clearScrapeRecoveryMarkers();
        scrapeSnapshot.value = data;
      } else {
        screenBusy.value = false;
        pausingScreen.value = false;
        // AI 错误/完整性异常只保留事实快照，不继续占用公共任务槽。
        pausedRunId.value = "";
        interruptedRunId.value = "";
        screenSnapshot.value = data;
      }
      deps.notify(
        data.integrity?.primary_reason
          || (integrityConclusion === "failed" ? "执行失败"
            : integrityConclusion === "interrupted" ? "任务已中断" : "无法确认是否完成"),
        integrityConclusion === "failed" ? "error" : "warning",
      );
      return;
    }
    if (isCompletedTaskStatus(data.status)) {
      pollRetryCount.value = 0;
      restoredTaskHint.value = "";
      const hasJobs = typeof data.scraped_count === "number" ? data.scraped_count > 0 : true;
      const shouldAutoScreen = kind === "scrape" && (autoScreenArmed.value || data.auto_screen === true) && hasJobs;
      autoScreenArmed.value = false;
      if (kind === "scrape") {
        scrapeBusy.value = false;
        scrapeCompleted.value = true;
        let noticeMessage: string;
        if (integrityConclusion === "empty") {
          noticeMessage = "已完成，没有找到岗位，可调整条件后重试";
        } else if (shouldAutoScreen) {
          noticeMessage = data.status === "completed_with_pending"
            ? "抓取完成，正在自动开始 AI 筛选，部分岗位待确认"
            : "抓取完成，正在自动开始 AI 筛选";
        } else {
          noticeMessage = data.status === "completed_with_pending"
            ? "抓取完成，但有待确认，请继续检查筛选条件"
            : "抓取完成，请继续确认 AI 筛选条件";
        }
        deps.notify(
          noticeMessage,
          data.status === "completed_with_pending" ? "warning" : "success",
        );
        // B038：单独抓取完成即自动保存未筛选轮，刷新后不再依赖手动"直接查看结果"。
        if (kind === "scrape" && !shouldAutoScreen && hasJobs) {
          await saveScrapedOnlySnapshot();
          if (roundEpoch !== workflowEpoch.value) return;
        }
        if (shouldAutoScreen) {
          if (roundEpoch !== workflowEpoch.value) return;
          deps.enterScreenStep();
          await deps.startAiScreen({ consumeAutoScreen: true, fields: autoScreenFields.value, profile: autoScreenProfile.value });
        }
      } else {
        screenBusy.value = false;
        pausingScreen.value = false;
        // 实时路径与刷新路径统一：任务完成后拉当前画像的全局最新轮。
        const fetched = await deps.fetchMergedLatestResult();
        if (roundEpoch !== workflowEpoch.value) return;
        if (fetched) {
          deps.setPipelineResult(fetched.merged);
          currentRoundStatus.value = fetched.newer.data.status === "scraped_only" ? "scraped_only" : "screened";
          if (isScrapedOnly.value) {
            activeCategory.value = "matched";
            // 任务接口可能返回的是上一份筛选快照；结果接口确认本轮
            // 其实只是抓取完成时，03 页必须显示“未开始 N”，不能沿用旧的 2 / 2。
            const total = Number(fetched.merged.total_scraped || fetched.merged.jobs?.length || 0);
            screenSnapshot.value = {
              ...(screenSnapshot.value || {}),
              status: "completed",
              stage: "scrape",
              total,
              source_total: total,
              success_count: 0,
              fail_count: 0,
              unstarted_count: total,
              pending_count: 0,
            };
          }
        } else {
          // B044：最新轮拉取失败不得让 04 空白——完成响应自带的内存结果
          //（后端与 status=done 同锁写入，完成瞬间必然携带）立即兜底，
          // 保证流程完成后 04 页必然有岗位展示；随后后台有界补拉持久化结果。
          const inline = data.result;
          if (inline && Array.isArray(inline.jobs)) {
            deps.setPipelineResult({
              ...inline,
              // 完成响应的外层 platform 是单平台任务的权威身份；旧后端
              // 可能只在外层返回它，不能因内联兜底丢掉主题平台。
              platform: inline.platform || data.platform,
            });
            currentRoundStatus.value = "screened";
          }
          retryMergeUpgrade(taskId, 0);
        }
        if (historyMode.value) {
          // 035：后台任务跑完时用户在看历史——不切走历史视图，顶部冒泡提示本轮完成。
          taskCompletedToast.value = { visible: true };
          // 刷新历史列表，让刚完成的轮次以已完成状态出现在历史中。
          void historyStore.loadHistory({ silent: true });
        } else {
          activeStep.value = "results";
        }
        deps.notify(
          data.status === "completed_with_pending"
            ? "部分完成，部分结果可能缺失"
            : "AI 筛选完成",
          data.status === "completed_with_pending" ? "warning" : "success",
        );
      }
      return;
    }
    if (data.status === "cancelled") {
      pollRetryCount.value = 0;
      restoredTaskHint.value = "";
      if (kind === "scrape") scrapeBusy.value = false;
      else screenBusy.value = false;
      if (kind === "screen") pausingScreen.value = false;
      pausedRunId.value = "";
      interruptedRunId.value = "";
      // 不弹 error 通知：deps.cancelScrape 已经弹过了；这里是轮询兜底（如刷新后接回的取消态）
      return;
    }
    if (data.status === "paused") {
      pollRetryCount.value = 0;
      if (kind === "scrape") {
        scrapeBusy.value = false;
        pausedRunId.value = taskId;
        scrapeSnapshot.value = data;
      }
      else screenBusy.value = false;
      if (kind === "screen") {
        pausingScreen.value = false;
        pausedRunId.value = taskId;
        void deps.loadLatestResult();
      }
      deps.notify(data.error || "任务已暂停，请处理后点继续", "warning");
      return;
    }
    if (data.status === "failed") {
      pollRetryCount.value = 0;
      restoredTaskHint.value = "";
      if (kind === "scrape") {
        scrapeBusy.value = false;
        clearScrapeRecoveryMarkers();
      } else {
        screenBusy.value = false;
        pausedRunId.value = "";
        interruptedRunId.value = "";
      }
      if (kind === "screen") pausingScreen.value = false;
      deps.notify(data.error || "任务执行失败", "error");
      // D7：任务因未登录失败时给出账号级登录引导。
      if (kind === "scrape" && deps.isLoginErrorCode(data.pause_info?.error_code)) {
        void deps.showLoginGuide(data.platform || draftPlatform.value);
      }
      return;
    }
    if (data.status === "interrupted") {
      // 服务重启打断：工作线程已死，不能继续轮询；停止 busy 并回到可操作的中断态。
      pollRetryCount.value = 0;
      if (kind === "scrape") {
        scrapeBusy.value = false;
        clearScrapeRecoveryMarkers();
        scrapeTaskId.value = taskId;
        analysisReady.value = true;
        activeStep.value = "search";
        restoredTaskHint.value = "上次抓取因服务重启被中断；已抓数据已保存，可结束保存结果或重新开始抓取";
      } else {
        // 服务中断属于错误终态：保留快照和查看入口，但不把它登记为
        // 可恢复占用，避免切平台/新任务被旧 AI 任务卡住。
        pausedRunId.value = "";
        interruptedRunId.value = "";
        screenBusy.value = false;
        screenTaskId.value = taskId;
        pausingScreen.value = false;
        analysisReady.value = true;
        deps.enterScreenStep();
        restoredTaskHint.value = "上次 AI 筛选因服务重启被中断；重新开始 AI 筛选会接着上次进度，不重复消耗";
      }
      data.progress = { ...(data.progress || {}), message: "任务因服务重启被中断，已保存进度" };
      if (kind === "scrape") scrapeSnapshot.value = data;
      else screenSnapshot.value = data;
      return;
    }
    pollTimer.value = window.setTimeout(() => void pollTask(taskId, kind), 1800);
  } catch (error) {
    if (roundEpoch !== workflowEpoch.value) return;
    pollRetryCount.value += 1;
    if (pollRetryCount.value > POLL_MAX_RETRIES) {
      // 达上限，主动放弃
      pollRetryCount.value = 0;
      if (kind === "scrape") scrapeBusy.value = false;
      else screenBusy.value = false;
      if (kind === "scrape") clearScrapeRecoveryMarkers();
      else {
        pausedRunId.value = "";
        interruptedRunId.value = "";
      }
      const failed: TaskSnapshot = {
        status: "failed",
        progress: { message: "任务执行失败" },
        logs: [],
        error: "进度获取连续失败，请检查网络后重试",
      };
      if (kind === "scrape") scrapeSnapshot.value = failed;
      else screenSnapshot.value = failed;
      deps.notify("进度获取连续失败，请检查网络后重试", "error");
      return;
    }
    const delay = Math.min(POLL_BASE_DELAY * 2 ** (pollRetryCount.value - 1), POLL_MAX_DELAY);
    const retrying: TaskSnapshot = {
      status: "running",
      // 文案改温和：大多数情况是后端正忙没及时回，不是真失败
      progress: { message: `正在获取进度（${pollRetryCount.value}/${POLL_MAX_RETRIES}）…` },
      logs: [],
      error: "",
    };
    if (kind === "scrape") scrapeSnapshot.value = retrying;
    else screenSnapshot.value = retrying;
    pollTimer.value = window.setTimeout(() => void pollTask(taskId, kind), delay);
  }
}

// B044：AI 筛选完成瞬间最新轮拉取失败的补拉。任务已完成（04 已用
// 内嵌内存结果展示），后台再按指数退避补拉持久化结果；期间开始新任务（task id 变化 / 任意 pipeline
// 占用）立即放弃，绝不串轮。复用 pollTimer 让既有清理路径（resetWorkflow
// / unmount / cancel / finish）统一收口。
function retryMergeUpgrade(taskId: string, attempt: number, roundEpoch = workflowEpoch.value) {
  if (attempt > POLL_MAX_RETRIES) return;
  if (pollTimer.value) window.clearTimeout(pollTimer.value);
  // 新一轮已开始（task id 变化或任意 pipeline 占用）→ 放弃补拉。
  if (roundEpoch !== workflowEpoch.value || screenTaskId.value !== taskId || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return;
  const delay = Math.min(POLL_BASE_DELAY * 2 ** attempt, POLL_MAX_DELAY);
  pollTimer.value = window.setTimeout(() => {
    pollTimer.value = undefined;
    void deps.fetchMergedLatestResult().then((fetched: MergedLatestResult | null) => {
      if (roundEpoch !== workflowEpoch.value) return;
      if (!fetched) {
        retryMergeUpgrade(taskId, attempt + 1, roundEpoch);
        return;
      }
      // 应用前复查：期间开始新任务则丢弃这次补拉结果。
      if (roundEpoch !== workflowEpoch.value || screenTaskId.value !== taskId || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return;
      deps.setPipelineResult(fetched.merged);
      currentRoundStatus.value = fetched.newer.data.status === "scraped_only" ? "scraped_only" : "screened";
      if (isScrapedOnly.value) activeCategory.value = "matched";
    });
  }, delay);
}

// 039（用户拍板·数字永久展示）：本轮定格为“已抓取，未筛选”时，03 面板同样要
// 给出真实计数——已完成 0 / N、未开始 N（N = 本轮已抓岗位，即筛选工作单元），
// 与刷新恢复后的合成快照同口径；不得以“没有筛选单元”为由让面板没有数字。
function markScrapedOnlyScreenCounts(total: number): void {
  const count = Math.max(0, Number(total) || 0);
  const snap = scrapeSnapshot.value;
  screenSnapshot.value = {
    status: "completed",
    stage: "done",
    progress: { message: "已抓取，未筛选" },
    logs: [],
    total: count,
    success_count: 0,
    fail_count: 0,
    unstarted_count: count,
    source_total: count,
    scraped_count: count,
    pending_count: 0,
    platform: snap?.platform,
    integrity: snap?.integrity || null,
    started_at: snap?.started_at,
    finished_at: snap?.finished_at,
  };
}

// B038：把抓取结果固化为"已抓取，未筛选"轮，供自动保存与手动查看共用。
async function saveScrapedOnlySnapshot(markViewed = false): Promise<"saved" | "zero" | "failed"> {
  const roundEpoch = workflowEpoch.value;
  if (!scrapeTaskId.value) return "failed";
  const emptyResult = (): PipelineResult => ({
    ok: true, jobs: [], dropped: [],
    total_scraped: 0, total_kept: 0, total_matched: 0, total_dropped: 0,
    profile_summary: profileSummary.value, error: "",
    integrity: scrapeSnapshot.value?.integrity || null,
  });
  const snap = scrapeSnapshot.value;
  const scrapedCount = Number(
    snap?.scraped_count ?? snap?.source_total ?? snap?.result?.total_scraped ?? -1,
  );
  if (scrapedCount === 0) {
    deps.setPipelineResult(emptyResult());
    markScrapedOnlyScreenCounts(0);
    if (!markViewed) resultLoaded.value = false;
    // 本轮已是“已抓取，未筛选”：03 面板的开始动作必须保持可用（screenStatus
    // 依赖该次级状态，completed 快照才会派生 start 而不是“已完成无动作”）。
    currentRoundStatus.value = "scraped_only";
    return "zero";
  }
  try {
    const data = await apiRequest<{
      saved?: boolean; run_id?: string; result?: PipelineResult;
    }>("/api/scrape-result-save", {
      method: "POST",
      json: {
        task_id: scrapeTaskId.value,
        profile_summary: profileSummary.value,
        profile_facts: profileFacts.value,
      },
    });
    if (roundEpoch !== workflowEpoch.value) return "failed";
    if (data.saved && data.result) deps.setPipelineResult(data.result);
    else deps.setPipelineResult(emptyResult());
    markScrapedOnlyScreenCounts(
      Number(data.result?.total_scraped ?? scrapedCount ?? 0),
    );
    if (!markViewed) resultLoaded.value = false;
    // 本轮已是“已抓取，未筛选”：03 面板的开始动作必须保持可用（screenStatus
    // 依赖该次级状态，completed 快照才会派生 start 而不是“已完成无动作”）。
    currentRoundStatus.value = "scraped_only";
    return "saved";
  } catch (error) {
    deps.notify(errorMessage(error, "保存结果失败"), "error");
    return "failed";
  }
}


async function viewScrapedOnly() {
  const outcome = await saveScrapedOnlySnapshot(true);
  if (outcome === "failed") return;
  activeCategory.value = "matched";
  activeStep.value = "results";
  deps.notify(
    outcome === "zero" ? "本轮没有抓到岗位，可回到第二步重新抓取" : "已保存本轮抓取结果（已抓取，未筛选）",
    outcome === "zero" ? "warning" : "success",
  );
}

// 指数退避：7 次 / 64s 上限。前 5 次快速重试（4s→8s→16s→32s→64s），
// 后 2 次保持 64s，总等待约 4 分钟。达上限后主动放弃并提示用户。


async function cancelActiveTasksForNewRound(silent = false): Promise<boolean> {
  lastCancellationCleanupFailed = false;
  const ids = new Set<string>();
  for (const id of [
    scrapeTaskId.value, screenTaskId.value, recrawlTaskId.value,
    pausedRunId.value, interruptedRunId.value,
  ]) {
    if (id) ids.add(id);
  }
  if (!ids.size) {
    try {
      // Spec041：兜底也只取消当前画像的任务；服务端按画像过滤，
      // 返回体再核对一次身份，任何情况下都不跨画像取消。
      const latest = await apiRequest<{
        has_task?: boolean; task_id?: string; profile_id?: string | null;
      }>(
        profileId.value
          ? `/api/latest-running-task?profile_id=${encodeURIComponent(profileId.value)}`
          : "/api/latest-running-task",
      );
      const returnedProfile = String(latest.profile_id || "");
      const sameProfile = !returnedProfile
        || returnedProfile === String(profileId.value || "");
      if (latest.has_task && latest.task_id && sameProfile) ids.add(latest.task_id);
    } catch { /* 接回失败不阻断归档 */ }
  }
  let cancelled = false;
  for (const id of ids) {
    try {
      const data = await apiRequest<CleanupActionResponse>(
        `/api/task/cancel/${encodeURIComponent(id)}`,
        { method: "POST" },
      );
      if (hasCleanupFailure(data)) lastCancellationCleanupFailed = true;
      cancelled = true;
    } catch (error) {
      const payload = (error as ApiError).payload as {
        error?: string;
        cleanup_error?: string;
      } | undefined;
      if (payload?.error === "browser_cleanup_failed"
          || payload?.cleanup_error === "browser_cleanup_failed") {
        lastCancellationCleanupFailed = true;
        cancelled = true;
        continue;
      }
      if (payload?.error && [
        "already_finished", "run_not_found", "task_not_active", "not_paused",
      ].includes(payload.error)) {
        continue;
      }
      deps.notify(errorMessage(error, "结束旧任务失败，已停止开始新一轮"), "error");
      return false;
    }
  }
  // 取消接口确认后，先把本地任务槽收口为非活动终态，再清理最新结果。
  // 否则 clearLatestResult 会继续看到旧的 paused/running 快照，拒绝归档，
  // “放弃本轮”就会出现按钮点了但现场仍留在原步骤的假成功。
  const markCancelled = (
    snapshot: TaskSnapshot | null,
    message: string,
  ): TaskSnapshot => ({
    ...(snapshot || {}),
    status: "cancelled",
    progress: { ...((snapshot || {}).progress || {}), message },
    logs: snapshot?.logs || [],
  });
  if (scrapeTaskId.value && ids.has(scrapeTaskId.value)) {
    scrapeBusy.value = false;
    scrapeActionBusy.value = "";
    scrapeSnapshot.value = markCancelled(scrapeSnapshot.value, "本轮已放弃");
  }
  if (screenTaskId.value && ids.has(screenTaskId.value)) {
    screenBusy.value = false;
    pausingScreen.value = false;
    screenSnapshot.value = markCancelled(screenSnapshot.value, "本轮已放弃");
  }
  if (recrawlTaskId.value && ids.has(recrawlTaskId.value)) {
    recrawlBusy.value = false;
    recrawlSnapshot.value = markCancelled(recrawlSnapshot.value, "本轮已放弃");
  }
  if (pausedRunId.value && ids.has(pausedRunId.value)) pausedRunId.value = "";
  if (interruptedRunId.value && ids.has(interruptedRunId.value)) interruptedRunId.value = "";
  if (cancelled && !silent) {
    deps.notify(
      lastCancellationCleanupFailed ? "任务已停止，但浏览器清理失败" : "已结束旧任务，开始新一轮",
      lastCancellationCleanupFailed ? "error" : "info",
    );
  }
  return true;
}


async function finishScreenSave() {
  if (deps.roundFlow.busyAction) return;
  const runId = screenTaskId.value || pausedRunId.value;
  if (!runId) return;
  // 025 B076：正处抓 JD 批次中 → 弹「等这批抓完再保存 / 立即保存」二选一。
  if (deps.roundFlow.openScreenFinishChoice()) return;
  deps.roundFlow.busyAction = "finish";
  try {
    await deps.finishPausedTask(runId);
  } finally {
    deps.roundFlow.busyAction = "";
  }
}

// B038：把抓取结果固化为"已抓取，未筛选"轮，供自动保存与手动查看共用。


function isCompletedTaskStatus(status?: string) {
  return Boolean(status && COMPLETED_TASK_STATUSES.has(status));
}

// 切片7：paused 任务从 /api/task-state 拉完整计数（FR-037）

// 切片7：paused 任务从 /api/task-state 拉完整计数（FR-037）
async function enrichPausedSnapshot(
  runId: string,
  snapshot: TaskSnapshot,
  kind: "scrape" | "screen" | "recrawl",
) {
  try {
    const data = await apiRequest<{
      status?: string;
      stage?: string;
      progress?: number | Record<string, unknown>;
      success_count?: number;
      fail_count?: number;
      unstarted_count?: number;
      total?: number;
      kept_count?: number;
      dropped_count?: number;
      pending_count?: number;
      source_total?: number;
      scraped_count?: number;
      pause_info?: { error_code?: string; error_reason?: string } | null;
      execution_config?: Record<string, unknown> | null;
      integrity?: IntegritySnapshot | null;
      result?: { updates?: Record<string, unknown> } | null;
    }>(taskStateUrl(runId));
    snapshot.success_count = data.success_count;
    snapshot.fail_count = data.fail_count;
    snapshot.unstarted_count = data.unstarted_count;
    snapshot.total = data.total;
    snapshot.kept_count = data.kept_count;
    snapshot.dropped_count = data.dropped_count;
    snapshot.pending_count = data.pending_count;
    snapshot.source_total = data.source_total;
    snapshot.scraped_count = data.scraped_count;
    snapshot.stage = data.stage || snapshot.stage;
    if (typeof data.progress === "number") {
      snapshot.progress = {
        ...(snapshot.progress || {}),
        overall_percent: data.progress,
      };
    } else if (data.progress) {
      // 016：恢复首拍防御——后端未带整体进度时沿用上一拍断点值，
      // 避免进度条"归零再跳变"；新值到达后按新值覆盖。
      const prevProgress = snapshot.progress || {};
      snapshot.progress = { ...data.progress };
      if (snapshot.progress.overall_percent == null
        && typeof prevProgress.overall_percent === "number") {
        snapshot.progress.overall_percent = prevProgress.overall_percent;
      }
      if (snapshot.progress.current == null
        && typeof prevProgress.current === "number") {
        snapshot.progress.current = prevProgress.current;
      }
      if (snapshot.progress.total == null
        && typeof prevProgress.total === "number") {
        snapshot.progress.total = prevProgress.total;
      }
    }
    if (data.pause_info) snapshot.pause_info = data.pause_info;
    if (data.execution_config) snapshot.execution_config = data.execution_config;
    if (data.integrity) snapshot.integrity = data.integrity;
    if (data.result?.updates) mergeRecrawlUpdates(data.result.updates);
  } catch { /* 退化到 progress 字段 */ }
  if (kind === "scrape") {
    scrapeTaskId.value = runId;
    scrapeSnapshot.value = { ...snapshot };
  } else if (kind === "screen") {
    screenTaskId.value = runId;
    screenSnapshot.value = { ...snapshot };
  } else {
    recrawlTaskId.value = runId;
    recrawlSnapshot.value = { ...snapshot };
  }
}

// T505/T509：按指定平台加载 schema（/api/filter-labels?platform=）。
// schemaLoader 内部用单调 reqId + AbortController + 响应平台校验，
// 保证旧平台响应晚到不覆盖当前平台（platform-schema.md L151-156）。
// T509：默认参数 = 草稿平台（新任务表单/简历建议路径）；deps.restoreRunningTask 显式传入任务平台
// 以满足 platform-schema.md L157「先从任务响应设置任务平台，再加载对应 schema/城市」。

// 待确认项「全部重抓」：缺 JD 的补 CDP 抓取，有 JD 的用画像重跑 AI 精筛。
// 复用现有轮询机制显示进度（已完成 X / 共 N），结果原地合并进当前结果，保留当前 tab。
async function recrawlUncertain(platformOverride?: "boss" | "zhilian") {
  let filter = platformOverride || resultPlatformFilter.value;
  // “全部”视图不发起混合重抓：仅当两个平台都有待确认岗位时才引导选择；
  // 只有一个平台有待确认岗位时直接用该平台，无需用户再选一次。
  if (filter === "all") {
    const bossCount = Number(uncertainByPlatform.value.boss || 0);
    const zhilianCount = Number(uncertainByPlatform.value.zhilian || 0);
    if (bossCount > 0 && zhilianCount > 0) {
      recrawlPlatformGuide.value = { ...uncertainByPlatform.value };
      return;
    }
    filter = bossCount > 0 ? "boss" : "zhilian";
  }
  const ids = groups.value.uncertain
    .filter((job) => job.platform === filter)
    .map((job) => deps.jobId(job))
    .filter(Boolean);
  if (!ids.length) {
    deps.notify("没有待确认的岗位", "info");
    return;
  }
  if (recrawlBusy.value) return;
  // 单平台重抓也留在结果页，后台状态由进度卡和灵动岛展示。
  recrawlBusy.value = true;
  recrawlPlatformGuide.value = null;
  recrawlSnapshot.value = {
    status: "running",
    progress: { message: `准备重抓 ${ids.length} 个待确认岗位…` },
    logs: [],
    error: "",
  };
  interruptedRunId.value = "";
  const roundEpoch = workflowEpoch.value;
  try {
    const data = await apiRequest<{ task_id: string }>("/api/pipeline/recrawl", {
      method: "POST",
      json: {
        // 单平台视图按岗位自身来源 run 重抓，不跨平台混合。
        source_run_id: resultRunIds.value[filter] || pipelineResultRunId.value,
        job_ids: ids,
        // 历史轮次必须使用该轮落盘画像，不能把当前工作流画像带入旧轮次；
        // runner 收到空画像后会按 source_run_id 从原轮次恢复。
        profile_summary: historyMode.value ? "" : profileSummary.value,
        profile_facts: historyMode.value ? null : profileFacts.value,
      },
    });
    if (roundEpoch !== workflowEpoch.value) return;
    recrawlTaskId.value = data.task_id;
    await pollRecrawl(data.task_id, roundEpoch);
  } catch (error) {
    if (roundEpoch !== workflowEpoch.value) return;
    const message = errorMessage(error, "重抓启动失败");
    recrawlBusy.value = false;
    // 启动请求未返回 task_id，后端不会有可继续/停止的任务。清掉临时
    // 重抓状态，但不擅自改变用户当前所在页面。
    recrawlTaskId.value = "";
    recrawlSnapshot.value = null;
    deps.notify(message, "error");
  }
}


function chooseRecrawlPlatform(platform: "boss" | "zhilian") {
  recrawlPlatformGuide.value = null;
  resultPlatformFilter.value = platform;
  void deps.roundFlow.startRecrawl(platform);
}


async function continueRecrawl() {
  if (!recrawlTaskId.value || recrawlBusy.value) return;
  const taskId = recrawlTaskId.value;
  const roundEpoch = workflowEpoch.value;
  recrawlBusy.value = true;
  restoredTaskHint.value = "";
  interruptedRunId.value = "";
  const recrawlResumeProgress = { ...(recrawlSnapshot.value?.progress || {}) };
  recrawlResumeProgress.message = "正在从重抓断点继续…";
  recrawlSnapshot.value = {
    status: "running",
    progress: recrawlResumeProgress,
    logs: recrawlSnapshot.value?.logs || [],
  };
  try {
    const data = await apiRequest<{ task_id?: string }>(
      `/api/task/continue/${encodeURIComponent(taskId)}`,
      { method: "POST" },
    );
    if (roundEpoch !== workflowEpoch.value) return;
    pausedRunId.value = "";
    recrawlTaskId.value = data.task_id || taskId;
    recrawlRetryCount.value = 0;
    await pollRecrawl(recrawlTaskId.value, roundEpoch);
  } catch (error) {
    if (roundEpoch !== workflowEpoch.value) return;
    recrawlBusy.value = false;
    // 继续重抓失败：回到报错暂停时的样子（与 deps.continueAiScreen 同一恢复路径）。
    const restored = await apiRequest<TaskSnapshot>(taskStateUrl(taskId)).catch(() => null);
    if (roundEpoch !== workflowEpoch.value) return;
    recrawlSnapshot.value = restored
      ? { ...restored, status: "paused" }
      : { ...(recrawlSnapshot.value || {}), status: "paused", error: errorMessage(error, "重抓断点继续失败") };
  }
}


async function pollRecrawl(taskId: string, roundEpoch = workflowEpoch.value) {
  if (roundEpoch !== workflowEpoch.value) return;
  try {
    const data = await apiRequest<TaskSnapshot>(taskStateUrl(taskId));
    if (roundEpoch !== workflowEpoch.value) return;
    recrawlSnapshot.value = data;
    const liveUpdates = (data.result as unknown as { updates?: Record<string, unknown> } | undefined)?.updates;
    if (liveUpdates) mergeRecrawlUpdates(liveUpdates as Record<string, unknown>);
    const integrityConclusion = data.integrity?.conclusion || "";
    if (["unverifiable", "failed", "interrupted"].includes(integrityConclusion)) {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      pausedRunId.value = "";
      interruptedRunId.value = "";
      if (integrityConclusion === "interrupted") {
        recrawlTaskId.value = taskId;
        restoredTaskHint.value = "上次补抓因服务重启被中断；可结束保存已有结果";
      }
      deps.notify(
        data.integrity?.primary_reason
          || (integrityConclusion === "failed" ? "重抓失败"
            : integrityConclusion === "interrupted" ? "重抓已中断" : "无法确认重抓是否完成"),
        integrityConclusion === "failed" ? "error" : "warning",
      );
      return;
    }
    if (isCompletedTaskStatus(data.status)) {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      const updates = (data.result as unknown as { updates?: Record<string, unknown> } | undefined)?.updates;
      if (updates) mergeRecrawlUpdates(updates as Record<string, unknown>);
      const fetched = await deps.fetchMergedLatestResult();
      if (roundEpoch !== workflowEpoch.value) return;
      if (fetched) {
        // 最新结果请求可能先于重抓写回完成，不能让旧快照把本次已确认的
        // 判定和风险标记盖掉；把本次任务返回的更新再合到新快照上。
        if (updates) applyRecrawlUpdates(fetched.merged, updates as Record<string, unknown>);
        deps.setPipelineResult(fetched.merged);
      }
      deps.notify(
        data.status === "completed_with_pending" || data.status === "partial"
          ? String(data.progress?.message || "重抓完成，但仍有岗位待确认")
          : "待确认岗位已重抓完成",
        data.status === "completed_with_pending" || data.status === "partial"
          ? "warning"
          : "success",
      );
      window.setTimeout(() => {
        if (roundEpoch === workflowEpoch.value) recrawlSnapshot.value = null;
      }, 3000);
      return;
    }
    if (data.status === "cancelled") {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      pausedRunId.value = "";
      interruptedRunId.value = "";
      deps.notify("已停止重抓", "warning");
      window.setTimeout(() => {
        if (roundEpoch === workflowEpoch.value) recrawlSnapshot.value = null;
      }, 3000);
      return;
    }
    if (data.status === "paused") {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      pausedRunId.value = taskId;
      deps.notify(data.error || "重抓已暂停，请处理后点继续", "warning");
      return;
    }
    if (data.status === "failed") {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      pausedRunId.value = "";
      interruptedRunId.value = "";
      deps.notify(data.error || "重抓失败", "error");
      window.setTimeout(() => {
        if (roundEpoch === workflowEpoch.value) recrawlSnapshot.value = null;
      }, 5000);
      return;
    }
    if (data.status === "interrupted") {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      pausedRunId.value = "";
      interruptedRunId.value = "";
      recrawlTaskId.value = taskId;
      restoredTaskHint.value = "上次补抓因服务重启被中断；可结束保存已有结果";
      data.progress = { ...(data.progress || {}), message: "任务因服务重启被中断，已保存进度" };
      recrawlSnapshot.value = data;
      return;
    }
    pollTimer.value = window.setTimeout(() => void pollRecrawl(taskId, roundEpoch), 1800);
  } catch (error) {
    if (roundEpoch !== workflowEpoch.value) return;
    recrawlRetryCount.value += 1;
    if (recrawlRetryCount.value > POLL_MAX_RETRIES) {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      pausedRunId.value = "";
      interruptedRunId.value = "";
      recrawlSnapshot.value = {
        status: "failed",
        progress: { message: "重抓进度获取连续失败" },
        logs: [],
        error: "重抓进度获取连续失败，请检查后重试",
      };
      deps.notify("重抓进度获取连续失败，请检查后重试", "error");
      return;
    }
    const delay = Math.min(POLL_BASE_DELAY * 2 ** (recrawlRetryCount.value - 1), POLL_MAX_DELAY);
    pollTimer.value = window.setTimeout(() => void pollRecrawl(taskId, roundEpoch), delay);
  }
}

// 把后端回写的 {jd/verdict/verdict_reason/caveats} 原地合并到当前结果，
// 已解决的项会随 groups 重算自动离开待确认 tab，未解决项原地保留。
function applyRecrawlUpdates(result: (PipelineResult & { jobs?: JobItem[] }) | null, updates: Record<string, unknown>) {
  if (!result || !Array.isArray(result.jobs)) return;
  for (const job of result.jobs) {
    const id = deps.jobId(job);
    const upd = updates[id];
    if (!upd || typeof upd !== "object") continue;
    const map = upd as Record<string, unknown>;
    if (typeof map.jd !== "undefined") job.jd = String(map.jd ?? "");
    if (typeof map.verdict !== "undefined") job.verdict = map.verdict as JobItem["verdict"];
    if (typeof map.verdict_reason !== "undefined") job.verdict_reason = String(map.verdict_reason ?? "");
    if (Array.isArray(map.caveats)) job.caveats = map.caveats as string[];
    if (Array.isArray(map.flags)) job.flags = map.flags as JobItem["flags"];
  }
}

function mergeRecrawlUpdates(updates: Record<string, unknown>) {
  applyRecrawlUpdates(pipelineResult.value as (PipelineResult & { jobs?: JobItem[] }) | null, updates);
}

// ---------------------------------------------------------------------------
// Task 009：详情生命周期控件接入（JobWorkspace actions slot）
// ---------------------------------------------------------------------------

/**
 * 传给 JobLifecycleActions 的岗位身份投影：
 * 权威三元组（job 自身冻结 platform + platform_job_id + canonical_url）完整时
 * 优先按三元组解析，避免把 pipeline pending 映射的平台原始 ID 误当内部 job_id。
 * 三元组不完整时保留原 job（由组件内部阻断写操作），绝不用当前 UI 平台补值。
 */


/**
 * 025 B078：完成态启动/刷新自动「开始新一轮」——复用 resetWorkflow（「开始新一轮」
 * 按钮背后的既有逻辑），不新写恢复/重置代码。判定：
 * - 有未完成流程（本地未完成快照 / 进行中、暂停、中断任务）→ 恢复现场（B068 不变）；
 * - 无进行中任务（含 latest-running-task 返回已完成终态任务）且上一轮已正常走完、
 *   结果已落历史 → 自动 resetWorkflow（01 页开放、02/03/04 自然灰色）。
 */


async function maybeAutoStartNewRound(): Promise<void> {
  // 未完成流程（本地有未完成快照）→ 恢复现场（B068 行为保留，不改）
  if (unfinishedWorkflowRestored.value) return;
  // 035：未结束任务真实存在时，刷新/启动优先恢复现场，不自动开始新一轮、不取消任务。
  if (hasLiveTaskState(state)) return;
  // Spec041 返工（真实验收失败项一）：已完成（已进 04 页 / 结束保存）不再
  // "刷新即自动开新一轮"——那会把刚恢复的当前轮结果与现场清成 01 空上传页。
  // 完成态优先原地接回；会话存档缺失时从后端最新轮补齐；确实取不到结果
  // 才退回 026 的开新一轮，避免用户停在空结果页。
  if (resultsPageSeen.value || finishedPartial.value) {
    if (activeStep.value === "results" && resultLoaded.value && pipelineResult.value) {
      resultsBootstrapPending.value = false;
      return;
    }
    const fetched = await deps.fetchMergedLatestResult();
    if (fetched) {
      await deps.loadLatestResult();
      if (resultLoaded.value && pipelineResult.value) {
        resultsBootstrapPending.value = false;
        activeStep.value = "results";
        return;
      }
    }
    resultsBootstrapPending.value = false;
    await resetWorkflow();
    return;
  }
  // 有恢复的活动任务：仅已完成终态属于完成态 → 自动新一轮；否则恢复现场
  if (activeTaskRestored.value) {
    const taskStatus = String(
      screenSnapshot.value?.status || scrapeSnapshot.value?.status || "",
    );
    const completedTask = FULLY_COMPLETED_TASK_STATUSES.has(taskStatus);
    if (!completedTask) return;
  }
  // 无进行中任务（或已完成终态）：查最新历史轮（只查不设，不糊脸）
  try {
    const fetched = await deps.fetchMergedLatestResult();
    if (!fetched) return;  // 无历史轮（全新用户）→ 保持干净 01 页
    // 025 B078：任一平台最新轮为未完成态（暂停/中断/已抓未筛选/未知）→
    // 属"有未完成流程"→ 恢复现场（B068 不变）；全部完成态才自动新一轮。
    const platformStatuses = Object.values(fetched.platformStatuses ?? {})
      .map((s) => String(s ?? ""));
    const anyUnfinished = platformStatuses.some(
      (s) => !s || !FULLY_COMPLETED_TASK_STATUSES.has(s),
    );
    if (platformStatuses.length && !anyUnfinished) {
      // 上一轮已正常走完、结果已落历史 → 自动「开始新一轮」
      //（复用按钮背后逻辑，不糊脸；想看结论去历史）
      await resetWorkflow();
      return;
    }
    // 未完成态（暂停/中断/已抓未筛选/status 缺失）→ 恢复现场（原 loadLatestResult）
    await deps.loadLatestResult();
  } catch {
    // 历史轮查询失败：回退原恢复行为，不误重置
    await deps.loadLatestResult();
  }
}


async function resetWorkflowInternal(silent = false): Promise<boolean> {
  // Spec041：轮次身份由现场存档生成并持有（同一轮跨抓取/筛选/结果稳定）；
  // 这里只做两件事——把旧轮现场归档成该轮历史查看现场，然后把身份换成新轮。
  const oldRunEpoch = sceneStore.roundEpoch.value || sceneStore.ensureRoundEpoch(profileId.value);
  // 历史模式下 pipelineResultRunId 是历史轮的 run id，不能拿它给当前轮归档。
  const oldHistoryRunId = (historyMode.value ? "" : pipelineResultRunId.value)
    || sceneStore.resolveRoundRunId(profileId.value, oldRunEpoch);
  const oldSceneIdentity: SceneIdentity = {
    profileId: profileId.value,
    runEpoch: oldRunEpoch,
    platform: platformState.result
      || screenSnapshot.value?.platform
      || scrapeSnapshot.value?.platform
      || draftPlatform.value,
  };
  // 先使旧轮所有尚未返回的请求失效，再等待取消/归档；否则旧轮响应可能
  // 在清空现场后重新写回结果页。
  workflowEpoch.value += 1;
  // 先停掉旧轮询，避免取消/归档等待期间旧任务回调把已清空的现场写回来。
  if (pollTimer.value) {
    window.clearTimeout(pollTimer.value);
    pollTimer.value = undefined;
  }
  if (!(await cancelActiveTasksForNewRound(silent))) return false;
  if (!(await deps.clearLatestResult())) return false;
  sceneStore.getCurrent(oldSceneIdentity);
  sceneStore.archiveCurrentForNewRound(oldRunEpoch, oldHistoryRunId, oldSceneIdentity.platform);
  sceneStore.rotateRoundEpoch(profileId.value);
  deps.clearWorkflowState();
  // 026 B078：开始新一轮即清除持久化的已结束事实。
  deps.clearFinishedState?.();
  resultsPageSeen.value = false;
  activeStep.value = "upload";
  analysisReady.value = false;
  scrapeCompleted.value = false;
  resultLoaded.value = false;
  selectedFile.value = null;
  aiConsent.value = false;
  scrapeTaskId.value = "";
  scrapeSnapshot.value = null;
  screenTaskId.value = "";
  screenSnapshot.value = null;
  recrawlTaskId.value = "";
  recrawlSnapshot.value = null;
  pipelineResult.value = null;
  pipelineResultRunId.value = "";
  resultPlatformFilter.value = "all";
  finishedPartial.value = false;
  recrawlPlatformGuide.value = null;
  deps.roundFlow.clearRoundContext();
  resultRunIds.value = { boss: "", zhilian: "" };
  platformState.setResultPlatform(null);
  setThemePlatform(draftPlatform.value);
  activeCategory.value = "matched";
  rejectedIds.value = new Set();
  pausedRunId.value = "";
  interruptedRunId.value = "";
  restoredTaskHint.value = "";
  currentRoundStatus.value = "";
  scopePreview.value = null;
  scopePreviewBusy.value = false;
  autoScreenArmed.value = false;
  oneClickOpen.value = false;
  profileError.value = "";
  // 第 2 页输入（关键词/城市/画像/区县）开新一轮不清：用户拍板"回到第 2 页、切平台
  // 再点执行就是复用这些内容"，只有用户手动改才变。
  // T506：第 3 页筛选草稿仍按平台、随新一轮重置。
  filterValues.value = { boss: {}, zhilian: {} };
  resumeAnalysis.value = null;
  resumeAnalysisReset.value?.();
  resumeAnalysisPhase.value = "idle";
  appliedResumePlatforms.value = new Set();
  historyRound.value = null;
  historyBackToLatest();
  scrapeBusy.value = false;
  screenBusy.value = false;
  recrawlBusy.value = false;
  pausingScreen.value = false;
  recrawlRetryCount.value = 0;
  screenPanelOpen.value = true;
  // 新一轮默认：预设档的翻页数回归档位默认（稳定 2 / 平衡 5 / 极限 10）；
  // 用户手动改过的翻页数保存在自定义档，切回预设档即回归默认。
  if (executionSelection.value in MODE_DEFAULT_PAGES) {
    advancedSettings.value.pages = MODE_DEFAULT_PAGES[executionSelection.value];
  }
  return true;
}

async function resetWorkflow() {
  await resetWorkflowInternal();
}

/**
 * 结束当前业务轮次：取消仍占用的任务、归档当前最新结果并清空现场，
 * 让用户明确回到第一步。与“结束并保存结果”不同，这里不把本轮停在结果页。
 */
async function abandonRound(): Promise<void> {
  if (cancelBusy.value || deps.roundFlow.busyAction) return;
  cancelBusy.value = true;
  try {
    const cleared = await resetWorkflowInternal(true);
    if (cleared) {
      deps.notify(
        lastCancellationCleanupFailed
          ? "已放弃本轮，但浏览器清理失败"
          : "已放弃本轮，已回到第一步",
        lastCancellationCleanupFailed ? "error" : "info",
      );
    }
  } finally {
    cancelBusy.value = false;
  }
}

return {
  abandonRound,
  pollTask,
  saveScrapedOnlySnapshot,
  viewScrapedOnly,
  cancelActiveTasksForNewRound,
  finishScreenSave,
  isCompletedTaskStatus,
  enrichPausedSnapshot,
  recrawlUncertain,
  chooseRecrawlPlatform,
  continueRecrawl,
  pollRecrawl,
  mergeRecrawlUpdates,
  maybeAutoStartNewRound,
  resetWorkflow,
};
}

// ---------------------------------------------------------------------------
// 036 B088：顶栏胶囊进度数字提取由 useDiscoveryState 数据层提供（实现在
// 数据层，避免 tasks→state 反向运行时依赖）。本域 re-export 标记「进度
// 数据源自任务快照」，消费方（roundStatusPayload 派生）统一经此处取用。
// ---------------------------------------------------------------------------
export { taskProgressFromSnapshot } from "./useDiscoveryState";
