<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch, type Ref } from "vue";
import {
  ArrowLeftToLine, Bookmark, Check, Download, FileText, Filter, History, LoaderCircle, Play,
  RotateCcw, Search, SlidersHorizontal, Sparkles, UploadCloud, X,
} from "@lucide/vue";
import CollapsibleCard from "../components/CollapsibleCard.vue";
import ExecutionModeSelector from "../components/ExecutionModeSelector.vue";
import JobLifecycleDialog from "../components/JobLifecycleDialog.vue";
import ParallelPlatformProgress from "../components/ParallelPlatformProgress.vue";
import JobWorkspace from "../components/JobWorkspace.vue";
import LocationPicker from "../components/LocationPicker.vue";
import HistoryRoundProfile from "../components/HistoryRoundProfile.vue";
import ResultHistoryDrawer from "../components/ResultHistoryDrawer.vue";
import LogViewerDialog from "../components/LogViewerDialog.vue";
import OneClickScreenDialog, {
  type OneClickFilterGroup,
  crossPlatformDedupeEnabled,
} from "../components/OneClickScreenDialog.vue";
import StepNavigator from "../components/StepNavigator.vue";
import ContinuePlatformGuide from "../components/ContinuePlatformGuide.vue";
import PauseBatchChoiceDialog from "../components/PauseBatchChoiceDialog.vue";
import ScreenRoundActions from "../components/ScreenRoundActions.vue";
import ScreenRecrawlProgress from "../components/ScreenRecrawlProgress.vue";
import PendingRecrawlCapsule from "../components/PendingRecrawlCapsule.vue";
import TaskProgress from "../components/TaskProgress.vue";
import SavedSearchPackagePicker from "../components/SavedSearchPackagePicker.vue";
import SavedSearchPackageSaveActions from "../components/SavedSearchPackageSaveActions.vue";
import { useScreenRoundFlow } from "../composables/useScreenRoundFlow";
import {
  attachRoundFlow,
  createDiscoveryDeps,
  wireDiscoveryDeps,
} from "../composables/discoveryDeps";
import { useModeWarnings } from "../composables/useModeWarnings";
import { useNarrowSearchLayout } from "../composables/useNarrowSearchLayout";
import { withoutRecrawl } from "../screenFlow";
import { platformLabel, type RoundStatusPayload } from "../discovery";
import type {
  AdvancedSettingsState, CandidateProfile, ExecutionSelection, ExecutionSettings,
  FrozenSearchScope, LocationCondition, Notice, Platform, PlatformCityCatalog,
  PlatformFilterSchema, RoundContext, TaskSnapshot as ApiTaskSnapshot,
} from "../types";
import { useDiscoveryState } from "../composables/useDiscoveryState";
import { useDiscoveryWorkflow } from "../composables/useDiscoveryWorkflow";
import { useDiscoverySearch } from "../composables/useDiscoverySearch";
import { useDiscoveryExecution } from "../composables/useDiscoveryExecution";
import { useDiscoveryTasks } from "../composables/useDiscoveryTasks";
import { useDiscoveryResults } from "../composables/useDiscoveryResults";
import { useDiscoverySceneState } from "../composables/useDiscoverySceneState";
import { useDiscoverySceneIdentity } from "../composables/useDiscoverySceneIdentity";
import { useProfileInputScene } from "../composables/useProfileInputScene";
import { useDiscoveryIslandBridge } from "../composables/useDiscoveryIslandBridge";
import { useDiscoveryLogViewer } from "../composables/useDiscoveryLogViewer";
import { useSearchPackages } from "../composables/useSearchPackages";
import {
  useDiscoveryParallelFlow,
} from "../composables/useDiscoveryParallelFlow";
import {
  createDiscoveryFlowResultProjection,
  useDiscoveryFlowCoordinator,
} from "../composables/useDiscoveryFlowCoordinator";
type ResultCategory = "matched" | "unmatched" | "uncertain" | "dropped";
type FieldLabel = [string, unknown, string | Record<string, string>];
interface AnalyzeResponse {
  ok: boolean;
  fields: Record<string, unknown>;
  labels: Record<string, FieldLabel>;
  platform?: Platform;
  filter_schema_version?: number;
  semantic?: Record<string, string[]>;
}
const props = defineProps<{ profileId: string }>();
const emit = defineEmits<{
  notify: [notice: Notice];
  "profile-created": [profile: CandidateProfile];
  "job-feedback-changed": [payload: { profileId: string; jobId: string }];
  "round-status": [payload: RoundStatusPayload | null];
  "open-browser-accounts": [];
  "island-notice": [payload: { id: string; title: string; detail?: string; target?: "results" | "task" }];
}>();
const state = useDiscoveryState(props, emit);
const parallelFlow = useDiscoveryParallelFlow({ profileId: () => props.profileId, onActionError: (message) => { notify(message, "error"); } });
const parallelMode = ref(true);
const {
  WORKFLOW_STATE_VERSION,
  workflowStateKey,
  workflowStateRestored,
  unfinishedWorkflowRestored,
  resultsPageSeen,
  restoredWorkflowSnapshot,
  activeTaskRestored,
  LOGIN_ERROR_CODES,
  loginGuide,
  platformState,
  draftPlatform,
  viewPlatform,
  schemaLoader,
  cityLoader,
  schemaRef,
  cityCatalogRef,
  schemaBusy,
  cityCatalogBusy,
  draftPlatformDisabled,
  pendingPlatformSwitch,
  nationalScopeConfirm,
  steps,
  stepCopy,
  activeStep,
  analysisReady,
  selectedFile,
  aiConsent,
  dragActive,
  uploadBusy,
  resumeError,
  keywords,
  selectedKeywords,
  customKeyword,
  cityText,
  locationDraft,
  customCity,
  fieldLabels,
  filterValues,
  profileSummary,
  profileFacts,
  resumeAnalysis,
  appliedResumePlatforms,
  scrapeTaskId,
  scrapeActionBusy,
  scrapeBusy,
  scrapeSnapshot,
  screenBusy,
  pausingScreen,
  screenSnapshot,
  screenTaskId,
  recrawlBusy,
  recrawlTaskId,
  recrawlSnapshot,
  recrawlRetryCount,
  scrapeCompleted,
  resultLoaded,
  resultsBootstrapPending,
  finishedPartial,
  recrawlPlatformGuide,
  exportBusy,
  finishSaveBusy,
  cancelBusy,
  historyScreenBusy,
  restoredTaskHint,
  pausedRunId,
  interruptedRunId,
  // 现场注入点：DiscoveryView.spec 用 setupState.pipelineResult 投一份恢复结果造现场；
  // 判定本体已收进 useDiscoveryState（hasRenderableResult），这条绑定必须保留。
  pipelineResult,
  pipelineResultRunId,
  resultPlatformFilter,
  resultEpoch,
  recrawlCapsuleDismissed,
  dismissRecrawlCapsule,
  resultRunIds,
  historyStore,
  historyRound,
  platformBeforeHistory,
  historyMode,
  returningFromHistory,
  currentRoundStatus,
  isScrapedOnly,
  historyStatusText,
  historyProfileText,
  activeCategory,
  rejectedIds,
  feedbackBusyIds,
  jdBusyIds,
  advancedBusy,
  executionSelection,
  scopePreview,
  scopePreviewBusy,
  scopePreviewReqId,
  advancedSettings,
  advancedRanges,
  pagesValue,
  executionModeLabels,
  executionModeSummary,
  screenPanelOpen,
  oneClickOpen,
  oneClickGroups,
  hasOldResult,
  autoScreenArmed,
  autoScreenFields,
  autoScreenProfile,
  profileError,
  profileInputEl,
  profileConfirmed,
  pipelineBusy,
  searchPanelsOpen,
  advancedPanelsOpen,
  pollTimer,
  scopeLocked,
  scopeLockReason,
  enabledSteps,
  completedSteps,
  currentCopy,
  cityList,
  effectiveSearchCities,
  FILTER_SENTINEL_LABELS,
  filterGroups,
  searchSummary,
  screenSummaryChips,
  filteredPipelineResult,
  hasRenderableResult,
  groups,
  uncertainByPlatform,
  resultTabs,
  currentJobs,
  currentEmptyMessage,
  COMPLETED_TASK_STATUSES,
  SPEED_FIELDS,
  POLL_MAX_RETRIES,
  POLL_BASE_DELAY,
  POLL_MAX_DELAY,
  pollRetryCount,
  lifecycleDialogOpen,
  lifecycleDialogJob,
  roundStatusPayload,
  historyOpen,
  historyItems,
  historyLoading,
  historyError,
  historyDeleting,
  historyDeleteTarget,
  historyDetail,
  showHistory,
  hideHistory,
  openHistoryRound,
  confirmHistoryDelete,
  cancelHistoryDelete,
  deleteHistoryRound,
  archiveHistoryLatest,
} = state;
const deps = createDiscoveryDeps({ emit, props });
const workflow = useDiscoveryWorkflow(state, deps);
const search = useDiscoverySearch(state, deps);
const execution = useDiscoveryExecution(state, deps);
const tasks = useDiscoveryTasks(state, deps);
const { currentResultsFlowId, currentResultsFlowSelection } = createDiscoveryFlowResultProjection({
  state,
  flow: parallelFlow,
  parallelMode,
});
const results = useDiscoveryResults(
  state,
  deps,
  currentResultsFlowId,
  parallelFlow.fetchFlowResults,
  currentResultsFlowSelection,
);
state.capsuleReturnToLatest.value = results.returnToLatest;
wireDiscoveryDeps(deps, {
  workflow,
  search,
  execution: { ...execution, isFlowOwnedScrapeTask: parallelFlow.isFlowOwnedScrapeTask },
  tasks: {
    ...tasks,
    isFlowOwnedScrapeTask: parallelFlow.isFlowOwnedScrapeTask,
    getFlowTaskIds: () => parallelFlow.trackList.value.flatMap((track) => [
      track.scrape_run_id,
      track.screen_run_id,
    ].filter((runId): runId is string => Boolean(runId))),
    abandonRound: async () => { await tasks.abandonRound(); },
  },
  results,
});
const sceneStore = useDiscoverySceneState();
const { identity: sceneIdentity } = useDiscoverySceneIdentity(state, props);
useProfileInputScene(state, sceneIdentity);
useDiscoveryIslandBridge(state);
const {
  open: historyLogOpen,
  initialTaskId: historyLogTaskId,
  openForTask: openHistoryLog,
} = useDiscoveryLogViewer(() => props.profileId);
const {
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
  markResultsPageSeen,
  persistFinishedState,
  clearFinishedState,
  showLoginGuide,
  isLoginErrorCode,
  confirmNationalScope,
  cancelNationalScope,
  setDraftPlatform,
  requestDraftPlatform,
  cancelPlatformSwitch,
  confirmPlatformSwitch,
  loadFilterLabels,
  loadCityCatalog,
  confirmCities,
  addCustomCity,
  removeCity,
  toggleFilter,
  chooseFile,
  handleDrop,
  analyzeResume,
  initializeFromAnalysis,
  applyResumeAnalysisToCurrentSchema,
  toggleKeyword,
  removeKeyword,
  addCustomKeyword,
  confirmProfile,
  handleProfileInput,
  handleProfileBlur,
  validateProfileForScreen,
  requireProfileConfirmed,
  loadAdvancedSettings,
  saveAdvancedSettings,
  currentExecutionSettings,
  refreshScopePreview,
  selectExecutionMode,
  mergeManualRanges,
  advancedRange,
  clampAdvanced,
  restoreRunningTask,
  scrapeAction,
  scrapeCanFinish,
  startScrape,
  pauseScrape,
  cancelActiveScrape,
  continueScrape,
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
  pollTask,
  saveScrapedOnlySnapshot,
  viewScrapedOnly,
  abandonRound: abandonRoundTask,
  cancelActiveTasksForNewRound,
  finishScreenSave,
  isCompletedTaskStatus,
  enrichPausedSnapshot,
  recrawlUncertain,
  chooseRecrawlPlatform,
  continueRecrawl,
  pollRecrawl,
  mergeRecrawlUpdates,
  resetWorkflow,
  setPipelineResult,
  hasLiveTaskState,
  loadLatestResult,
  fetchMergedLatestResult,
  clearLatestResult,
  openHistoryDrawer,
  toggleHistoryDrawer,
  closeHistoryDrawer,
  enterHistoryRound,
  returnToLatest,
  onResultPlatformFilterChange,
  exportResultCsv,
  restoreLocationsFromContext,
  jobId,
  withBusy,
  ensureFeedbackProfile,
  feedbackPayload,
  toggleInterest,
  toggleRejected,
  retryJd,
  lifecycleJob,
  onJobFeedbackChanged,
  openLifecycleDialog,
  closeLifecycleDialog,
  handleLifecycleDialogKeydown,
} = { ...workflow, ...search, ...execution, ...tasks, ...results };
function handleNewResumeInput(event: Event) { if ((event.target as HTMLInputElement)?.files?.length || (event as DragEvent).dataTransfer?.files?.length) parallelFlow.resetConditionState(); if (event.type === "drop") handleDrop(event as DragEvent); else chooseFile(event); }
restoreWorkflowState();
restoreSaved02State();
const flowCoordinator = useDiscoveryFlowCoordinator({
  state,
  flow: parallelFlow,
  parallelMode,
  results,
  sceneStore,
  profileId: () => props.profileId,
  emitIslandNotice: (payload) => emit("island-notice", payload),
  notify,
  requestDraftPlatform,
  openOneClick,
  resetWorkflow: tasks.resetWorkflow,
  abandonRound: abandonRoundTask,
  maybeAutoStartNewRound: tasks.maybeAutoStartNewRound,
  restoreWorkflowState,
  restoreSaved02State,
  loadAdvancedSettings,
  loadFilterLabels,
  loadCityCatalog,
  restoreRunningTask,
});
const {
  flowPresentation,
  recoveryParallelMode,
  historyFlowItems,
  flowFailureNotice,
  flowStatusNotice,
  flowRoundPartialNotice,
  conditionSnapshot,
  parallelMappingError,
  parallelDialogPreparing,
  parallelPlatformGroups,
  parallelStartBlockedNotice,
  oneClickStartDisabled,
  skipAvailabilityPrecheckForPackageRestore,
  roundConditionLockSummary, roundConditionLocked,
  selectParallelMode,
  openOneClickWithParallel,
  handleUnifiedFilterChange,
  handleParallelPlatformChange,
  confirmParallelOneClick,
  resetFlowNavigationProjection,
  resetWorkflowAfterSuccess,
  abandonRound,
  maybeAutoStartNewRound,
} = flowCoordinator;
const modeWarnings = useModeWarnings(executionSelection, scopePreview);
const searchPackages = useSearchPackages({ keywords, selectedKeywords, customKeyword, cityText, customCity, profileSummary, profileFacts, conditionSnapshot, analysisReady }, { persistDraft: () => { state.saveSearchDraftFor(draftPlatform.value); if (conditionSnapshot.value) parallelFlow.restoreConditionSnapshot(conditionSnapshot.value); }, prepareSnapshot: () => { conditionSnapshot.value = parallelFlow.createConditionSnapshot(); }, restoreDraft: () => state.saveSearchDraftFor(draftPlatform.value), restoreConditions: (snapshot) => { if (snapshot) parallelFlow.restoreConditionSnapshot(snapshot); else parallelFlow.resetConditionState(); }, restoreStep: () => state.navigateStep("upload", { source: "system" }), enterSearchStep: () => { skipAvailabilityPrecheckForPackageRestore(); enterSearchStep(); }, notify }, { profileId: () => props.profileId, roundKey: () => sceneIdentity.value.runEpoch, analysisKey: () => state.resumeAnalysisPhase.value, fileKey: () => selectedFile.value ? `${selectedFile.value.name}:${selectedFile.value.size}:${selectedFile.value.lastModified}` : "" });

