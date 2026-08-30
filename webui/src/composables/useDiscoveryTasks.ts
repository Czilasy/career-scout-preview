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
import { MODE_DEFAULT_PAGES } from "./useDiscoveryState";
import type { MergedLatestResult, TaskSnapshot } from "./useDiscoveryState";

export function useDiscoveryTasks(state: DiscoveryState, deps: TasksNeeds) {
  const { COMPLETED_TASK_STATUSES, POLL_BASE_DELAY, POLL_MAX_DELAY, POLL_MAX_RETRIES, activeCategory, activeStep, activeTaskRestored, advancedSettings, aiConsent, analysisReady, appliedResumePlatforms, autoScreenArmed, autoScreenFields, autoScreenProfile, cityText, currentRoundStatus, customCity, customKeyword, draftPlatform, executionSelection, filterValues, finishedPartial, groups, historyBackToLatest, historyMode, historyRound, interruptedRunId, isScrapedOnly, keywords, locationDraft, oneClickOpen, pausedRunId, pausingScreen, pipelineResult, pipelineResultRunId, pollRetryCount, pollTimer, profileError, profileFacts, profileSummary, recrawlBusy, recrawlPlatformGuide, recrawlRetryCount, recrawlSnapshot, recrawlTaskId, rejectedIds, restoredTaskHint, resultLoaded, resultPlatformFilter, resultRunIds, resultsPageSeen, resumeAnalysis, schemaLoader, scopePreview, scopePreviewBusy, scrapeBusy, scrapeCompleted, scrapeSnapshot, scrapeTaskId, screenBusy, screenPanelOpen, screenSnapshot, screenTaskId, selectedFile, selectedKeywords, uncertainByPlatform, unfinishedWorkflowRestored } = state;
  const { cancelScrape, clearLatestResult, clearWorkflowState, continueAiScreen, enterScreenStep, fetchMergedLatestResult, finishPausedTask, isLoginErrorCode, jobId, loadLatestResult, notify, restoreRunningTask, setPipelineResult, showLoginGuide, startAiScreen } = deps;


async function pollTask(taskId: string, kind: "scrape" | "screen") {
  try {
    const data = await apiRequest<TaskSnapshot>(`/api/task-state/${encodeURIComponent(taskId)}`);
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
        if (shouldAutoScreen) {
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
        }
        if (shouldAutoScreen) {
          deps.enterScreenStep();
          await deps.startAiScreen({ consumeAutoScreen: true, fields: autoScreenFields.value, profile: autoScreenProfile.value });
        }
      } else {
        screenBusy.value = false;
        pausingScreen.value = false;
        // 实时路径与刷新路径统一：任务完成后拉双平台合并结果（R2），
        // 避免只 set 单平台结果导致结果页切平台显示 0。
        const fetched = await deps.fetchMergedLatestResult();
        if (fetched) {
          deps.setPipelineResult(fetched.merged);
          currentRoundStatus.value = fetched.newer.data.status === "scraped_only" ? "scraped_only" : "screened";
          if (isScrapedOnly.value) activeCategory.value = "matched";
        } else {
          // B044：合并拉取失败不得让 04 空白——完成响应自带的内存结果
          //（后端与 status=done 同锁写入，完成瞬间必然携带）立即兜底，
          // 保证流程完成后 04 页必然有岗位展示；随后后台有界补拉合并
          // 视图（R2 语义），成功再升级为双平台展示。
          const inline = data.result;
          if (inline && Array.isArray(inline.jobs)) {
            deps.setPipelineResult(inline);
            currentRoundStatus.value = "screened";
          }
          retryMergeUpgrade(taskId, 0);
        }
        activeStep.value = "results";
        deps.notify(
          data.status === "completed_with_pending"
            ? "AI 筛选完成，但有岗位待确认"
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
      // 不弹 error 通知：deps.cancelScrape 已经弹过了；这里是轮询兜底（如刷新后接回的取消态）
      return;
    }
    if (data.status === "paused") {
      pollRetryCount.value = 0;
      if (kind === "scrape") scrapeBusy.value = false;
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
      if (kind === "scrape") scrapeBusy.value = false;
      else screenBusy.value = false;
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
      interruptedRunId.value = taskId;
      if (kind === "scrape") {
        scrapeBusy.value = false;
        scrapeTaskId.value = taskId;
        analysisReady.value = true;
        activeStep.value = "search";
        restoredTaskHint.value = "上次抓取因服务重启被中断；已抓数据已保存，可结束保存结果或重新开始抓取";
      } else {
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
    pollRetryCount.value += 1;
    if (pollRetryCount.value > POLL_MAX_RETRIES) {
      // 达上限，主动放弃
      pollRetryCount.value = 0;
      if (kind === "scrape") scrapeBusy.value = false;
      else screenBusy.value = false;
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

// B044：AI 筛选完成瞬间合并拉取失败的补拉升级。任务已完成（04 已用
// 内嵌内存结果展示），后台再按指数退避补拉双平台合并视图；成功即应用
// 升级为 R2 双平台展示；期间开始新任务（task id 变化 / 任意 pipeline
// 占用）立即放弃，绝不串轮。复用 pollTimer 让既有清理路径（resetWorkflow
// / unmount / cancel / finish）统一收口。
function retryMergeUpgrade(taskId: string, attempt: number) {
  if (attempt > POLL_MAX_RETRIES) return;
  if (pollTimer.value) window.clearTimeout(pollTimer.value);
  // 新一轮已开始（task id 变化或任意 pipeline 占用）→ 放弃补拉。
  if (screenTaskId.value !== taskId || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return;
  const delay = Math.min(POLL_BASE_DELAY * 2 ** attempt, POLL_MAX_DELAY);
  pollTimer.value = window.setTimeout(() => {
    pollTimer.value = undefined;
    void deps.fetchMergedLatestResult().then((fetched: MergedLatestResult | null) => {
      if (!fetched) {
        retryMergeUpgrade(taskId, attempt + 1);
        return;
      }
      // 应用前复查：期间开始新任务则丢弃这次补拉结果。
      if (screenTaskId.value !== taskId || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return;
      deps.setPipelineResult(fetched.merged);
      currentRoundStatus.value = fetched.newer.data.status === "scraped_only" ? "scraped_only" : "screened";
      if (isScrapedOnly.value) activeCategory.value = "matched";
    });
  }, delay);
}

// B038：把抓取结果固化为"已抓取，未筛选"轮，供自动保存与手动查看共用。
async function saveScrapedOnlySnapshot(markViewed = false): Promise<"saved" | "zero" | "failed"> {
  if (!scrapeTaskId.value) return "failed";
  const emptyResult = (): PipelineResult => ({
    ok: true, jobs: [], dropped: [],
    total_scraped: 0, total_kept: 0, total_matched: 0, total_dropped: 0,
    profile_summary: profileSummary.value, error: "",
  });
  const snap = scrapeSnapshot.value;
  const scrapedCount = Number(
    snap?.scraped_count ?? snap?.source_total ?? snap?.result?.total_scraped ?? -1,
  );
  if (scrapedCount === 0) {
    deps.setPipelineResult(emptyResult());
    if (!markViewed) resultLoaded.value = false;
    if (markViewed) currentRoundStatus.value = "scraped_only";
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
    if (data.saved && data.result) deps.setPipelineResult(data.result);
    else deps.setPipelineResult(emptyResult());
    if (!markViewed) resultLoaded.value = false;
    if (markViewed) currentRoundStatus.value = "scraped_only";
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


async function cancelActiveTasksForNewRound(): Promise<boolean> {
  const ids = new Set<string>();
  for (const id of [
    scrapeTaskId.value, screenTaskId.value, recrawlTaskId.value,
    pausedRunId.value, interruptedRunId.value,
  ]) {
    if (id) ids.add(id);
  }
  if (!ids.size) {
    try {
      const latest = await apiRequest<{ has_task?: boolean; task_id?: string }>("/api/latest-running-task");
      if (latest.has_task && latest.task_id) ids.add(latest.task_id);
    } catch { /* 接回失败不阻断归档 */ }
  }
  let cancelled = false;
  for (const id of ids) {
    try {
      await apiRequest(`/api/task/cancel/${encodeURIComponent(id)}`, { method: "POST" });
      cancelled = true;
    } catch (error) {
      const payload = (error as ApiError).payload as { error?: string } | undefined;
      if (payload?.error && [
        "already_finished", "run_not_found", "task_not_active", "not_paused",
      ].includes(payload.error)) {
        continue;
      }
      deps.notify(errorMessage(error, "结束旧任务失败，已停止开始新一轮"), "error");
      return false;
    }
  }
  if (cancelled) deps.notify("已结束旧任务，开始新一轮", "info");
  return true;
}


async function finishScreenSave() {
  if (deps.roundFlow.busyAction) return;
  const runId = screenTaskId.value || pausedRunId.value;
  if (!runId) return;
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
      result?: { updates?: Record<string, unknown> } | null;
    }>(`/api/task-state/${encodeURIComponent(runId)}`);
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
  if (historyMode.value) {
    deps.notify("历史轮次不可改写，请先回到最新", "warning");
    return;
  }
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
  // 单平台直接重抓：进度回 03 页展示（“全部”视图单平台场景由 startRecrawl
  // 以 undefined 调用，这里补切；单平台视图已在 startRecrawl 显式切过）。
  activeStep.value = "screen";
  recrawlBusy.value = true;
  recrawlPlatformGuide.value = null;
  recrawlSnapshot.value = {
    status: "running",
    progress: { message: `准备重抓 ${ids.length} 个待确认岗位…` },
    logs: [],
    error: "",
  };
  interruptedRunId.value = "";
  try {
    const data = await apiRequest<{ task_id: string }>("/api/pipeline/recrawl", {
      method: "POST",
      json: {
        // 单平台视图按岗位自身来源 run 重抓，不跨平台混合。
        source_run_id: resultRunIds.value[filter] || pipelineResultRunId.value,
        job_ids: ids,
        profile_summary: profileSummary.value,
        profile_facts: profileFacts.value,
      },
    });
    recrawlTaskId.value = data.task_id;
    await pollRecrawl(data.task_id);
  } catch (error) {
    recrawlBusy.value = false;
    recrawlSnapshot.value = {
      status: "failed", progress: {}, logs: [], error: errorMessage(error, "重抓启动失败"),
    };
    deps.notify(errorMessage(error, "重抓启动失败"), "error");
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
    pausedRunId.value = "";
    recrawlTaskId.value = data.task_id || taskId;
    recrawlRetryCount.value = 0;
    await pollRecrawl(recrawlTaskId.value);
  } catch (error) {
    recrawlBusy.value = false;
    // 继续重抓失败：回到报错暂停时的样子（与 deps.continueAiScreen 同一恢复路径）。
    const restored = await apiRequest<TaskSnapshot>(
      `/api/task-state/${encodeURIComponent(taskId)}`,
    ).catch(() => null);
    recrawlSnapshot.value = restored
      ? { ...restored, status: "paused" }
      : { ...(recrawlSnapshot.value || {}), status: "paused", error: errorMessage(error, "重抓断点继续失败") };
  }
}


async function pollRecrawl(taskId: string) {
  try {
    const data = await apiRequest<TaskSnapshot>(`/api/task-state/${encodeURIComponent(taskId)}`);
    recrawlSnapshot.value = data;
    const liveUpdates = (data.result as unknown as { updates?: Record<string, unknown> } | undefined)?.updates;
    if (liveUpdates) mergeRecrawlUpdates(liveUpdates as Record<string, unknown>);
    if (isCompletedTaskStatus(data.status)) {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      const updates = (data.result as unknown as { updates?: Record<string, unknown> } | undefined)?.updates;
      if (updates) mergeRecrawlUpdates(updates as Record<string, unknown>);
      deps.notify(
        data.status === "completed_with_pending" || data.status === "partial"
          ? String(data.progress?.message || "重抓完成，但仍有岗位待确认")
          : "待确认岗位已重抓完成",
        data.status === "completed_with_pending" || data.status === "partial"
          ? "warning"
          : "success",
      );
      activeStep.value = "results";
      window.setTimeout(() => { recrawlSnapshot.value = null; }, 3000);
      return;
    }
    if (data.status === "cancelled") {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      deps.notify("已停止重抓", "warning");
      window.setTimeout(() => { recrawlSnapshot.value = null; }, 3000);
      return;
    }
    if (data.status === "paused") {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      deps.notify(data.error || "重抓已暂停，请处理后点继续", "warning");
      return;
    }
    if (data.status === "failed") {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      deps.notify(data.error || "重抓失败", "error");
      window.setTimeout(() => { recrawlSnapshot.value = null; }, 5000);
      return;
    }
    if (data.status === "interrupted") {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
      interruptedRunId.value = taskId;
      recrawlTaskId.value = taskId;
      restoredTaskHint.value = "上次补抓因服务重启被中断；可结束保存已有结果";
      data.progress = { ...(data.progress || {}), message: "任务因服务重启被中断，已保存进度" };
      recrawlSnapshot.value = data;
      return;
    }
    pollTimer.value = window.setTimeout(() => void pollRecrawl(taskId), 1800);
  } catch (error) {
    recrawlRetryCount.value += 1;
    if (recrawlRetryCount.value > POLL_MAX_RETRIES) {
      recrawlRetryCount.value = 0;
      recrawlBusy.value = false;
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
    pollTimer.value = window.setTimeout(() => void pollRecrawl(taskId), delay);
  }
}

// 把后端回写的 {jd/verdict/verdict_reason/caveats} 原地合并到当前结果，
// 已解决的项会随 groups 重算自动离开待确认 tab，未解决项原地保留。

// 把后端回写的 {jd/verdict/verdict_reason/caveats} 原地合并到当前结果，
// 已解决的项会随 groups 重算自动离开待确认 tab，未解决项原地保留。
function mergeRecrawlUpdates(updates: Record<string, unknown>) {
  const result = pipelineResult.value as (PipelineResult & { jobs?: JobItem[] }) | null;
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
  // 026 B078：已进 04 页（或结束保存）＝上次流程已结束 → 直接开始新一轮，
  // 不再依赖后端历史轮状态/时间戳推断（spec FR-001/FR-005）。
  if (resultsPageSeen.value || finishedPartial.value) {
    await resetWorkflow();
    return;
  }
  // 有恢复的活动任务：仅已完成终态属于完成态 → 自动新一轮；否则恢复现场
  if (activeTaskRestored.value) {
    const taskStatus = String(
      screenSnapshot.value?.status || scrapeSnapshot.value?.status || "",
    );
    const completedTask = ["completed", "completed_with_pending", "partial", "succeeded"]
      .includes(taskStatus);
    if (!completedTask) return;
  }
  // 无进行中任务（或已完成终态）：查最新历史轮（只查不设，不糊脸）
  try {
    const fetched = await deps.fetchMergedLatestResult();
    if (!fetched) return;  // 无历史轮（全新用户）→ 保持干净 01 页
    const completedStatuses = [
      "completed", "completed_with_pending", "partial", "succeeded",
    ];
    // 025 B078：任一平台最新轮为未完成态（暂停/中断/已抓未筛选/未知）→
    // 属"有未完成流程"→ 恢复现场（B068 不变）；全部完成态才自动新一轮。
    const platformStatuses = Object.values(fetched.platformStatuses ?? {})
      .map((s) => String(s ?? ""));
    const anyUnfinished = platformStatuses.some(
      (s) => !s || !completedStatuses.includes(s),
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


async function resetWorkflow() {
  if (!(await cancelActiveTasksForNewRound())) return;
  if (!(await deps.clearLatestResult())) return;
  deps.clearWorkflowState();
  // 026 B078：开始新一轮即清除持久化的已结束事实。
  deps.clearFinishedState?.();
  resultsPageSeen.value = false;
  if (pollTimer.value) window.clearTimeout(pollTimer.value);
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
  activeCategory.value = "matched";
  rejectedIds.value = new Set();
  pausedRunId.value = "";
  interruptedRunId.value = "";
  restoredTaskHint.value = "";
  currentRoundStatus.value = "";
  scopePreview.value = null;
  scopePreviewBusy.value = false;
  keywords.value = [];
  autoScreenArmed.value = false;
  oneClickOpen.value = false;
  profileError.value = "";
  selectedKeywords.value = [];
  customKeyword.value = "";
  cityText.value = "";
  customCity.value = "";
  locationDraft.reset();
  // T506：重置两个平台的筛选草稿
  filterValues.value = { boss: {}, zhilian: {} };
  resumeAnalysis.value = null;
  appliedResumePlatforms.value = new Set();
  profileSummary.value = "";
  profileFacts.value = {};
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
}

return {
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