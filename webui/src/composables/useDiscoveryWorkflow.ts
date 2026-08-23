// 021 B8 T027：DiscoveryView workflow 动作层（自 DiscoveryView.vue script 原样搬运，函数体零改动，跨域引用经 deps 调用时解析）。
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
  const { WORKFLOW_STATE_VERSION, activeStep, analysisReady, cityText, currentRoundStatus, enabledSteps, filterValues, finishedPartial, historyMode, interruptedRunId, keywords, pausedRunId, pipelineResult, pipelineResultRunId, profileFacts, profileSummary, recrawlSnapshot, recrawlTaskId, restoredWorkflowSnapshot, resultLoaded, resultsPageSeen, scrapeBusy, scrapeCompleted, scrapeSnapshot, scrapeTaskId, screenBusy, screenPanelOpen, screenSnapshot, screenTaskId, searchPanelsOpen, selectedKeywords, unfinishedWorkflowRestored, workflowStateKey, workflowStateRestored } = state;


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
  const saved = readWorkflowState();
  if (!saved?.unfinished) {
    workflowStateRestored.value = true;
    return;
  }
  unfinishedWorkflowRestored.value = true;
  restoredWorkflowSnapshot.value = saved;
  resultsPageSeen.value = Boolean(saved.resultsPageSeen);
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
}


function enterSearchStep() {
  // B040：抓取运行中切回步骤 2 时保持配置卡收拢。
  searchPanelsOpen.value = !scrapeBusy.value;
  activeStep.value = "search";
}


function enterScreenStep() {
  // B040：AI 筛选运行中切回步骤 3 时保持配置卡收拢。
  screenPanelOpen.value = !resultLoaded.value && !screenBusy.value;
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
};
}