// 结果已可渲染的判定在 useDiscoveryState（hasRenderableResult），页面只绑定。
const roundFlow = reactive(useScreenRoundFlow({
  refs: {
    filterValues,
    keywords,
    selectedKeywords,
    cityText,
    profileSummary,
    profileFacts,
    profileConfirmed,
    scrapeTaskId, scrapeBusy, scrapeSnapshot: scrapeSnapshot as unknown as Ref<ApiTaskSnapshot | null>,
    screenTaskId,
    pausedRunId,
    interruptedRunId,
    screenBusy,
    pausingScreen,
    screenSnapshot: screenSnapshot as unknown as Ref<ApiTaskSnapshot | null>,
    recrawlBusy,
    recrawlTaskId,
    recrawlSnapshot: recrawlSnapshot as unknown as Ref<ApiTaskSnapshot | null>,
    profileId: computed(() => props.profileId),
    pollTimer,
    finishedPartial,
    resultsPageSeen,
    navigateStep: (step) => state.navigateStep(step, { source: "system" }), historyRound,
    flowActive: computed(() => parallelFlow.hasUnfinishedRound.value),
    currentRoundStatus,
    resultPlatformFilter,
    uncertainCount: computed(() => groups.value.uncertain.length),
  },
  api: {
    startAiScreen: execution.flowStartAiScreen,
    continueAiScreen: execution.continueAiScreen,
    recrawlUncertain: tasks.recrawlUncertain,
    continueRecrawl: tasks.continueRecrawl,
    finishPausedTask: execution.finishPausedTask,
    resetWorkflow: resetWorkflowAfterSuccess,
    loadLatestResult: results.loadLatestResult, returnToLatest: results.returnToLatest,
    notify: workflow.notify,
  },
}));
attachRoundFlow(deps, roundFlow);
const isNarrowSearchLayout = useNarrowSearchLayout();
watch([searchPanelsOpen, advancedPanelsOpen], ([newS, newA], [oldS, oldA]) => {
  if (isNarrowSearchLayout.value) return;
  if (newS !== oldS) {
    advancedPanelsOpen.value = newS;
  } else if (newA !== oldA) {
    searchPanelsOpen.value = newA;
  }
});
watch(activeStep, (step) => {
  // 026 B078：进当前轮 04 页＝流程结束，置位并持久化已结束事实（唯一判据）。
  // 035：历史轮浏览、从历史「回到最新」过渡期间，以及未结束任务存在时
  //（含刷新恢复把 activeStep 恢复为 results 的路径）一律不得置位。
  // 041：已抓取未筛选轮只是先看一眼结果，流程未结束，刷新后仍回到该现场。
  // 046 v2 状态词表：这里只准问问题 A「此刻有没有活体 worker」（hasLiveTaskState
  // 已拆纯：只认排队/运行，不含暂停与中断）。拿问题 B「本轮未结束」来挡置位，
  // 中断轮的「已看过」就永不置位、loadLatestResult 的闸门把结果永远挡在门外
  //（historyMode / returningFromHistory 是 ref，必须取 .value）。
  if (step === "results" && !historyMode.value && !returningFromHistory.value && !hasLiveTaskState()
      && !isScrapedOnly.value) {
    markResultsPageSeen();
  }
});
watch(
  [
    selectedKeywords,
    cityText,
    () => advancedSettings.value.pages,
    () => draftPlatform.value,
    () => locationDraft.byPlatform,
  ],
  () => { if (!scopeLocked.value) void refreshScopePreview(); },
  { deep: true },
);
watch(
  [draftPlatform, schemaRef],
  () => applyResumeAnalysisToCurrentSchema(),
);
onBeforeUnmount(() => {
  state.sceneSnapshot.value = sceneStore.getCurrent(sceneIdentity.value);
  persistWorkflowState();
  sceneStore.persistToSession(props.profileId);
  if (pollTimer.value) window.clearTimeout(pollTimer.value);
  document.removeEventListener("keydown", handleLifecycleDialogKeydown);
});
watch(profileSummary, () => {
  if (roundFlow.suppressProfileWatch) return;
  profileConfirmed.value = false;
});
watch(activeCategory, (next, prev) => {
  if (prev === "uncertain" && next !== "uncertain") recrawlPlatformGuide.value = null;
});
watch(historyDetail, (detail, prev) => {
  if (detail) {
    enterHistoryRound(detail);
  } else if (prev && historyRound.value) {
    void returnToLatest();
  }
});
defineExpose({ openHistoryDrawer, toggleHistoryDrawer, closeHistoryDrawer });
watch(lifecycleDialogOpen, (open) => {
  if (open) document.addEventListener("keydown", handleLifecycleDialogKeydown);
  else document.removeEventListener("keydown", handleLifecycleDialogKeydown);
});
watch(roundStatusPayload, (payload) => {
  emit("round-status", payload);
});
watch(restoredTaskHint, (value) => {
  if (!value) return;
  const tone: Notice["tone"] =
    value.includes("失败") ? "error"
    : (value.includes("被中断") || value.includes("暂停中")) ? "warning"
    : "info";
  emit("notify", { message: value, tone });
});
</script>
<template>
  <main
    class="view-shell"
    :class="{ 'results-view': activeStep === 'results' }"
    data-testid="discovery-view"
  >
    <!-- 037 复审：restoredTaskHint 恢复提示已融入灵动岛（见 script watch），不再渲染独立 restore-banner 浮窗 -->
    <div
      class="platform-segment"
      role="tablist"
      aria-label="新任务目标平台"
      :data-testid="`platform-current-${parallelMode ? 'all' : viewPlatform}`"
      :data-loaded-schema-platform="schemaRef?.platform || ''"
      :data-loaded-city-platform="cityCatalogRef?.platform || ''"
    >
      <button
        v-for="platform in (['all', 'boss', 'zhilian'] as const)"
        :key="platform"
        type="button"
        role="tab"
        :aria-selected="platform === 'all' ? parallelMode : !parallelMode && viewPlatform === platform"
        :class="['platform-segment-btn', { active: platform === 'all' ? parallelMode : !parallelMode && viewPlatform === platform }]"
        :data-testid="`platform-segment-${platform}`"
        :disabled="scopeLocked && !parallelFlow.hasUnfinishedRound.value"
        :title="scopeLockReason || undefined"
        @click="selectParallelMode(platform)"
      >{{ platform === 'all' ? '全部' : platformLabel(platform) }}</button>
    </div>
    <p v-if="flowStatusNotice" class="mode-warning-inline" data-testid="parallel-flow-error" role="alert" aria-live="polite">
      {{ flowStatusNotice }}
    </p>
    <StepNavigator
      :steps="steps"
      :active-step="activeStep"
       :enabled-steps="enabledSteps"
      :completed-steps="completedSteps"
      @select="selectStep"
    />
    <section class="view-stage">
      <header class="stage-header">
        <div>
          <span class="eyebrow">{{ currentCopy.eyebrow }}</span>
          <h1>{{ currentCopy.title }}</h1>
          <p>{{ currentCopy.description }}</p>
        </div>
        <div v-if="activeStep === 'results'" class="stage-actions">
          <span v-if="historyMode" class="history-round-marker" data-testid="history-round-marker">
            <History :size="17" aria-hidden="true" />历史轮次 · {{ historyStatusText }}
          </span>
          <HistoryRoundProfile v-if="historyMode && !isScrapedOnly" :profile-text="historyProfileText" />
          <button v-if="historyMode && isScrapedOnly" class="button primary" type="button" data-testid="screen-from-history" :disabled="historyScreenBusy" :title="historyScreenBusy ? '正在载入…' : '开始 AI 筛选'" :aria-label="historyScreenBusy ? '正在载入…' : '开始 AI 筛选'" @click="startScreenFromHistory">
            <LoaderCircle v-if="historyScreenBusy" class="spin" :size="16" aria-hidden="true" />
            {{ historyScreenBusy ? "正在载入…" : "开始 AI 筛选" }}
          </button>
          <!-- 窄屏这些按钮只剩 44px 图标：可访问名与图标形状都必须能分辨动作。 -->
          <button v-if="historyMode" class="button secondary" type="button" data-testid="back-to-latest" title="回到最新" aria-label="回到最新" @click="returnToLatest">
            <ArrowLeftToLine :size="17" aria-hidden="true" />回到最新
          </button>
           <button
             class="button secondary"
             type="button"
            data-testid="export-result-csv"
            :disabled="exportBusy || !resultLoaded"
            :title="exportBusy ? '导出中…' : '导出 CSV'"
            :aria-label="exportBusy ? '导出中…' : '导出 CSV'"
            @click="exportResultCsv"
          >
            <LoaderCircle v-if="exportBusy" class="spin" :size="17" aria-hidden="true" />
            <Download v-else :size="17" aria-hidden="true" />
            {{ exportBusy ? "导出中…" : "导出 CSV" }}
          </button>
             <button class="button secondary" type="button" data-testid="start-new-round" :disabled="Boolean(roundFlow.busyAction) || !parallelFlow.canResetNewRound.value || parallelFlow.stale.value" :title="roundFlow.busyAction === 'new-round' ? '重置中…' : parallelFlow.canResetNewRound.value || parallelFlow.stale.value ? '开始新一轮' : parallelFlow.newRoundBlockReason.value" :aria-label="roundFlow.busyAction === 'new-round' ? '重置中…' : '开始新一轮'" @click="roundFlow.confirmNewRound()">
            <LoaderCircle v-if="roundFlow.busyAction === 'new-round'" class="spin" :size="17" aria-hidden="true" />
            <RotateCcw v-else :size="17" aria-hidden="true" />
            {{ roundFlow.busyAction === 'new-round' ? "重置中…" : "开始新一轮" }}
          </button>
        </div>
      </header>
      <section v-show="activeStep === 'upload'" class="content-card workflow-card upload-layout">
        <div class="workflow-copy">
          <span class="card-kicker">简历只会发往你配置的 AI 服务</span>
          <h2>上传后生成建议，不替你做最终决定</h2>
          <p>分析会得到关键词、城市和七类筛选条件。每一项都可以在后续步骤调整。</p>
          <ul class="feature-list">
            <li><Check :size="17" aria-hidden="true" />抓取前确认关键词和城市</li>
            <li><Check :size="17" aria-hidden="true" />筛选前确认七类业务条件</li>
            <li><Check :size="17" aria-hidden="true" />AI 失败进入待确认，不伪装成匹配</li>
          </ul>
        </div>
        <div class="upload-form">
          <label
            class="file-drop"
            :class="{ active: dragActive, chosen: selectedFile }"
            @dragover.prevent="dragActive = true"
            @dragleave.prevent="dragActive = false"
            @drop.prevent="handleNewResumeInput"
          >
            <input
              type="file"
              accept=".txt,.pdf,.docx"
              data-testid="resume-input"
              @change="handleNewResumeInput"
            >
            <UploadCloud :size="30" aria-hidden="true" />
            <strong>{{ selectedFile ? selectedFile.name : "选择或拖入简历" }}</strong>
            <span>TXT / PDF / DOCX，最大尺寸由本地后端校验</span>
          </label>
          <label class="consent-line">
            <input v-model="aiConsent" type="checkbox" data-testid="resume-consent">
            <span>我知悉简历文本会发送到已配置的 AI 服务用于本次分析。</span>
          </label>
           <button
             class="button primary wide-button"
             :class="{ danger: !!resumeError && !uploadBusy }"
             type="button"
            data-testid="analyze-resume"
            :disabled="uploadBusy"
            @click="analyzeResume"
          >
            <LoaderCircle v-if="uploadBusy" class="spin" :size="18" aria-hidden="true" />
            <Sparkles v-else :size="18" aria-hidden="true" />
            {{ uploadBusy ? "分析中…" : resumeError ? "失败，点击重试" : "上传并分析" }}
          </button>
          <div class="upload-shortcuts" data-testid="upload-shortcuts">
             <button
               class="button ghost wide-button"
               type="button"
              @click="analysisReady = true; enterSearchStep()"
            >
              跳过简历，直接手动搜索
            </button>
            <!-- Spec 044 B100：从已保存的常用配置直接进入第二页（不重传简历、不重新分析）。 -->
            <SavedSearchPackagePicker v-bind="searchPackages.pickerProps.value" v-on="searchPackages.pickerEvents" />
          </div>
        </div>
      </section>
      <section v-show="activeStep === 'search'" class="workflow-stack search-layout">
        <CollapsibleCard title="哪些词用于广泛抓取？" v-model="searchPanelsOpen" :actions-in-header="true" :scene-identity="sceneIdentity" scene-card-key="search" :class="{ locked: scopeLocked }">
          <template #prefix>
            <Search :size="17" aria-hidden="true" />
          </template>
          <!-- Spec 044 B100：只在用户点击时保存，任何编辑都不触发保存。 -->
          <template #actions><SavedSearchPackageSaveActions v-bind="searchPackages.saveProps.value" v-on="searchPackages.saveEvents" /></template>
          <template #summary>
            <span v-if="scopeLocked" class="lock-chip" role="status">{{ scrapeBusy || recrawlBusy ? '抓取中 · 范围已锁定' : screenBusy ? '筛选中 · 范围已锁定' : scopeLockReason }}</span>
            <span class="selection-summary">{{ searchSummary }}</span>
          </template>
          <div class="search-columns">
            <div class="search-col">
              <p class="search-col-title">关键词 × 城市</p>
              <div class="chip-grid" aria-label="搜索关键词和城市">
                <span
                  v-for="keyword in keywords"
                  :key="keyword.word"
                  class="keyword-chip"
                  :class="{ selected: selectedKeywords.includes(keyword.word), recommended: keyword.recommended, locked: scopeLocked }"
                >
                   <button
                     class="keyword-chip-label"
                     type="button"
                    data-testid="keyword-chip"
                    :disabled="scopeLocked"
                    :aria-pressed="selectedKeywords.includes(keyword.word)"
                    @click="toggleKeyword(keyword.word)"
                  >
                    {{ keyword.word }}<small v-if="keyword.recommended">推荐</small>
                  </button>
                   <button
                     type="button"
                    class="keyword-chip-remove"
                    data-testid="remove-keyword"
                    :aria-label="'删除关键词 ' + keyword.word"
                    :disabled="scopeLocked"
                    @click="removeKeyword(keyword.word)"
                  >×</button>
                </span>
                <LocationPicker
                  v-for="city in cityList"
                  :key="city"
                  :city="city"
                  :platform="draftPlatform"
                  :scene-identity="sceneIdentity"
                  :model-value="locationDraft.getLocations(draftPlatform, city)"
                  :disabled="scopeLocked"
                  @update:model-value="locationDraft.setLocations(draftPlatform, city, $event)"
                  @remove="removeCity(city)"
                />
              </div>
              <div class="search-input-grid">
              <div class="inline-input-row">
                <label class="field-label grow">
                  <span>关键词</span>
                  <input v-model="customKeyword" data-testid="custom-keyword" type="text" placeholder="回车添加" :disabled="scopeLocked" @keydown.enter.prevent="addCustomKeyword">
                </label>
                <button class="button secondary align-end" data-testid="add-keyword" type="button" :disabled="scopeLocked" @click="addCustomKeyword">添加</button>
              </div>
              <div class="inline-input-row">
                <label class="field-label grow">
                  <span>城市</span>
