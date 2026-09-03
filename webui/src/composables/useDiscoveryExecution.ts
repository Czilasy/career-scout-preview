// 021 B8 T027：DiscoveryView execution 动作层（自 DiscoveryView.vue script 原样搬运，函数体零改动，跨域引用经 deps 调用时解析）。
// 031 B8：deps 形参类型 = discoveryDeps.ts 的 ExecutionNeeds（跨域依赖契约）。
import type { Ref } from "vue";
import type { DiscoveryState } from "./useDiscoveryState";
import type { ExecutionNeeds } from "./discoveryDeps";
import { nextTick } from "vue";
import { ApiError, apiRequest, errorMessage, settingsApi, userFacingMessage } from "../api";
import type { PipelineResult, RoundStatusPayload } from "../discovery";
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
import TaskProgress from "../components/TaskProgress.vue";
import {
  buildSearchScriptParams,
  createCityCatalogLoader,
  createPlatformState,
  createSchemaLoader,
  DEFAULT_PLATFORM,
  filterPipelineResultByPlatform,
  normalizeScopePreview,
  partitionPipelineResult,
  projectResumeSuggestionToSchema,
  shouldConfirmNationalScope,
} from "../discovery";
import OneClickScreenDialog, {
  type OneClickFilterGroup,
  crossPlatformDedupeEnabled,
} from "../components/OneClickScreenDialog.vue";
import { setThemePlatform } from "../composables/useTheme";
import type { AiScreenLaunch } from "./useDiscoveryState";
import type { OneClickLaunch } from "./useDiscoveryState";
import type { TaskSnapshot } from "./useDiscoveryState";

