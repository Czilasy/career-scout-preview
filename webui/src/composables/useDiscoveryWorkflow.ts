// 021 B8 T027：DiscoveryView workflow 动作层（自 DiscoveryView.vue script 原样搬运，函数体零改动，跨域引用经 deps 调用时解析）。
// 031 B8：deps 形参类型 = discoveryDeps.ts 的 WorkflowNeeds（跨域依赖契约）。
import { nextTick, watch } from "vue";
import type { Ref } from "vue";
import type { DiscoveryState } from "./useDiscoveryState";
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
  PageScene,
  TaskSnapshot as ApiTaskSnapshot,
} from "../types";
import type { StepId } from "./useDiscoveryState";
import type { WorkflowNeeds } from "./discoveryDeps";
import { setThemePlatform } from "./useTheme";

const LIVE_SAVED_TASK_STATUSES = new Set(["running", "queued", "paused", "pausing", "interrupted"]);
const TERMINAL_SAVED_TASK_STATUSES = new Set([
  "done", "completed", "completed_with_pending", "partial", "succeeded", "scraped_only",
]);

function isPlatformValue(value: unknown): value is Platform {
  return value === "boss" || value === "zhilian";
}

function isCompletedWorkflowSnapshot(saved: Record<string, any>): boolean {
  // 只有已经展示过结果页、且快照带有结果，才把 sessionStorage 的 unfinished
  // 标记视为过期的完成态快照；02/03 页的半截现场仍按未完成流程恢复。
  if (saved.activeStep !== "results" || saved.resultLoaded === false) return false;
  if (!saved.pipelineResult || typeof saved.pipelineResult !== "object") return false;
  if (saved.pausedRunId || saved.interruptedRunId) return false;
  // Spec041：已抓取未筛选轮只是先看一眼结果，流程未结束；刷新要回到该现场，
  // 不能按完成态快照清掉（否则刷新被带回 01 页）。
  if (String(saved.currentRoundStatus || "") === "scraped_only") return false;

  const statuses = [saved.scrapeSnapshot?.status, saved.screenSnapshot?.status, saved.recrawlSnapshot?.status]
    .map((status) => String(status || ""))
    .filter(Boolean);
  if (statuses.some((status) => LIVE_SAVED_TASK_STATUSES.has(status))) return false;
  return statuses.every((status) => TERMINAL_SAVED_TASK_STATUSES.has(status));
}