<input v-model="customCity" data-testid="custom-city" type="text" placeholder="不输入则不指定城市；添加后点击城市按钮可选择区/县" :disabled="scopeLocked" @keydown.enter.prevent="addCustomCity">
                </label>
                <button class="button secondary align-end" data-testid="add-city" type="button" :disabled="scopeLocked" @click="addCustomCity">添加</button>
              </div>
              </div>
            </div>
          </div>
          <label class="field-label">
            <span class="profile-label-row">
              <span>求职画像（用于 AI 精筛）<small v-if="!profileSummary" class="profile-empty-hint">　未填写将跳过精筛</small></span>
               <button
                 type="button"
                class="profile-confirm-btn tip"
                data-testid="profile-confirm"
                :class="{ confirmed: profileConfirmed }"
                :data-tip="'确认后 AI 精筛按当前画像判断，修改画像需重新确认'"
                :aria-pressed="profileConfirmed"
                @click.prevent.stop="confirmProfile"
              >我已确认</button>
            </span>
            <textarea
              v-model="profileSummary"
              ref="profileInputEl"
              rows="4"
              :disabled="scopeLocked"
              class="profile-summary-input"
              :class="{ 'profile-invalid': profileError }"
              :aria-invalid="profileError ? 'true' : undefined"
              placeholder="上传简历后自动生成；也可手动填写，如：3年Python后端，熟悉FastAPI/Redis，期望AI应用开发方向"
              @input="handleProfileInput"
              @blur="handleProfileBlur"
            ></textarea>
            <p v-if="profileError" class="profile-inline-error" data-testid="profile-inline-error" role="status">
              {{ profileError }}
            </p>
          </label>
        </CollapsibleCard>
        <CollapsibleCard class="advanced-panel" title="高级执行设置" v-model="advancedPanelsOpen" :scene-identity="sceneIdentity" scene-card-key="advanced">
          <template #prefix>
            <SlidersHorizontal :size="17" aria-hidden="true" />
          </template>
          <template #actions>
            <button class="button secondary adv-save-btn" type="button" :disabled="advancedBusy" @click="saveAdvancedSettings">
              <LoaderCircle v-if="advancedBusy" class="spin" :size="15" aria-hidden="true" />
              {{ advancedBusy ? "保存中…" : (executionSelection === "custom" ? "保存高级设置" : "保存为自定义档") }}
            </button>
          </template>
        <div class="adv-groups">
          <ExecutionModeSelector
            :model-value="executionSelection"
            :busy="advancedBusy"
            :disabled="!scopePreview"
            @update:model-value="selectExecutionMode"
          />
          <p class="adv-mode-summary" data-testid="adv-mode-summary">
            <span v-if="modeWarnings.length" class="mode-warning-inline" data-testid="mode-warning-inline" role="status">⚠️ {{ modeWarnings.join(" · ") }} ｜</span>
            {{ executionModeSummary }}
          </p>
          <div class="adv-fields">
          <div class="adv-group">
            <p class="adv-group-title">列表抓取</p>
            <div class="advanced-grid">
              <label class="field-label"><span>每组合翻页数 <i class="tip" :data-tip="pagesValue > 10 ? '范围 1~10。每个关键词×城市组合抓多少页。预设档默认：稳定 2 / 平衡 5 / 极限 10，手动修改后保存将进入自定义档。BOSS 最多返回 10 页（300 条），超出可能无新数据' : '范围 1~10。每个关键词×城市组合抓多少页。预设档默认：稳定 2 / 平衡 5 / 极限 10，手动修改后保存将进入自定义档'">?</i></span><input v-model.number="advancedSettings.pages" data-testid="pages-per-combination" type="number" min="1" :disabled="scopeLocked" @change="clampAdvanced('pages')"></label>
              <label class="field-label"><span>组合间延迟（秒） <i class="tip" data-tip="范围由当前模式版本提供。两个搜索组合之间等待多久，实际会±5秒随机抖动">?</i></span><input v-model.number="advancedSettings.inter_combo_delay" type="number" :min="advancedRange('inter_combo_delay')[0]" :max="advancedRange('inter_combo_delay')[1]" :disabled="scopeLocked || executionSelection !== 'custom'" @change="clampAdvanced('inter_combo_delay')"></label>
            </div>
          </div>
          <!-- 详情抓取 -->
          <div class="adv-group">
            <p class="adv-group-title">详情抓取（JD）</p>
            <div class="advanced-grid">
              <label class="field-label"><span>每批抓取数量 <i class="tip" data-tip="范围由当前模式版本提供。每批交给浏览器抓JD的岗位数">?</i></span><input v-model.number="advancedSettings.detail_batch_size" data-testid="detail-batch-size" type="number" :min="advancedRange('detail_batch_size')[0]" :max="advancedRange('detail_batch_size')[1]" :disabled="scopeLocked || executionSelection !== 'custom'" @change="clampAdvanced('detail_batch_size')"></label>
              <label class="field-label"><span>岗位间隔（秒） <i class="tip" data-tip="范围由当前模式版本提供。抓完一个岗位详情后等待再抓下一个">?</i></span><input v-model.number="advancedSettings.detail_interval" type="number" :min="advancedRange('detail_interval')[0]" :max="advancedRange('detail_interval')[1]" :disabled="scopeLocked || executionSelection !== 'custom'" @change="clampAdvanced('detail_interval')"></label>
              <label class="field-label"><span>重置频率 <i class="tip" data-tip="范围由当前模式版本提供。每抓多少个详情后重置会话计数器">?</i></span><input v-model.number="advancedSettings.detail_reset_every" type="number" :min="advancedRange('detail_reset_every')[0]" :max="advancedRange('detail_reset_every')[1]" :disabled="scopeLocked || executionSelection !== 'custom'" @change="clampAdvanced('detail_reset_every')"></label>
              <label class="field-label"><span>批次冷却（秒） <i class="tip" data-tip="范围由当前模式版本提供。两批详情抓取之间的休息时间">?</i></span><input v-model.number="advancedSettings.detail_batch_cooldown" type="number" :min="advancedRange('detail_batch_cooldown')[0]" :max="advancedRange('detail_batch_cooldown')[1]" :disabled="scopeLocked || executionSelection !== 'custom'" @change="clampAdvanced('detail_batch_cooldown')"></label>
              <label class="field-label"><span>并发 Tab 数 <i class="tip" data-tip="范围由当前模式版本提供。同时常驻多少个浏览器 tab 抓 JD（1-10）">?</i></span><input v-model.number="advancedSettings.detail_tab_pool_size" data-testid="detail-tab-pool-size" type="number" :min="advancedRange('detail_tab_pool_size')[0]" :max="advancedRange('detail_tab_pool_size')[1]" :disabled="scopeLocked || executionSelection !== 'custom'" @change="clampAdvanced('detail_tab_pool_size')"></label>
            </div>
          </div>
          <!-- AI 筛选 -->
          <div class="adv-group">
            <p class="adv-group-title">AI 筛选</p>
            <div class="advanced-grid">
              <label class="field-label"><span>粗筛每批数量 <i class="tip" data-tip="范围由当前模式版本提供。粗筛每次发送的岗位摘要数">?</i></span><input v-model.number="advancedSettings.screen_batch_size" type="number" :min="advancedRange('screen_batch_size')[0]" :max="advancedRange('screen_batch_size')[1]" :disabled="scopeLocked || executionSelection !== 'custom'" @change="clampAdvanced('screen_batch_size')"></label>
              <label class="field-label"><span>粗筛并发数 <i class="tip" data-tip="范围由当前模式版本提供。粗筛同时发送的 AI 请求数">?</i></span><input v-model.number="advancedSettings.screen_concurrency" type="number" :min="advancedRange('screen_concurrency')[0]" :max="advancedRange('screen_concurrency')[1]" :disabled="scopeLocked || executionSelection !== 'custom'" @change="clampAdvanced('screen_concurrency')"></label>
              <label class="field-label"><span>精筛每批数量 <i class="tip" data-tip="范围由当前模式版本提供。精筛每次发送的完整 JD 数">?</i></span><input v-model.number="advancedSettings.match_batch_size" type="number" :min="advancedRange('match_batch_size')[0]" :max="advancedRange('match_batch_size')[1]" :disabled="scopeLocked || executionSelection !== 'custom'" @change="clampAdvanced('match_batch_size')"></label>
              <label class="field-label"><span>精筛并发数 <i class="tip" data-tip="范围由当前模式版本提供。精筛同时发送的 AI 请求数">?</i></span><input v-model.number="advancedSettings.match_concurrency" type="number" :min="advancedRange('match_concurrency')[0]" :max="advancedRange('match_concurrency')[1]" :disabled="scopeLocked || executionSelection !== 'custom'" @change="clampAdvanced('match_concurrency')"></label>
            </div>
          </div>
          </div>
          </div>
        </CollapsibleCard>
        <ParallelPlatformProgress v-if="parallelMode && flowPresentation.scrapeItems.value.length" :items="flowPresentation.scrapeItems.value" :busy-platform="parallelFlow.operatingPlatform.value" :stale="parallelFlow.stale.value" :finish-busy="finishSaveBusy" @action="parallelFlow.operateTrack" @finish="finishPausedTask" />
        <TaskProgress v-if="!parallelMode" :snapshot="scrapeSnapshot" kind="scrape" :task-id="scrapeTaskId" :user-finished="finishedPartial" />
        <div
          v-if="loginGuide.visible"
          class="login-guide"
          data-testid="login-guide"
          role="status"
        >
          <p>
            {{ platformLabel(loginGuide.platform) }} 尚未登录：请打开账号
            <strong>{{ loginGuide.accountName || '当前账号' }}</strong> 的
            {{ platformLabel(loginGuide.platform) }} 窗口登录后，再重新开始任务。
          </p>
           <button
             type="button"
            class="button secondary small"
            data-testid="open-accounts-from-guide"
            @click="emit('open-browser-accounts')"
          >
            打开账号面板
          </button>
        </div>
        <div class="workflow-actions">
          <p v-if="draftPlatformDisabled" class="platform-disabled-notice" data-testid="platform-disabled-notice" role="status">
            当前平台（{{ platformLabel(draftPlatform) }}）已禁用新建任务，请切换到可用平台。
          </p>
          <!-- 046 Edge Cases：「全部」门禁覆盖所有平台线，不可用平台在提交前点名；判定与显示名都在 useDiscoveryFlowCoordinator。 -->
          <p v-if="parallelStartBlockedNotice" class="platform-disabled-notice" data-testid="parallel-platform-disabled-notice" role="status">{{ parallelStartBlockedNotice }}</p>
          <button class="button primary one-click-cta" type="button" data-testid="start-one-click" :disabled="oneClickStartDisabled" @click="openOneClickWithParallel">
            <Play :size="20" aria-hidden="true" />开始筛选并 AI 优化
          </button>
          <div class="one-click-secondary-actions">
          <button v-if="scrapeAction.kind === 'none' && !parallelMode" class="button primary" type="button" data-testid="start-scrape" :disabled="draftPlatformDisabled || pipelineBusy" @click="handleStartScrapeClick">
            <Search :size="18" aria-hidden="true" />
            单独抓取
          </button>
          <ScreenRoundActions
            v-if="!parallelMode && (scrapeAction.kind !== 'none' || scrapeCanFinish)"
            :action="scrapeAction"
            :busy="Boolean(scrapeActionBusy)"
            :busy-action="scrapeActionBusy"
            :busy-label="scrapeActionBusy === 'pause-scrape' ? '正在暂停…' : scrapeActionBusy === 'continue-scrape' ? '正在继续…' : ''"
            :finish-busy="finishSaveBusy"
            :show-finish-save="scrapeCanFinish || scrapeAction.kind !== 'none'"
            :show-cancel="scrapeAction.kind !== 'none'"
            cancel-label="放弃本轮"
            :cancel-busy="cancelBusy"
            @pause-scrape="pauseScrape()"
            @continue-scrape="continueScrape()"
            @finish-save="finishPausedTask(pausedRunId || scrapeTaskId)"
            @cancel="abandonRound()"
          />
          <button v-if="scrapeCompleted" class="button secondary" type="button" data-testid="continue-to-screen" @click="enterScreenStep()">
            进行确认AI筛选条件
          </button>
          <button v-if="scrapeCompleted && !resultLoaded && !screenBusy && !hasLiveTaskState()" class="button primary" type="button" data-testid="view-scraped-only" @click="viewScrapedOnly">
            直接查看结果
          </button>
          </div>
        </div>
      </section>
      <section v-show="activeStep === 'screen'" class="workflow-stack">
        <CollapsibleCard title="确认筛选条件" v-model="screenPanelOpen" :scene-identity="sceneIdentity" scene-card-key="screen" data-testid="screen-condition-card" :data-locked="roundConditionLocked ? 'true' : undefined">
          <template #prefix>
            <Filter :size="17" aria-hidden="true" />
          </template>
          <template #summary>
            <!-- 046 D-07：本轮锁定后的汇总读冻结条件那一份事实，判定在 useDiscoveryFlowCoordinator。 -->
            <span v-if="roundConditionLockSummary" class="selection-summary" data-testid="screen-summary-locked">{{ roundConditionLockSummary }}</span>
            <span v-else-if="screenSummaryChips.length" class="summary-chips">
              <span v-for="chip in screenSummaryChips" :key="chip.label" class="summary-chip">{{ chip.label }}: {{ chip.value }}</span>
            </span>
            <span v-else class="selection-summary">未设置筛选条件</span>
          </template>
          <template #actions>
            <div class="workflow-actions screen-card-actions">
              <ScreenRoundActions v-if="!parallelMode"
                :action="withoutRecrawl(roundFlow.screenAction)"
                :busy="Boolean(roundFlow.busyAction)"
                :busy-action="roundFlow.busyAction"
                :busy-label="roundFlow.busyAction === 'pause' ? '正在暂停…' : roundFlow.busyAction === 'continue' ? '正在继续…' : ''"
                :finish-busy="roundFlow.busyAction === 'finish' || finishSaveBusy"
                :disabled="roundFlow.screenAction.kind === 'start' && (draftPlatformDisabled || !scrapeCompleted)"
                :show-finish-save="roundFlow.screenSecondaryActionsVisible"
                :show-cancel="roundFlow.screenSecondaryActionsVisible"
                cancel-label="放弃本轮"
                cancel-test-id="abandon-screen-round"
                :cancel-busy="cancelBusy"
                @pause="roundFlow.pauseScreen()"
                @continue="roundFlow.continueScreen()"
                @start="roundFlow.startScreen()"
                @finish-save="finishScreenSave()"
                @cancel="abandonRound()"
              />
            </div>
          </template>
          <div class="filter-groups"><!-- 046 D-07：本轮锁定（roundConditionLocked）时芯片一律点不动；锁定事实与上面的汇总同源，只有这一份。 -->
            <fieldset v-for="group in filterGroups" :key="group.key" class="filter-group">
              <legend>{{ group.label }}</legend>
              <div class="chip-grid compact">
                 <button
                  v-if="group.sentinel"
                  class="choice-chip"
                   :class="{ selected: !(filterValues[draftPlatform][group.key] || []).length }"
                   type="button"
                  :disabled="Boolean(roundConditionLocked || screenBusy || screenTaskId || pausedRunId || interruptedRunId || finishedPartial)"
                  :aria-pressed="!(filterValues[draftPlatform][group.key] || []).length"
                  @click="filterValues[draftPlatform][group.key] = []"
                >{{ group.sentinel.label }}</button>
                 <button
                  v-for="([label, code]) in group.options"
                  :key="code"
                  class="choice-chip"
                   :class="{ selected: (filterValues[draftPlatform][group.key] || []).includes(code) }"
                   type="button"
                  :disabled="Boolean(roundConditionLocked || screenBusy || screenTaskId || pausedRunId || interruptedRunId || finishedPartial)"
                  :aria-pressed="(filterValues[draftPlatform][group.key] || []).includes(code)"
                  @click="toggleFilter(group.key, code)"
                >{{ label }}</button>
              </div>
            </fieldset>
          </div>
        </CollapsibleCard>
        <ContinuePlatformGuide v-if="!historyMode && roundFlow.continueGuide" :guide="roundFlow.continueGuide" @choose="roundFlow.chooseContinuePlatform" @cancel="roundFlow.cancelContinueGuide" />
        <ParallelPlatformProgress v-if="parallelMode && flowPresentation.screenItems.value.length" :items="flowPresentation.screenItems.value" :busy-platform="parallelFlow.operatingPlatform.value" :stale="parallelFlow.stale.value" :finish-busy="finishSaveBusy" @action="parallelFlow.operateTrack" @finish="finishPausedTask" />
        <TaskProgress v-if="!parallelMode" :snapshot="screenSnapshot" kind="screen" :task-id="screenTaskId" :user-finished="finishedPartial" />
        <ScreenRecrawlProgress v-if="recrawlSnapshot || recrawlBusy" :snapshot="recrawlSnapshot" :task-id="recrawlTaskId" :action="roundFlow.recrawlAction" :busy="Boolean(roundFlow.busyAction)" :busy-action="roundFlow.busyAction" :busy-label="roundFlow.busyAction === 'pause-recrawl' ? '正在暂停重抓…' : ''" :show-finish-save="roundFlow.recrawlAction.kind === 'pause-recrawl' || roundFlow.recrawlAction.kind === 'continue-recrawl'" :show-cancel="roundFlow.recrawlAction.kind === 'pause-recrawl' || roundFlow.recrawlAction.kind === 'continue-recrawl'" :cancel-busy="roundFlow.busyAction === 'cancel-recrawl'" cancel-label="停止详情补抓" cancel-test-id="cancel-recrawl" @pause-recrawl="roundFlow.pauseRecrawl()" @continue-recrawl="roundFlow.continueRecrawl()" @finish-save="roundFlow.finishRecrawl()" @cancel="roundFlow.cancelRecrawl()" />
      </section>
      <section
        v-show="activeStep === 'results'"
        class="results-stage"
        :class="{
          'has-recrawl-guide': Boolean(roundFlow.continueGuide) || (activeCategory === 'uncertain' && recrawlPlatformGuide),
          'has-pending-capsule': !historyMode && resultLoaded && !isScrapedOnly && groups.uncertain.length > 0,
        }"
      >
        <PendingRecrawlCapsule
          v-if="!historyMode && resultLoaded && !isScrapedOnly"
          :count="groups.uncertain.length"
          :busy="recrawlBusy || Boolean(recrawlSnapshot && (recrawlSnapshot.status === 'running' || recrawlSnapshot.status === 'queued'))"
          :dismissed="recrawlCapsuleDismissed"
          :result-epoch="resultEpoch"
          @recrawl="roundFlow.startRecrawl(resultPlatformFilter === 'all' ? undefined : resultPlatformFilter)"
          @dismiss="dismissRecrawlCapsule()"
        />
        <div class="command-band">
          <div v-if="!historyMode && !hasRenderableResult && !resultsBootstrapPending" class="latest-empty" data-testid="latest-result-empty">
            暂无结果：开始新一轮并将最新结果保存后，这里会显示最新轮次。
          </div>
          <div v-if="!historyMode && resultsBootstrapPending && !hasRenderableResult" class="latest-empty" data-testid="latest-result-loading">
            正在恢复上次的结果…
          </div>
          <div class="result-tabs" role="tablist" aria-label="AI 筛选结果分类">
             <button
               v-for="tab in resultTabs"
               :key="tab.id"
               type="button"
              role="tab"
              :aria-selected="activeCategory === tab.id"
              :class="['vtab', `vtab--${tab.id}`, { active: activeCategory === tab.id }]"
              @click="activeCategory = tab.id"
            ><span class="vtab-dot" aria-hidden="true"></span>{{ tab.label }}<span class="vtab-count">{{ tab.count }}</span></button>
          </div>
          <button v-if="!historyMode && isScrapedOnly && resultLoaded" class="button primary" type="button" data-testid="scraped-only-confirm-filters" @click="enterScreenStep()">
            去筛选
          </button>
          <span v-if="!isScrapedOnly" class="command-note" aria-hidden="true">判定依据：你的简历关键词 · 两阶段判断</span>
          <div
            v-if="flowFailureNotice"
            class="flow-failure-notice"
            role="alert"
            aria-live="polite"
            data-testid="flow-failure-notice"
          >{{ flowFailureNotice }}</div>
          <!-- 046 D-08：本轮未结束、部分结果已可查的说明；判定（含历史轮不提示）在 useDiscoveryFlowCoordinator。 -->
          <div v-if="flowRoundPartialNotice" class="flow-round-progress-notice" role="status" aria-live="polite" data-testid="flow-round-progress-notice">{{ flowRoundPartialNotice }}</div>
        </div>
        <ContinuePlatformGuide v-if="!historyMode && roundFlow.continueGuide" :guide="roundFlow.continueGuide" @choose="roundFlow.chooseContinuePlatform" @cancel="roundFlow.cancelContinueGuide" />
        <div v-if="!historyMode && activeCategory === 'uncertain' && recrawlPlatformGuide" class="recrawl-guide" data-testid="recrawl-platform-guide" role="dialog" aria-label="选择重抓平台">
          <p class="recrawl-guide-title">选择要重抓的平台</p>
          <p class="recrawl-guide-counts">BOSS {{ recrawlPlatformGuide.boss }} · 智联 {{ recrawlPlatformGuide.zhilian }}</p>
          <div class="recrawl-guide-actions">
            <button type="button" class="button secondary small" data-testid="recrawl-choose-boss" :disabled="recrawlPlatformGuide.boss === 0" @click="chooseRecrawlPlatform('boss')">重抓 BOSS（{{ recrawlPlatformGuide.boss }}）</button>
            <button type="button" class="button secondary small" data-testid="recrawl-choose-zhilian" :disabled="recrawlPlatformGuide.zhilian === 0" @click="chooseRecrawlPlatform('zhilian')">重抓 智联（{{ recrawlPlatformGuide.zhilian }}）</button>
            <button type="button" class="button danger small" data-testid="recrawl-guide-cancel" @click="recrawlPlatformGuide = null">取消</button>
          </div>
        </div>
        <JobWorkspace
          v-if="!resultsBootstrapPending || hasRenderableResult"
          :jobs="currentJobs"
          :empty-message="currentEmptyMessage"
          :defer-mobile-detail="Boolean(recrawlSnapshot && recrawlSnapshot.status === 'paused')"
          :platform-filter="historyMode ? '' : resultPlatformFilter"
          :result-epoch="resultEpoch"
          :scene-identity="sceneIdentity"
          :scene-mode="historyMode ? 'history' : 'current'"
          :history-run-id="historyRound?.runId || ''"
          @update:platform-filter="onResultPlatformFilterChange"
          @selection-fallback="notify('原选中岗位已不在新结果中，已切换到第一条', 'info')"
        >
          <template #heading-actions>
             <button
               type="button"
              v-if="activeCategory === 'uncertain' && groups.uncertain.length > 0 && !isScrapedOnly && (historyMode || resultLoaded)"
              class="button secondary small pending-recrawl-heading-action"
              data-testid="pending-recrawl-heading"
              :disabled="Boolean(roundFlow.busyAction) || recrawlBusy || Boolean(recrawlSnapshot && (recrawlSnapshot.status === 'running' || recrawlSnapshot.status === 'queued'))"
              @click="roundFlow.startRecrawl(resultPlatformFilter === 'all' ? undefined : resultPlatformFilter)"
            >
              <LoaderCircle v-if="Boolean(roundFlow.busyAction) || recrawlBusy" class="spin" :size="15" aria-hidden="true" />
              <RotateCcw v-else :size="15" aria-hidden="true" />
              全部重抓（{{ groups.uncertain.length }}）
            </button>
          </template>
          <template #actions="{ job }">
            <template v-if="activeCategory !== 'dropped'">
              <button class="button primary" type="button" :disabled="feedbackBusyIds.has(jobId(job))" @click="toggleInterest(job)">
                <LoaderCircle v-if="feedbackBusyIds.has(jobId(job))" class="spin" :size="17" aria-hidden="true" />
                <Bookmark v-else :size="17" aria-hidden="true" />
                {{ feedbackBusyIds.has(jobId(job)) ? "处理中…" : job._marked === "interested" ? "已收藏" : "收藏" }}
              </button>
              <button class="button danger" type="button" :disabled="feedbackBusyIds.has(jobId(job))" @click="toggleRejected(job)">
                <LoaderCircle v-if="feedbackBusyIds.has(jobId(job))" class="spin" :size="17" aria-hidden="true" />
                {{ feedbackBusyIds.has(jobId(job)) ? "处理中…" : (rejectedIds.has(jobId(job)) || job._marked === "rejected") ? "撤销不感兴趣" : "不感兴趣" }}
              </button>
              <button v-if="!historyMode && !isScrapedOnly && !job.jd" class="button secondary" type="button" :disabled="jdBusyIds.has(jobId(job))" @click="retryJd(job)">
                <LoaderCircle v-if="jdBusyIds.has(jobId(job))" class="spin" :size="17" aria-hidden="true" />
                <FileText v-else :size="17" aria-hidden="true" />
                {{ jdBusyIds.has(jobId(job)) ? "补抓中…" : "补抓 JD" }}
              </button>
               <button
                 class="button secondary"
                 type="button"
                data-testid="open-lifecycle-dialog"
                @click="openLifecycleDialog(job)"
              >
                <History :size="17" aria-hidden="true" />查看轨迹
              </button>
            </template>
          </template>
        </JobWorkspace>
      </section>
    </section>
    <Transition name="dialog">
      <div
        v-if="nationalScopeConfirm"
        class="dialog-backdrop"
        data-testid="national-scope-confirm"
        @click.self="cancelNationalScope"
      >
        <section class="dialog-panel national-scope-dialog" role="dialog" aria-modal="true" aria-label="未填写城市">
          <h2>未填写城市</h2>
          <p>未填写城市，将按全国范围抓取，是否继续？</p>
          <div class="dialog-actions">
            <button class="button secondary" type="button" data-testid="cancel-national-scope" @click="cancelNationalScope">取消</button>
            <button class="button primary" type="button" data-testid="confirm-national-scope" @click="confirmNationalScope">继续按全国抓取</button>
          </div>
        </section>
      </div>
    </Transition>
    <Transition name="dialog">
      <div
        v-if="pendingPlatformSwitch"
        class="dialog-backdrop"
        data-testid="platform-switch-confirm"
        @click.self="cancelPlatformSwitch"
      >
        <section class="dialog-panel platform-switch-dialog" role="dialog" aria-modal="true" aria-label="确认切换平台">
          <h2>确认切换平台</h2>
          <p>上一轮任务还未进行 AI 筛选。切换平台后，该轮抓取结果不会保存，是否继续切换？</p>
          <div class="dialog-actions">
            <button class="button secondary" type="button" data-testid="cancel-platform-switch" @click="cancelPlatformSwitch">继续留在当前平台</button>
            <button class="button danger" type="button" data-testid="confirm-platform-switch" @click="confirmPlatformSwitch">仍然切换</button>
          </div>
        </section>
      </div>
    </Transition>
    <ResultHistoryDrawer
      :open="historyOpen"
      :items="historyItems" :flow-items="historyFlowItems"
      :detail="historyDetail"
      :loading="historyLoading"
      :error="historyError"
      :deleting="historyDeleting"
      :delete-target="historyDeleteTarget"
      @close="hideHistory"
      @open-round="openHistoryRound"
      @confirm-delete="confirmHistoryDelete"
      @cancel-delete="cancelHistoryDelete"
      @delete-round="deleteHistoryRound"
      @view-log="openHistoryLog"
    />
    <LogViewerDialog
      :open="historyLogOpen"
      :initial-task-id="historyLogTaskId"
      :profile-id="profileId"
      @close="historyLogOpen = false"
    />
    <JobLifecycleDialog
      :open="lifecycleDialogOpen"
      :profile-id="profileId"
      :job="lifecycleDialogJob"
      @close="closeLifecycleDialog"
      @job-feedback-changed="onJobFeedbackChanged"
    />
    <OneClickScreenDialog
      :open="oneClickOpen" :platform="draftPlatform" :groups="oneClickGroups"
      v-model="filterValues[draftPlatform]" :has-old-result="hasOldResult"
      :mode="parallelMode ? 'all' : 'single'" :platforms="['boss', 'zhilian']"
      :platform-groups="parallelPlatformGroups" :platform-model-values="parallelFlow.platformValues"
      :unified-values="parallelFlow.unifiedValues" :confirm-disabled="Boolean(parallelMappingError) || parallelFlow.stale.value"
      :preparing="parallelDialogPreparing" :loading="parallelFlow.loading.value"
      :error-message="parallelMappingError" @unified-change="handleUnifiedFilterChange"
      @platform-change="handleParallelPlatformChange" @close="oneClickOpen = false"
      @confirm="confirmOneClick" @parallel-confirm="confirmParallelOneClick"
    />
    <!-- 025 B076：批中二选一弹窗（暂停：立即停止 / 等这批抓完；结束保存：等这批再保存 / 立即保存） -->
    <PauseBatchChoiceDialog
      :open="roundFlow.pauseDialogOpen"
      :batch-info="roundFlow.pauseBatchInfo"
      :kind="roundFlow.pauseDialogKind"
      @close="roundFlow.closePauseDialog()"
      @choose="roundFlow.confirmPauseChoice"
    />
  </main>
</template>
<style scoped src="./DiscoveryView.css"></style>