export function useDiscoveryExecution(state: DiscoveryState, deps: ExecutionNeeds) {
  const { activeCategory, activeStep, activeTaskRestored, advancedPanelsOpen, analysisReady, autoScreenArmed, autoScreenFields, autoScreenProfile, cancelBusy, cityList, currentRoundStatus, draftPlatform, effectiveSearchCities, filterValues, finishSaveBusy, finishedPartial, historyDetail, historyMode, historyRound, historyScreenBusy, interruptedRunId, locationDraft, nationalScopeConfirm, oneClickOpen, pausedRunId, pipelineBusy, pipelineResult, pipelineResultRunId, platformBeforeHistory, platformState, pollRetryCount, pollTimer, profileConfirmed, profileError, profileFacts, profileSummary, recrawlBusy, recrawlPlatformGuide, recrawlSnapshot, recrawlTaskId, restoredTaskHint, resultEpoch, resultLoaded, resultPlatformFilter, resultRunIds, resultsPageSeen, schemaRef, scrapeBusy, scrapeCompleted, scrapeSnapshot, scrapeTaskId, screenBusy, screenPanelOpen, screenSnapshot, screenTaskId, searchPanelsOpen, selectedKeywords, switchAccountId, switchAccounts } = state;
  const { clearWorkflowState, enrichPausedSnapshot, enterScreenStep, enterSearchStep, isCompletedTaskStatus, isLoginErrorCode, loadCityCatalog, loadFilterLabels, loadLatestResult, notify, pollRecrawl, pollTask, refreshScopePreview, requireProfileConfirmed, restoreLocationsFromContext, returnToLatest, saveScrapedOnlySnapshot, setDraftPlatform, setPipelineResult, showLoginGuide, validateProfileForScreen } = deps;


async function restoreRunningTask() {
  try {
    const data = await apiRequest<{
      has_task?: boolean;
      task_id?: string;
      kind?: string;
      status?: string;
      // T509：任务自身平台（http-api.md L201，所有 has_task=true 响应含 platform）
      platform?: Platform;
      progress?: Record<string, unknown>;
      logs?: string[];
      error?: string;
      stage?: string;
      pause_info?: { error_code?: string; error_reason?: string } | null;
      execution_config?: Record<string, unknown> | null;
      backend_version?: string;
      current_version?: string;
      version_match?: boolean;
      scrape_task_id?: string;
      scrape_completed?: boolean;
      source_run_id?: string;
      started_at?: number;
      finished_at?: number;
      scraped_count?: number;
      source_total?: number;
      frozen_filters?: Record<string, unknown>;
      profile_summary?: string;
      profile_facts?: Record<string, unknown>;
      auto_screen?: boolean;
      auto_screen_fields?: Record<string, unknown>;
      round_context?: Partial<RoundContext> | null;
    }>("/api/latest-running-task");
    if (!data.has_task || !data.task_id) return;
    // 026 B078：已进 04 页（或结束保存）＝上次流程已结束。后端残留的任何
    // 任务（interrupted/paused/failed/completed scrape）都不得触发恢复或
    // "服务重启被中断"提示，保持 01 页；统一由 maybeAutoStartNewRound 重置
    //（spec FR-002/FR-003/FR-004；判据为持久化的已结束事实）。
    if (resultsPageSeen.value || finishedPartial.value) return;
    activeTaskRestored.value = true;
    // T509：先设置任务自身平台，再加载对应 schema/城市（platform-schema.md L157）。
    // 不改草稿平台（不变式 2：setTaskPlatform 不改 draft/result）。
    const taskPlatform = data.platform;
    if (taskPlatform) {
      platformState.setTaskPlatform(taskPlatform);
      // 任务平台变化时同步主题品牌色（如恢复一个智联任务时切到蓝色品牌色）。
      setThemePlatform(taskPlatform);
      void deps.loadFilterLabels(taskPlatform);
      void deps.loadCityCatalog(taskPlatform);
    }
    // frozen_filters 写入任务平台对应的草稿槽；缺平台时退化到草稿平台（兼容旧 mock）
    const filterPlatform: Platform = taskPlatform ?? draftPlatform.value;
    if (data.kind === "recrawl") {
      // 重抓任务恢复：先加载结果供 04 查看，但跳过"上次已完成"快照
      //（03 页应显示重抓自身进度，不伪造完成态）。
      await deps.loadLatestResult({ skipTerminalSnapshot: true });
    }
    const snapshot: TaskSnapshot = {
      status: data.status || "running",
      progress: data.progress || {},
      logs: data.logs || [],
      error: data.error || "",
      stage: data.stage,
      pause_info: data.pause_info,
      started_at: data.started_at,
      finished_at: data.finished_at,
      // T510：快照携带任务平台，供 TaskProgress 展示真实平台徽章
      platform: taskPlatform,
    };
    let kind: "scrape" | "screen" | "recrawl" = "screen";
    if (data.kind === "scrape") kind = "scrape";
    else if (data.kind === "recrawl") kind = "recrawl";
    if (kind === "scrape" && deps.isCompletedTaskStatus(data.status) && data.auto_screen) {
      scrapeTaskId.value = data.scrape_task_id || data.task_id;
      scrapeCompleted.value = true;
      analysisReady.value = true;
      const savedFilters = data.auto_screen_fields || data.frozen_filters || {};
      const drafts = filterValues.value[filterPlatform];
      for (const key of Object.keys(drafts)) delete drafts[key];
      Object.assign(
        drafts,
        Object.fromEntries(
          Object.entries(savedFilters)
            .filter((entry): entry is [string, string[]] => Array.isArray(entry[1]))
            .map(([key, value]) => [key, value as string[]]),
        ),
      );
      profileSummary.value = data.profile_summary || "";
      profileFacts.value = data.profile_facts && typeof data.profile_facts === "object"
        ? (data.profile_facts as Record<string, unknown>) : {};
      deps.enterScreenStep();
      restoredTaskHint.value = "检测到一键任务已抓取完成，正在自动接续 AI 筛选";
      if (data.round_context) deps.restoreLocationsFromContext(data.round_context);
      void startAiScreen({ consumeAutoScreen: true, fields: drafts, profile: profileSummary.value });
      return;
    }
    if (kind === "scrape" && deps.isCompletedTaskStatus(data.status) && !data.auto_screen) {
      scrapeTaskId.value = data.task_id;
      scrapeCompleted.value = true;
      analysisReady.value = true;
      activeStep.value = "search";
      profileSummary.value = data.profile_summary || "";
      profileFacts.value = data.profile_facts && typeof data.profile_facts === "object"
        ? (data.profile_facts as Record<string, unknown>) : {};
      scrapeSnapshot.value = {
        ...snapshot,
        scraped_count: data.scraped_count,
        source_total: data.source_total,
      };
      if (data.round_context) deps.roundFlow.restoreRoundContext(data.round_context);
      if (data.round_context) deps.restoreLocationsFromContext(data.round_context);
      restoredTaskHint.value = "检测到已完成的抓取任务，正在恢复结果";
      await deps.loadLatestResult();
      await deps.saveScrapedOnlySnapshot();
      return;
    }
    // 035（真机问题①，FR-010）：恢复到活的抓取任务（运行/排队/暂停/失败/中断）时，
    // 本轮进度必在 02——screen 侧任何展示数据必属旧轮，同步清空（含 sessionStorage
    // 整包恢复带入的 screenSnapshot 残留），03 页回「未开始」。
    if (kind === "scrape" && ["running", "queued", "paused", "failed", "interrupted"].includes(String(data.status))) {
      screenTaskId.value = "";
      screenSnapshot.value = null;
      recrawlTaskId.value = "";
      recrawlSnapshot.value = null;
      currentRoundStatus.value = "";
    }
    if (data.status === "interrupted") {
      // 服务重启打断的任务：工作线程已死不能 poll；提示用户重开（后端会自动接着上次进度）
      interruptedRunId.value = data.task_id;
      if (data.kind === "scrape") {
        scrapeTaskId.value = data.task_id;
        analysisReady.value = true;
        activeStep.value = "search";
        restoredTaskHint.value = "上次抓取因服务重启被中断；已抓数据已保存，可结束保存结果或重新开始抓取";
        scrapeSnapshot.value = {
          ...snapshot,
          scraped_count: data.scraped_count,
          source_total: data.source_total,
        };
        if (data.round_context) deps.restoreLocationsFromContext(data.round_context);
        return;
      }
      if (data.kind === "recrawl") {
        recrawlTaskId.value = data.task_id;
        resultLoaded.value = true;
        activeCategory.value = "uncertain";
        activeStep.value = "results";
        restoredTaskHint.value = "上次补抓因服务重启被中断；可结束保存已有结果";
        return;
      }
      restoredTaskHint.value = "上次 AI 筛选因服务重启被中断；重新开始 AI 筛选会接着上次进度，不重复消耗";
      scrapeTaskId.value = data.scrape_task_id || "";
      scrapeCompleted.value = Boolean(data.scrape_completed);
      screenTaskId.value = data.task_id;
      analysisReady.value = true;
      deps.enterScreenStep();
      const savedFilters = data.frozen_filters || {};
      // T509：写入任务平台对应的草稿槽（platform-schema.md L157），
      // 不再用草稿平台槽 — 否则 zhilian 任务恢复后 filters 落到 boss 槽会被 boss schema 拒绝。
      const drafts = filterValues.value[filterPlatform];
      for (const key of Object.keys(drafts)) delete drafts[key];
      Object.assign(
        drafts,
        Object.fromEntries(
          Object.entries(savedFilters)
            .filter((entry): entry is [string, string[]] => Array.isArray(entry[1]))
            .map(([key, value]) => [key, value as string[]]),
        ),
      );
      profileSummary.value = data.profile_summary || "";
      profileFacts.value = data.profile_facts && typeof data.profile_facts === "object"
        ? (data.profile_facts as Record<string, unknown>) : {};
      if (data.round_context) deps.roundFlow.restoreRoundContext(data.round_context);
      deps.restoreLocationsFromContext(data.round_context);
      return;
    }
    // 切片7：paused 状态从 DB 恢复（无内存工作线程，不能 poll）
    if (data.status === "failed" && kind === "scrape") {
      scrapeTaskId.value = data.task_id;
      analysisReady.value = true;
      activeStep.value = "search";
      scrapeSnapshot.value = {
        ...snapshot,
        scraped_count: data.scraped_count,
        source_total: data.source_total,
      };
      restoredTaskHint.value = "检测到失败的抓取任务；已抓数据已保存，可结束保存结果或重新开始抓取";
      if (data.round_context) deps.restoreLocationsFromContext(data.round_context);
      return;
    }
    if (data.status === "paused") {
      pausedRunId.value = data.task_id;
      analysisReady.value = true;
      if (kind === "scrape") {
        activeStep.value = "search";
        autoScreenArmed.value = Boolean(data.auto_screen);
        if (data.auto_screen_fields) {
          const autoDrafts = filterValues.value[filterPlatform];
          for (const key of Object.keys(autoDrafts)) delete autoDrafts[key];
          Object.assign(
            autoDrafts,
            Object.fromEntries(
              Object.entries(data.auto_screen_fields)
                .filter((entry): entry is [string, string[]] => Array.isArray(entry[1]))
                .map(([key, value]) => [key, value as string[]]),
            ),
          );
          autoScreenFields.value = Object.fromEntries(
            Object.entries(data.auto_screen_fields)
              .filter((entry): entry is [string, string[]] => Array.isArray(entry[1]))
              .map(([key, value]) => [key, value as string[]]),
          );
        }
        profileSummary.value = data.profile_summary || "";
      profileFacts.value = data.profile_facts && typeof data.profile_facts === "object"
        ? (data.profile_facts as Record<string, unknown>) : {};
        autoScreenProfile.value = data.profile_summary || "";
        if (data.round_context) deps.restoreLocationsFromContext(data.round_context);
      } else if (kind === "screen") {
        scrapeTaskId.value = data.scrape_task_id || "";
        scrapeCompleted.value = Boolean(data.scrape_completed);
        screenTaskId.value = data.task_id;
        deps.enterScreenStep();
        // T509：paused screen 任务也投影冻结筛选快照（platform-schema.md L157）
        const savedFilters = data.frozen_filters || {};
        const drafts = filterValues.value[filterPlatform];
        for (const key of Object.keys(drafts)) delete drafts[key];
        Object.assign(
          drafts,
          Object.fromEntries(
            Object.entries(savedFilters)
              .filter((entry): entry is [string, string[]] => Array.isArray(entry[1]))
              .map(([key, value]) => [key, value as string[]]),
          ),
        );
        profileSummary.value = data.profile_summary || "";
      profileFacts.value = data.profile_facts && typeof data.profile_facts === "object"
        ? (data.profile_facts as Record<string, unknown>) : {};
        if (data.round_context) deps.roundFlow.restoreRoundContext(data.round_context);
        deps.restoreLocationsFromContext(data.round_context);
      } else {
        // 重抓暂停也是任务未结束：进度回 03 页，04 保持结果展示可切。
        recrawlTaskId.value = data.task_id;
        resultLoaded.value = true;
        activeCategory.value = "uncertain";
        activeStep.value = "screen";
      }
      // 拉 /api/task-state 拿完整计数画面（success/fail/unstarted/total）
      await deps.enrichPausedSnapshot(data.task_id, snapshot, kind);
      const reason = data.pause_info?.error_reason || "任务已暂停";
      restoredTaskHint.value = `检测到暂停中的任务（${reason}），处理后点继续`;
      return;
    }
    if (kind === "scrape") {
      scrapeTaskId.value = data.task_id;
      analysisReady.value = true;
      scrapeBusy.value = true;
      scrapeSnapshot.value = snapshot;
      restoredTaskHint.value = "检测到抓取任务仍在后台运行，已自动接回";
      activeStep.value = "search";
      autoScreenArmed.value = Boolean(data.auto_screen);
      if (data.auto_screen_fields) {
        autoScreenFields.value = Object.fromEntries(
          Object.entries(data.auto_screen_fields)
            .filter((entry): entry is [string, string[]] => Array.isArray(entry[1]))
            .map(([key, value]) => [key, value as string[]]),
        );
      }
      const restoredProfile = data.profile_summary || "";
      profileSummary.value = restoredProfile;
      profileFacts.value = data.profile_facts && typeof data.profile_facts === "object"
        ? (data.profile_facts as Record<string, unknown>) : {};
      autoScreenProfile.value = restoredProfile;
      if (data.round_context) deps.restoreLocationsFromContext(data.round_context);
      void deps.pollTask(data.task_id, "scrape");
    } else if (kind === "screen") {
      screenTaskId.value = data.task_id;
      scrapeTaskId.value = data.scrape_task_id || "";
      scrapeCompleted.value = true;
      // 025 B078：已完成终态任务不当作运行中恢复（不设 busy、不 poll）——
      // 由 maybeAutoStartNewRound 判定为完成态自动「开始新一轮」。
      const terminalDone = ["completed", "completed_with_pending", "partial", "succeeded"]
        .includes(String(data.status));
      if (terminalDone) {
        screenSnapshot.value = snapshot;
        analysisReady.value = true;
        return;
      }
      screenBusy.value = true;
      screenSnapshot.value = snapshot;
      restoredTaskHint.value = "检测到 AI 筛选任务仍在后台运行，已自动接回";
      analysisReady.value = true;
      deps.enterScreenStep();
      void deps.pollTask(data.task_id, "screen");
      if (data.round_context) deps.restoreLocationsFromContext(data.round_context);
    } else {
      recrawlTaskId.value = data.task_id;
      recrawlBusy.value = true;
      recrawlSnapshot.value = snapshot;
      resultLoaded.value = true;
      activeCategory.value = "uncertain";
      // 重抓运行中进度在 03 页展示；04 保持结果展示，用户可切回看旧结果。
      activeStep.value = "screen";
      restoredTaskHint.value = "检测到重抓任务仍在后台运行，已自动接回";
      void deps.pollRecrawl(data.task_id);
    }
  } catch { /* non-critical: 接不回就当没有 */ }
}


async function startScrape(options: OneClickLaunch = {}) {
  if (historyMode.value) {
    deps.notify("历史轮次不可改写，请先回到最新", "warning");
    return;
  }
  if (pipelineBusy.value) {
    deps.notify("当前已有任务在运行或暂停，请先处理完再开始新任务", "warning");
    return;
  }
  const scriptParams = buildSearchScriptParams(
    selectedKeywords.value,
    effectiveSearchCities.value,
    locationDraft.allLocations(draftPlatform.value, cityList.value),
  );
  // 城市为空时，入口已先弹出全国范围确认；确认后用 effectiveSearchCities
  // 生成的“全国”继续，不能在这里再次按空城市拦截。
  if (!scriptParams.keyword) {
    deps.notify("请确认至少一个关键词", "warning");
    return;
  }
  // Spec 038 B091 FR-019：开抓前校验至少 1 账号选中参与轮询
  // 全取消所有账号勾选时阻止开抓（默认零配置：账号默认全选，本校验只在
  // 用户主动取消所有账号后拦截；账号簿读取失败不阻断开抓，后端会兜底）。
  try {
    const accountsData = await apiRequest<{
      accounts: Array<{ pool?: { selected: boolean } }>;
    }>("/api/browser-accounts");
    const anySelected = accountsData.accounts.some((a) => a.pool?.selected ?? true);
    if (!anySelected) {
      deps.notify("请至少勾选一个账号参与轮询后再开抓", "warning");
      return;
    }
  } catch {
    // 读取异常时继续提交，由任务创建端的 FR-019 硬门禁裁决；避免把短暂
    // 的账号簿读取异常误报成用户不可开抓。
  }
  const preview = await deps.refreshScopePreview();
  if (!preview) return;
  // 开始抓取后自动收拢两个配置面板（用户可随时手动展开查看）。
  autoScreenArmed.value = Boolean(options.autoScreen);
  autoScreenFields.value = options.fields || {};
  autoScreenProfile.value = options.profile || "";
  profileError.value = "";
  searchPanelsOpen.value = false;
  advancedPanelsOpen.value = false;
  scrapeBusy.value = true;
  scrapeCompleted.value = false;
  resultLoaded.value = false;
  pipelineResult.value = null;
  interruptedRunId.value = "";
  scrapeSnapshot.value = { status: "running", progress: { message: "正在创建抓取任务…" }, logs: [] };
  finishedPartial.value = false;
  recrawlPlatformGuide.value = null;
  // 035（真机问题①，FR-010 场景 5）：新一轮开始即清空上一轮 AI 筛选/重抓展示状态，
  // 03 页不保留旧一轮内容。
  screenTaskId.value = "";
  screenSnapshot.value = null;
  recrawlTaskId.value = "";
  recrawlSnapshot.value = null;
  currentRoundStatus.value = "";
  try {
    const data = await apiRequest<{ task_id: string }>("/api/execute-search", {
      method: "POST",
      json: {
        platform: draftPlatform.value,
        script_params: scriptParams,
        scope_digest: preview.scope_digest,
        profile_summary: profileSummary.value,
        profile_facts: profileFacts.value,
        ...(options.autoScreen ? {
          auto_screen: true,
          auto_screen_fields: options.fields || {},
          auto_screen_profile: options.profile || "",
          // B033：一键自动筛选同样冻结画像事实快照，刷新后接续不丢三通道输入
          auto_screen_facts: profileFacts.value,
        } : {}),
      },
    });
    scrapeTaskId.value = data.task_id;
    await deps.pollTask(data.task_id, "scrape");
  } catch (error) {
    scrapeBusy.value = false;
    scrapeSnapshot.value = { status: "failed", error: errorMessage(error, "抓取启动失败") };
    deps.notify(errorMessage(error, "抓取启动失败"), "error");
    // D7：未登录被拒时给出账号级登录引导并跳转账号面板。
    if (error instanceof ApiError && deps.isLoginErrorCode(error.payload.error_code)) {
      void deps.showLoginGuide(draftPlatform.value);
    }
  }
}


async function cancelScrape() {
  if (!scrapeTaskId.value) return;
  // 先停轮询，避免取消后还去拿旧状态
  if (pollTimer.value) { window.clearTimeout(pollTimer.value); pollTimer.value = undefined; }
  cancelBusy.value = true;
  try {
    await apiRequest(`/api/task/cancel/${encodeURIComponent(scrapeTaskId.value)}`, {
      method: "POST",
    });
    // 后端会立刻关浏览器并标 cancelled；这里直接复位，不等下一次轮询
    scrapeBusy.value = false;
    autoScreenArmed.value = false;
    restoredTaskHint.value = "";
    scrapeSnapshot.value = { status: "cancelled", progress: { message: "已停止抓取" }, logs: [], error: "" };
    interruptedRunId.value = "";
    deps.notify("已停止抓取", "warning");
  } catch (error) {
    // 取消接口失败时不要卡死：恢复轮询让前端看真实状态
    deps.notify(errorMessage(error, "停止失败，请重试"), "error");
    await deps.pollTask(scrapeTaskId.value, "scrape");
  }
  finally {
    cancelBusy.value = false;
  }
}


async function continueScrape(targetAccount?: string) {
  if (historyMode.value) return;
  if (!scrapeTaskId.value || scrapeBusy.value) return;
  scrapeBusy.value = true;
  scrapeCompleted.value = false;
  pausedRunId.value = ""; // 切片7：清掉 DB paused 标记，进入内存工作模式
  interruptedRunId.value = "";
  restoredTaskHint.value = "";
  // 016：续跑起步沿用上一快照的断点进度，禁止归零后再跳到真实位置
  const resumeProgress = { ...(scrapeSnapshot.value?.progress || {}) };
  resumeProgress.message = "正在从断点继续…";
  scrapeSnapshot.value = {
    status: "running",
    progress: resumeProgress,
    logs: scrapeSnapshot.value?.logs || [],
  };
  try {
    const data = await apiRequest<{ task_id: string; skipped: number; old_jobs: number }>(
      `/api/task/continue/${encodeURIComponent(scrapeTaskId.value)}`,
      { method: "POST", json: targetAccount ? { target_account: targetAccount } : undefined },
    );
    scrapeTaskId.value = data.task_id;
    pollRetryCount.value = 0;
    switchAccountId.value = "";
    await deps.pollTask(data.task_id, "scrape");
  } catch (error) {
    scrapeBusy.value = false;
    scrapeSnapshot.value = { status: "failed", progress: {}, logs: [], error: errorMessage(error, "断点续抓启动失败") };
  }
}


async function loadSwitchAccounts() {
  try {
    const data = await apiRequest<{ accounts?: Array<{ id: string; name: string }> }>("/api/browser-accounts");
    const list = Array.isArray(data.accounts)
      ? data.accounts.map((a) => ({ id: String(a.id), name: a.id === "a" ? "默认账号" : (a.name || a.id) }))
      : [];
    switchAccounts.value = list.filter((a) => a.id);
  } catch {
    switchAccounts.value = [];
  }
}


async function flowStartAiScreen(opts?: AiScreenLaunch) {
  if (!deps.validateProfileForScreen()) {
    deps.notify("求职画像至少 10 个字（不含首尾空格）", "warning");
    return;
  }
  if (!deps.requireProfileConfirmed()) return;
  await startAiScreen(opts);
}


async function startAiScreen(options: AiScreenLaunch = {}) {
  if (historyMode.value) {
    deps.notify("历史轮次不可改写，请先回到最新", "warning");
    return;
  }


  // 抓取/重抓占用时不允许再开一轮 AI 筛选；中断/暂停的 AI 续跑仍可进入。
  if (scrapeBusy.value || recrawlBusy.value || scrapeSnapshot.value?.status === "paused") {
    deps.notify("当前已有任务在运行或暂停，请先处理完再开始新任务", "warning");
    return;
  }
  if (!scrapeCompleted.value || !scrapeTaskId.value) {
    if (!scrapeCompleted.value) {
      deps.notify("请先完成本轮抓取，再开始 AI 筛选", "warning");
    } else {
      // 旧快照缺父任务来源时不伪造 ID，明确提示重新抓取（B027 契约）。
      deps.notify("旧结果缺少抓取任务来源，无法继续 AI 筛选；请重新开始抓取", "warning");
    }
    return;
  }
  if (!deps.validateProfileForScreen()) {
    deps.notify("求职画像至少 10 个字（不含首尾空格）", "warning");
    return;
  }
  const consumeAutoScreen = Boolean(options.consumeAutoScreen || autoScreenArmed.value);
  autoScreenArmed.value = false;
  const screenFields = options.fields || filterValues.value[draftPlatform.value];
  const screenProfile = options.profile ?? profileSummary.value;
  screenPanelOpen.value = false;
  screenBusy.value = true;
  pausedRunId.value = ""; // 切片7：清掉 DB paused 标记，进入内存工作模式
  interruptedRunId.value = "";
  finishedPartial.value = false;
  restoredTaskHint.value = "";
  deps.roundFlow.clearRoundContext();
  // 020 US5：发起即离开「已抓取未筛选」次级状态，终态展示不被其遮蔽。
  currentRoundStatus.value = "screened";
  screenSnapshot.value = { status: "running", progress: { message: "正在创建 AI 筛选任务…" }, logs: [] };
  try {
    const data = await apiRequest<{ task_id: string; resuming?: boolean }>("/api/ai-screen", {
      method: "POST",
      json: {
        // T506/T508：只提交当前草稿平台的筛选草稿 + schema 版本。
        // 不发 platform（父 run 已冻结平台，后端从父 run 读）；不发 BOSS 的 stage 给智联 run。
        screening_fields: screenFields,
        filter_schema_version: schemaRef.value?.schema_version ?? null,
        profile_summary: screenProfile,
        profile_facts: profileFacts.value,
        scrape_task_id: scrapeTaskId.value,
        // 019：跨平台去重开关（对话框本地记忆；后端随 run 冻结，续跑沿用）。
        cross_platform_dedupe: crossPlatformDedupeEnabled(),
        ...(consumeAutoScreen ? { consume_auto_screen: true } : {}),
      },
    });
    screenTaskId.value = data.task_id;
    if (data.resuming) {
      screenSnapshot.value = { status: "running", progress: { message: "检测到上次未完成的筛选，接着上次进度继续…" }, logs: [] };
      deps.notify("检测到上次未完成的筛选，已自动续跑", "info");
    }
    await deps.pollTask(data.task_id, "screen");
  } catch (error) {
    screenBusy.value = false;
    screenSnapshot.value = { status: "failed", error: errorMessage(error, "AI 筛选启动失败") };
    deps.notify(errorMessage(error, "AI 筛选启动失败"), "error");
  }
}


async function continueAiScreen(platform?: Platform) {
  if (historyMode.value) return;
  const ctx = platform ? deps.roundFlow.roundContexts[platform] : deps.roundFlow.roundContext;
  const status = String(ctx?.status || screenSnapshot.value?.status || "");
  const isPausedResume = Boolean(pausedRunId.value) || status === "paused";
  activeStep.value = "screen";
  if (!isPausedResume) {
    await startAiScreen({
      fields: ctx?.screening_fields || filterValues.value[draftPlatform.value],
      profile: ctx?.profile_summary || profileSummary.value,
    });
    return;
  }
  const runId = pausedRunId.value || ctx?.screen_run_id || screenTaskId.value;
  if (!runId || screenBusy.value) return;
  screenBusy.value = true;
  finishedPartial.value = false;
  restoredTaskHint.value = "";
  deps.roundFlow.clearRoundContext();
  interruptedRunId.value = "";
  // 016：续跑起步沿用上一快照断点进度，禁止归零再跳
  const screenResumeProgress = { ...(screenSnapshot.value?.progress || {}) };
  screenResumeProgress.message = "正在从 AI 断点继续…";
  screenSnapshot.value = {
    status: "running",
    progress: screenResumeProgress,
    logs: screenSnapshot.value?.logs || [],
  };
  try {
    const data = await apiRequest<{ task_id: string }>(
      `/api/task/continue/${encodeURIComponent(runId)}`,
      { method: "POST" },
    );
    pausedRunId.value = "";
    screenTaskId.value = data.task_id;
    pollRetryCount.value = 0;
    await deps.pollTask(data.task_id, "screen");
  } catch (error) {
    screenBusy.value = false;
    // 继续失败（如 AI 限流未解除 → 409 block_not_resolved）：回到报错暂停时的样子。
    // 与刷新页面同一恢复路径：拉 task-state 全量暂停快照（进度/日志/pause_info/中文原因），
    // 不重建空快照，不直出英文错误码。
    const restored = await apiRequest<TaskSnapshot>(
      `/api/task-state/${encodeURIComponent(runId)}`,
    ).catch(() => null);
    screenSnapshot.value = restored
      ? { ...restored, status: "paused" }
      : { ...(screenSnapshot.value || {}), status: "paused", error: errorMessage(error, "AI 断点继续失败") };
  }
}
// 切片7：统一取消 paused 任务（FR-024）。


async function finishPausedTask(runId: string) {
  if (!runId) return;
  // 先停轮询，避免旧状态在保存完成后覆盖新快照。
  if (pollTimer.value) { window.clearTimeout(pollTimer.value); pollTimer.value = undefined; }
  finishSaveBusy.value = true;
  try {
    const data = await apiRequest<{
      result?: PipelineResult;
      snapshot_run_id?: string;
      scrape_task_id?: string;
      platform?: Platform;
    }>(`/api/task/finish/${encodeURIComponent(runId)}`, { method: "POST" });
    scrapeBusy.value = false;
    screenBusy.value = false;
    recrawlBusy.value = false;
    restoredTaskHint.value = "";
    pausedRunId.value = "";
    interruptedRunId.value = "";
    autoScreenArmed.value = false;
    finishedPartial.value = true;
    // 026 B078：结束保存同样视为流程已结束，持久化已结束事实，
    // 刷新后不被后端残留 run 误恢复。
    deps.persistFinishedState?.();
    deps.clearWorkflowState();
    const totalScraped = Number(data.result?.total_scraped ?? 0);
    const finished: TaskSnapshot = {
      status: "completed_with_pending", stage: "done",
      progress: { message: "已结束并保存部分结果" }, logs: [], error: "",
      scraped_count: totalScraped,
      source_total: totalScraped,
      platform: data.platform,
    };
    if (scrapeSnapshot.value) scrapeSnapshot.value = finished;
    if (screenSnapshot.value) screenSnapshot.value = finished;
    if (recrawlSnapshot.value) recrawlSnapshot.value = null;
    if (data.scrape_task_id) scrapeTaskId.value = data.scrape_task_id;
    if (data.result) {
      const result = data.result as PipelineResult & { platform?: Platform };
      if (!result.platform && data.platform) result.platform = data.platform;
      deps.setPipelineResult(result);
      if (data.snapshot_run_id) pipelineResultRunId.value = data.snapshot_run_id;
      // 保存结果后恢复画像，保证“继续 AI 筛选”能真正发起而不被画像校验拦下。
      const savedProfile = (result as Record<string, unknown>).profile_summary;
      deps.roundFlow.suppressProfileWatch = true;
      try {
        if (typeof savedProfile === "string" && savedProfile.trim()) {
          profileSummary.value = savedProfile;
        }
        const savedFacts = (result as Record<string, unknown>).profile_facts;
        if (savedFacts && typeof savedFacts === "object") {
          profileFacts.value = savedFacts as Record<string, unknown>;
        }
        await nextTick();
        profileConfirmed.value = true;
      } finally {
        deps.roundFlow.suppressProfileWatch = false;
      }
    }
    scrapeCompleted.value = true;
    resultLoaded.value = true;
    currentRoundStatus.value = "screened";
    // 不强制跳结果页：由"查看结果/继续 AI 筛选"入口决定下一步。
    deps.notify("任务已结束，已完成结果已保存", "success");
  } catch (error) {
    deps.notify(errorMessage(error, "结束任务失败"), "error");
  }
  finally {
    finishSaveBusy.value = false;
  }
}

// 切片7：统一取消 paused 任务（FR-024）。
async function cancelPausedTask(runId: string) {
  if (!runId) return;
  cancelBusy.value = true;
  try {
    await apiRequest(`/api/task/cancel/${encodeURIComponent(runId)}`, {
      method: "POST",
    });
    scrapeBusy.value = false;
    screenBusy.value = false;
    restoredTaskHint.value = "";
    pausedRunId.value = "";
    interruptedRunId.value = "";
    autoScreenArmed.value = false;
    if (scrapeSnapshot.value) scrapeSnapshot.value = { status: "cancelled", progress: { message: "已取消任务" }, logs: [], error: "" };
    if (screenSnapshot.value) screenSnapshot.value = { status: "cancelled", progress: { message: "已取消任务" }, logs: [], error: "" };
    deps.notify("已取消任务，已有结果保留", "warning");
  } catch (error) {
    deps.notify(errorMessage(error, "取消失败，请重试"), "error");
  }
  finally {
    cancelBusy.value = false;
  }
}


function handleStartScrapeClick() {
  if (scrapeBusy.value) {
    void cancelScrape();
    return;
  }
  if (!deps.validateProfileForScreen()) {
    deps.notify("求职画像至少 10 个字（不含首尾空格）", "warning");
    return;
  }
  if (!deps.requireProfileConfirmed()) return;
  if (shouldConfirmNationalScope(selectedKeywords.value, cityList.value)) {
    nationalScopeConfirm.value = "scrape";
    return;
  }
  void startScrape();
}


function openOneClick() {
  if (pipelineBusy.value) {
    deps.notify("当前已有任务在运行或暂停，请先处理完再开始新任务", "warning");
    return;
  }
  profileError.value = "";
  if (!selectedKeywords.value.length) {
    deps.enterSearchStep();
    deps.notify("请先到第二步补齐关键词和城市", "warning");
    return;
  }
  if (shouldConfirmNationalScope(selectedKeywords.value, cityList.value)) {
    nationalScopeConfirm.value = "one-click";
    return;
  }
  openOneClickDialog();
}


function openOneClickDialog() {
  if (!deps.validateProfileForScreen()) {
    deps.notify("求职画像至少 10 个字（不含首尾空格）", "warning");
    return;
  }
  if (!deps.requireProfileConfirmed()) return;
  oneClickOpen.value = true;
}


function confirmOneClick(fields: Record<string, string[]>) {
 oneClickOpen.value = false;
 filterValues.value[draftPlatform.value] = fields;
 void startScrape({ autoScreen: true, fields, profile: profileSummary.value });
}

// B038：历史未筛选轮补筛——退出历史模式，挂载父抓取任务与画像后
// 复用现有"开始 AI 筛选"全流程；后端把结果升级回同一轮次。
async function startScreenFromHistory() {
  const detail = historyDetail.value;
  if (!detail) return;
  const taskId = String(detail.scrape_task_id || "");
  if (!taskId) {
    deps.notify("该轮缺少抓取任务来源，无法发起 AI 筛选；请重新抓取", "warning");
    return;
  }
  if (pipelineBusy.value) {
    deps.notify("当前已有任务在运行或暂停，请先处理完再开始新任务", "warning");
    return;
  }
  historyScreenBusy.value = true;
  try {
  // 退出历史模式（historyRound 置空不触发 deps.returnToLatest 的加载）。
  platformBeforeHistory.value = null;
  historyRound.value = null;
  resultPlatformFilter.value = "all";
  pipelineResult.value = null;
  pipelineResultRunId.value = "";
  resultLoaded.value = false;
  resultRunIds.value = { boss: "", zhilian: "" };
  resultEpoch.value += 1;
  currentRoundStatus.value = "";
  platformState.setDraftPlatform(detail.platform);
  draftPlatform.value = detail.platform;
  setThemePlatform(detail.platform);
  // 等待目标平台 schema/城市加载完成，避免步骤 3 提交旧平台的
  // filter_schema_version 触发后端 409（platform-schema.md L157）。
  await Promise.all([
    deps.loadFilterLabels(detail.platform),
    deps.loadCityCatalog(detail.platform),
  ]);
  // 挂载父抓取任务：AI 筛选从该任务读取同一来源岗位，不重新抓取。
  scrapeTaskId.value = taskId;
  scrapeCompleted.value = true;
  profileSummary.value = String(detail.result?.profile_summary || "");
  const pfacts = (detail.result as PipelineResult & { profile_facts?: unknown }).profile_facts;
  profileFacts.value = (pfacts && typeof pfacts === "object"
    ? pfacts as Record<string, unknown> : {});
  filterValues.value[detail.platform] = {};
  activeCategory.value = "matched";
  deps.enterScreenStep();
  deps.notify("已载入该轮岗位，确认筛选条件后开始 AI 筛选", "info");
  } finally {
    historyScreenBusy.value = false;
  }
}

return {
  restoreRunningTask,
  startScrape,
  cancelScrape,
  continueScrape,
  loadSwitchAccounts,
  flowStartAiScreen,
  startAiScreen,
  continueAiScreen,
  finishPausedTask,
  cancelPausedTask,
  handleStartScrapeClick,
  openOneClick,
  openOneClickDialog,
  confirmOneClick,
  startScreenFromHistory,
};
}
