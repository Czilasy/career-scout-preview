// 021 B8 T027：DiscoveryView workflow 动作层（自 DiscoveryView.vue script 原样搬运，函数体零改动，跨域引用经 deps 调用时解析）。
import { watch } from "vue";
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
  TaskSnapshot as ApiTaskSnapshot,
} from "../types";
import type { StepId } from "./useDiscoveryState";

export function useDiscoveryWorkflow(state: DiscoveryState, deps: any = {}) {
  const { WORKFLOW_STATE_VERSION, activeStep, advancedPanelsOpen, analysisReady, cityText, currentRoundStatus, enabledSteps, filterValues, finishedPartial, historyMode, interruptedRunId, keywords, pausedRunId, pipelineResult, pipelineResultRunId, profileFacts, profileSummary, recrawlBusy, recrawlSnapshot, recrawlTaskId, restoredWorkflowSnapshot, resultLoaded, resultsPageSeen, scrapeBusy, scrapeCompleted, scrapeSnapshot, scrapeTaskId, screenBusy, screenPanelOpen, screenSnapshot, screenTaskId, searchPanelsOpen, selectedKeywords, unfinishedWorkflowRestored, workflowStateKey, workflowStateRestored } = state;


function readWorkflowState(): Record<string, any> | null {
  try {
    const raw = sessionStorage.getItem(workflowStateKey.value);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, any>;
    return parsed.version === WORKFLOW_STATE_VERSION ? parsed : null;
  } catch {
    return null;
  }
}


function clearWorkflowState(): void {
  try { sessionStorage.removeItem(workflowStateKey.value); } catch { /* storage unavailable */ }
}

// 026 B078：已结束事实独立持久化（localStorage，跨会话）。
// "是否进过 04 页（结果页）"是流程是否结束的唯一判据（spec FR-001/A1）。
// 与 workflow 快照（sessionStorage）分离：进 04 页/结束保存时置位并持久化，
// 开始新一轮时清除；刷新/重启后 restoreWorkflowState 据此恢复，即使后端
// 残留历史 interrupted run，也不把已结束流程误恢复成半截流程。
function finishedStateKey(): string {
  return `${workflowStateKey.value}:finished`;
}

function readFinishedState(): { resultsPageSeen: boolean; finishedPartial: boolean } {
  try {
    const raw = localStorage.getItem(finishedStateKey());
    if (!raw) return { resultsPageSeen: false, finishedPartial: false };
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return {
      resultsPageSeen: Boolean(parsed.resultsPageSeen),
      finishedPartial: Boolean(parsed.finishedPartial),
    };
  } catch {
    return { resultsPageSeen: false, finishedPartial: false };
  }
}

function persistFinishedState(): void {
  try {
    localStorage.setItem(finishedStateKey(), JSON.stringify({
      resultsPageSeen: resultsPageSeen.value,
      finishedPartial: finishedPartial.value,
    }));
  } catch { /* best effort */ }
}

function clearFinishedState(): void {
  try { localStorage.removeItem(finishedStateKey()); } catch { /* best effort */ }
}

function markResultsPageSeen(): void {
  resultsPageSeen.value = true;
  // 进 04 页即流程结束：清空未完成快照（既有行为），并持久化已结束事实。
  clearWorkflowState();
  persistFinishedState();
}

// D7：未登录类错误码（BOSS/智联 preflight 与任务暂停的稳定错误码）。


function workflowIsFinished(): boolean {
  return resultsPageSeen.value || finishedPartial.value;
}


function persistWorkflowState(): void {
  if (!workflowStateRestored.value) return;
  const unfinished = !workflowIsFinished() && Boolean(
    analysisReady.value || scrapeTaskId.value || screenTaskId.value || pausedRunId.value || interruptedRunId.value
    || scrapeBusy.value || screenBusy.value
    || [scrapeSnapshot.value?.status, screenSnapshot.value?.status].some((status) =>
      ["running", "queued", "paused", "interrupted"].includes(String(status))),
  );
  if (!unfinished) {
    clearWorkflowState();
    return;
  }
  try {
    sessionStorage.setItem(workflowStateKey.value, JSON.stringify({
      version: WORKFLOW_STATE_VERSION,
      unfinished: true,
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
    }));
  } catch {
    // sessionStorage is best effort; the backend remains authoritative.
  }
}


function restoreWorkflowState(): void {
  // 026 B078：已结束事实独立持久化（localStorage），作为恢复判定的唯一闸门。
  const finished = readFinishedState();
  if (finished.resultsPageSeen) resultsPageSeen.value = true;
  if (finished.finishedPartial) finishedPartial.value = true;
  const saved = readWorkflowState();
  if (!saved?.unfinished) {
    workflowStateRestored.value = true;
    return;
  }
  // 026 B078：已结束（进过 04 页/结束保存）的流程，即使 sessionStorage 残留
  // 未完成快照，也不恢复任何步骤状态——否则刷新瞬间会短暂恢复 02/03 页
  //（随后被 maybeAutoStartNewRound 的 resetWorkflow 纠正，产生脏过渡窗口）。
  if (resultsPageSeen.value || finishedPartial.value) {
    workflowStateRestored.value = true;
    return;
  }
  unfinishedWorkflowRestored.value = true;
  restoredWorkflowSnapshot.value = saved;
  // 026 B078：已结束事实（localStorage，line 上方已恢复）优先；快照内的
  // resultsPageSeen 仅作未结束时兜底（未结束快照里该值恒 false），
  // 不得覆盖"已进 04 页"判定，否则异常态下会复发 B078。
  if (!resultsPageSeen.value) {
    resultsPageSeen.value = Boolean(saved.resultsPageSeen);
  }
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
  if (ready) {
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