export function useDiscoveryWorkflow(state: DiscoveryState, deps: WorkflowNeeds) {
  const { WORKFLOW_STATE_VERSION, activeCategory, activeStep, advancedPanelsOpen, analysisReady, cityText, currentRoundStatus, draftPlatform, enabledSteps, ensureSearchDraftLoaded, filterValues, finishedPartial, historyMode, interruptedRunId, keywords, loadSearchDraftFor, pausedRunId, pipelineResult, pipelineResultRunId, platformState, profileFacts, profileSummary, recrawlBusy, recrawlSnapshot, recrawlTaskId, restoredWorkflowSnapshot, resultLoaded, resultPlatformFilter, resultsBootstrapPending, resultsPageSeen, saveSearchDraftFor, sceneSnapshot, scrapeBusy, scrapeCompleted, scrapeSnapshot, scrapeTaskId, screenBusy, screenPanelOpen, screenSnapshot, screenTaskId, searchPanelsOpen, selectedKeywords, unfinishedWorkflowRestored, workflowStateKey, workflowStateRestored } = state;


function readStoredPayload(): Record<string, any> | null {
  try {
    const raw = sessionStorage.getItem(workflowStateKey.value);
    if (!raw) return null;
    return JSON.parse(raw) as Record<string, any>;
  } catch {
    return null;
  }
}


function readWorkflowState(): Record<string, any> | null {
  try {
    const parsed = readStoredPayload();
    if (!parsed) return null;
    // Spec041：版本升到 2；旧的 1 仍可恢复，缺少现场字段时按默认现场处理。
    return parsed.version === WORKFLOW_STATE_VERSION || parsed.version === 1 ? parsed : null;
  } catch {
    return null;
  }
}


function clearWorkflowState(): void {
  try {
    const pageScene = readStoredPayload()?.pageScene;
    if (pageScene && typeof pageScene === "object") {
      sessionStorage.setItem(workflowStateKey.value, JSON.stringify({ pageScene }));
    } else {
      sessionStorage.removeItem(workflowStateKey.value);
    }
  } catch { /* storage unavailable */ }
}

// 026 B078：已结束事实独立持久化（localStorage，跨会话）。
// "是否进过 04 页（结果页）"是流程是否结束的唯一判据（spec FR-001/A1）。
// 与 workflow 快照（sessionStorage）分离：进 04 页/结束保存时置位并持久化，
// 开始新一轮时清除；刷新/重启后 restoreWorkflowState 据此恢复，即使后端
// 残留历史 interrupted run，也不把已结束流程误恢复成半截流程。
//
// Spec041 返工（真实验收失败项一）：已结束事实同时带上"那一轮在哪个平台"，
// 会话现场存档缺失时（新标签页 / 重启应用）用它先把平台与结果页骨架接回来，
// 不让用户看到 BOSS 空上传页。
function finishedStateKey(): string {
  return `${workflowStateKey.value}:finished`;
}

function readFinishedState(): {
  resultsPageSeen: boolean;
  finishedPartial: boolean;
  platform: Platform | "";
  runId: string;
} {
  try {
    const raw = localStorage.getItem(finishedStateKey());
    if (!raw) return { resultsPageSeen: false, finishedPartial: false, platform: "", runId: "" };
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const platform = isPlatformValue(parsed.platform) ? parsed.platform : "";
    return {
      resultsPageSeen: Boolean(parsed.resultsPageSeen),
      finishedPartial: Boolean(parsed.finishedPartial),
      platform,
      runId: String(parsed.runId || ""),
    };
  } catch {
    return { resultsPageSeen: false, finishedPartial: false, platform: "", runId: "" };
  }
}

function persistFinishedState(): void {
  try {
    const roundPlatform = platformState.result
      || screenSnapshot.value?.platform
      || scrapeSnapshot.value?.platform
      || draftPlatform.value;
    localStorage.setItem(finishedStateKey(), JSON.stringify({
      resultsPageSeen: resultsPageSeen.value,
      finishedPartial: finishedPartial.value,
      platform: roundPlatform,
      runId: pipelineResultRunId.value || "",
    }));
  } catch { /* best effort */ }
}

function clearFinishedState(): void {
  try { localStorage.removeItem(finishedStateKey()); } catch { /* best effort */ }
}

// 刷新可能不会先触发组件卸载。工作流现场只在卸载时写入，会让刚完成的
// 简历分析和用户刚改好的搜索条件留在内存里，刷新后又被旧数据覆盖。状态
// 已完成首次恢复后，任何现场变化都立即沿用同一份 sessionStorage 存档。
watch(
  [
    workflowStateRestored,
    activeStep,
    analysisReady,
    keywords,
    selectedKeywords,
    cityText,
    filterValues,
    profileSummary,
    profileFacts,
    scrapeTaskId,
    screenTaskId,
    pausedRunId,
    interruptedRunId,
    recrawlTaskId,
    scrapeCompleted,
    scrapeSnapshot,
    screenSnapshot,
    recrawlSnapshot,
    pipelineResult,
    pipelineResultRunId,
    currentRoundStatus,
    resultLoaded,
    resultsPageSeen,
    activeCategory,
    resultPlatformFilter,
  ],
  () => {
    if (workflowStateRestored.value) persistWorkflowState();
  },
  { deep: true },
);

/** 把当前现场写成会话快照（完成态快照保留结果与现场，供刷新原地接回）。 */
function writeWorkflowSnapshot(completed: boolean): void {
  try {
    const existingPageScene = readStoredPayload()?.pageScene;
    sessionStorage.setItem(workflowStateKey.value, JSON.stringify({
      version: WORKFLOW_STATE_VERSION,
      unfinished: !completed,
      completed,
      activeStep: activeStep.value,
      analysisReady: analysisReady.value,
      keywords: keywords.value,
      selectedKeywords: selectedKeywords.value,
      cityText: cityText.value,
      filterValues: filterValues.value,
      profileSummary: profileSummary.value,
      profileFacts: profileFacts.value,
      scrapeTaskId: scrapeTaskId.value,
      screenTaskId: screenTaskId.value,
      pausedRunId: pausedRunId.value,
      interruptedRunId: interruptedRunId.value,
      recrawlTaskId: recrawlTaskId.value,
      scrapeCompleted: scrapeCompleted.value,
      scrapeSnapshot: scrapeSnapshot.value,
      screenSnapshot: screenSnapshot.value,
      recrawlSnapshot: recrawlSnapshot.value,
      pipelineResult: pipelineResult.value,
      pipelineResultRunId: pipelineResultRunId.value,
      currentRoundStatus: currentRoundStatus.value,
      resultLoaded: resultLoaded.value,
      resultsPageSeen: resultsPageSeen.value,
      // Spec041 返工：结果分类与结果平台筛选也是结果页现场的一部分。
      activeCategory: activeCategory.value,
      resultPlatformFilter: resultPlatformFilter.value,
      // 轮次平台身份：刷新后同步恢复平台与品牌色，不让默认 BOSS 先糊一屏。
      platform: draftPlatform.value,
      resultPlatform: platformState.result || "",
      ...(existingPageScene && typeof existingPageScene === "object"
        ? { pageScene: existingPageScene }
        : {}),
    }));
  } catch {
    // sessionStorage is best effort; the backend remains authoritative.
  }
}

function markResultsPageSeen(): void {
  resultsPageSeen.value = true;
  persistFinishedState();
  // Spec041 返工：进 04 页不再把整份现场清掉——完成态现场是刷新后"还在这一页"
  // 的唯一来源（真实验收失败项一）。确实没有结果可展示时才按旧行为清掉。
  if (workflowStateRestored.value) persistWorkflowState();
  else clearWorkflowState();
}

// D7：未登录类错误码（BOSS/智联 preflight 与任务暂停的稳定错误码）。


function workflowIsFinished(): boolean {
  return resultsPageSeen.value || finishedPartial.value;
}


function persistWorkflowState(): void {
  if (!workflowStateRestored.value) return;
  const snapshot = {
    activeStep: activeStep.value,
    resultLoaded: resultLoaded.value,
    pipelineResult: pipelineResult.value,
    // Spec041：完成态判定需要知道这轮是否"已抓取未筛选"（未筛选轮不算结束）。
    currentRoundStatus: currentRoundStatus.value,
    pausedRunId: pausedRunId.value,
    interruptedRunId: interruptedRunId.value,
    scrapeSnapshot: scrapeSnapshot.value,
    screenSnapshot: screenSnapshot.value,
    recrawlSnapshot: recrawlSnapshot.value,
  };
  // 完成态：结果页现场要留下（刷新原地接回）；没有结果的完成标记仍清掉。
  if (workflowIsFinished() || isCompletedWorkflowSnapshot(snapshot)) {
    const restorable = activeStep.value === "results"
      && resultLoaded.value
      && Boolean(pipelineResult.value)
      && !scrapeBusy.value && !screenBusy.value && !recrawlBusy.value
      && !pausedRunId.value && !interruptedRunId.value;
    if (!restorable) {
      clearWorkflowState();
      return;
    }
    writeWorkflowSnapshot(true);
    return;
  }
  const unfinished = !workflowIsFinished()
    && !isCompletedWorkflowSnapshot(snapshot)
    && Boolean(
      analysisReady.value || scrapeTaskId.value || screenTaskId.value || pausedRunId.value || interruptedRunId.value
      || scrapeBusy.value || screenBusy.value
      || [scrapeSnapshot.value?.status, screenSnapshot.value?.status].some((status) =>
        ["running", "queued", "paused", "interrupted"].includes(String(status))),
    );
  if (!unfinished) {
    clearWorkflowState();
    return;
  }
  writeWorkflowSnapshot(false);
}


// 041 验收补丁：恢复流程（刷新/切画像接回）会把 analysisReady 恢复为 true，
// 但它不是"本次分析刚完成"，不能借此强制展开 02/03 面板——那会覆盖现场存档
// 里用户收起卡片的状态（FR-001 面板展开/收起保持）。只在恢复这一拍到
// 下一拍之间抑制自动展开；用户真实操作（分析完成、跳过简历）不在窗口内。
let suppressPanelAutoExpand = false;

function restoreWorkflowState(): void {
  suppressPanelAutoExpand = true;
  try {
    restoreWorkflowStateInner();
  } finally {
    // 无论走哪条恢复路径，搜索草稿工作副本都要挂到"当前平台的槽位"上：
    // 之后的编辑才会落到该平台，切平台时也才保存得下来（真实验收失败项三）。
    ensureSearchDraftLoaded();
    void nextTick(() => { suppressPanelAutoExpand = false; });
  }
}

/** 把某轮平台同步接到草稿/结果/品牌色，刷新时不再先给一屏默认 BOSS。 */
function applyRoundPlatform(platform: Platform): void {
  platformState.setDraftPlatform(platform);
  draftPlatform.value = platform;
  platformState.setResultPlatform(platform);
  setThemePlatform(platform);
  // 搜索草稿按平台槽位取回（两平台各自保留；没有存档时保持空草稿）。
  loadSearchDraftFor(platform);
}

/** 恢复一份可展示的轮次快照（完成态与未完成态共用同一份字段语义）。 */
function restoreSnapshotPayload(saved: Record<string, any>): void {
  restoredWorkflowSnapshot.value = saved;
  const roundPlatform = isPlatformValue(saved.resultPlatform)
    ? saved.resultPlatform
    : (isPlatformValue(saved.platform) ? saved.platform : "");
  if (roundPlatform) applyRoundPlatform(roundPlatform);
  if (saved.activeStep) activeStep.value = saved.activeStep as StepId;
  analysisReady.value = Boolean(saved.analysisReady);
  // Spec041 返工：搜索草稿以"平台槽位"为准（两个平台各自保留、刷新后都在）；
  // 只有该画像没有槽位存档（旧版本快照）时才退回快照里的单份草稿，并写进槽位。
  const restoredFromSlots = roundPlatform ? loadSearchDraftFor(roundPlatform) : false;
  if (!restoredFromSlots) {
    keywords.value = Array.isArray(saved.keywords) ? saved.keywords : [];
    selectedKeywords.value = Array.isArray(saved.selectedKeywords) ? saved.selectedKeywords : [];
    cityText.value = String(saved.cityText || "");
    if (roundPlatform) saveSearchDraftFor(roundPlatform);
  }
  if (saved.filterValues && typeof saved.filterValues === "object") {
    filterValues.value = { boss: {}, zhilian: {}, ...saved.filterValues };
  }
  profileSummary.value = String(saved.profileSummary || "");
  profileFacts.value = saved.profileFacts && typeof saved.profileFacts === "object" ? saved.profileFacts : {};
  scrapeTaskId.value = String(saved.scrapeTaskId || "");
  screenTaskId.value = String(saved.screenTaskId || "");
  pausedRunId.value = String(saved.pausedRunId || "");
  interruptedRunId.value = String(saved.interruptedRunId || "");
  recrawlTaskId.value = String(saved.recrawlTaskId || "");
  scrapeCompleted.value = Boolean(saved.scrapeCompleted);
  scrapeSnapshot.value = saved.scrapeSnapshot || null;
  screenSnapshot.value = saved.screenSnapshot || null;
  recrawlSnapshot.value = saved.recrawlSnapshot || null;
  pipelineResult.value = saved.pipelineResult || null;
  pipelineResultRunId.value = String(saved.pipelineResultRunId || "");
  currentRoundStatus.value = String(saved.currentRoundStatus || "");
  resultLoaded.value = Boolean(saved.resultLoaded);
  if (saved.activeCategory) activeCategory.value = saved.activeCategory;
  if (saved.resultPlatformFilter) resultPlatformFilter.value = saved.resultPlatformFilter;
  sceneSnapshot.value = null;
}

function restoreWorkflowStateInner(): void {
  // 026 B078：已结束事实独立持久化（localStorage），作为恢复判定的唯一闸门。
  const finished = readFinishedState();
  if (finished.resultsPageSeen) resultsPageSeen.value = true;
  if (finished.finishedPartial) finishedPartial.value = true;
  const saved = readWorkflowState();
  // Spec041 返工（真实验收失败项一）：完成态现场优先原地接回（结果页 + 平台 +
  // 结果分类 + 页面现场）。关页时序可能让"已结束事实"来不及写入，所以判据是
  // 快照本身是不是完成态结果页现场，而不是标记写没写。
  if (saved && isCompletedWorkflowSnapshot(saved)) {
    restoreSnapshotPayload(saved);
    resultsPageSeen.value = true;
    workflowStateRestored.value = true;
    persistFinishedState();
    // 立刻写回完成态快照（不等恢复后的第一拍 watch），下一次刷新继续原地接回。
    persistWorkflowState();
    return;
  }
  if (resultsPageSeen.value || finishedPartial.value) {
    // 会话现场存档缺失（换标签页/重启应用）：先同步恢复平台与结果页骨架，
    // 由 maybeAutoStartNewRound 从后端最新轮补齐；确实没有结果再退回新一轮。
    if (isPlatformValue(finished.platform)) {
      applyRoundPlatform(finished.platform);
      activeStep.value = "results";
      resultsBootstrapPending.value = true;
    }
    workflowStateRestored.value = true;
    return;
  }
  if (!saved?.unfinished) {
    workflowStateRestored.value = true;
    return;
  }
  unfinishedWorkflowRestored.value = true;
  restoreSnapshotPayload(saved);
  // 026 B078：已结束事实（localStorage，line 上方已恢复）优先；快照内的
  // resultsPageSeen 仅作未结束时兜底（未结束快照里该值恒 false），
  // 不得覆盖"已进 04 页"判定，否则异常态下会复发 B078。
  if (!resultsPageSeen.value) {
    resultsPageSeen.value = Boolean(saved.resultsPageSeen);
  }
  workflowStateRestored.value = true;
}


function restoreSaved02State(): void {
  const saved = restoredWorkflowSnapshot.value;
  if (!saved || resultsPageSeen.value) return;
  // 任务接口只负责恢复后台任务状态；02 页的用户草稿和停留步骤以本地快照为准。
  if (saved.activeStep) activeStep.value = saved.activeStep as StepId;
  analysisReady.value = Boolean(saved.analysisReady);
  keywords.value = Array.isArray(saved.keywords) ? saved.keywords : [];
  selectedKeywords.value = Array.isArray(saved.selectedKeywords) ? saved.selectedKeywords : [];
  cityText.value = String(saved.cityText || "");
  if (saved.filterValues && typeof saved.filterValues === "object") {
    filterValues.value = { boss: {}, zhilian: {}, ...saved.filterValues };
  }
  profileSummary.value = String(saved.profileSummary || "");
  profileFacts.value = saved.profileFacts && typeof saved.profileFacts === "object" ? saved.profileFacts : {};
  scrapeCompleted.value = Boolean(saved.scrapeCompleted);
  pipelineResult.value = saved.pipelineResult || null;
  pipelineResultRunId.value = String(saved.pipelineResultRunId || "");
  currentRoundStatus.value = String(saved.currentRoundStatus || "");
  resultLoaded.value = Boolean(saved.resultLoaded);
  // 恢复 02/03 页时面板默认关闭：任务运行中/完成后不自动展开，
  // 只有简历分析完成（analysisReady watch）才自动打开一次。
}


function enterSearchStep() {
  // 面板不再无任务自动展开：任务运行中/完成后保持关闭，只有简历分析完成
  // （analysisReady 触发 watch）时才自动打开一次，展示 AI 预填内容。
  activeStep.value = "search";
}


function enterScreenStep() {
  // 面板默认关闭：AI 筛选中或任务完成后都不自动展开，只有简历分析完成时
  // 由 analysisReady watch 自动打开一次。
  activeStep.value = "screen";
}


function selectStep(step: string) {
  if (historyMode.value && step !== "results") {
    notify("历史轮次不可改写，请先回到最新", "warning");
    return;
  }
  if (!enabledSteps.value.includes(step as StepId)) return;
  if (step === "search") enterSearchStep();
  else if (step === "screen") enterScreenStep();
  else activeStep.value = step as StepId;
}


function notify(message: string, tone: Notice["tone"] = "info") {
  deps.emit("notify", { message, tone });
}

// 简历分析完成（AI 预填搜索词/筛选条件）时自动打开 02/03 页配置面板，
// 让用户看到并修改预填内容；任务运行中/完成后不再自动展开。
// 仅在 analysisReady 由 false 变 true 的瞬间触发一次，用户手动开关不被打断。
watch(analysisReady, (ready) => {
  if (ready && !suppressPanelAutoExpand) {
    searchPanelsOpen.value = true;
    advancedPanelsOpen.value = true;
    screenPanelOpen.value = true;
  }
});

return {
  readWorkflowState,
  clearWorkflowState,
  workflowIsFinished,
  persistWorkflowState,
  restoreWorkflowState,
  restoreSaved02State,
  enterSearchStep,
  enterScreenStep,
  selectStep,
  notify,
  // 026 B078：已结束事实持久化
  markResultsPageSeen,
  persistFinishedState,
  clearFinishedState,
};
}
