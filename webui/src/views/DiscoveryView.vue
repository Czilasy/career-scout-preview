<script setup lang="ts">
// 021 B8 T027：视图壳——状态/动作外迁 composables，模板零改动。
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, type Ref } from "vue";
import {
  Bookmark, Check, Download, FileText, Filter, History, LoaderCircle, Play,
  RotateCcw, Search, SlidersHorizontal, Sparkles, Square, UploadCloud, X,
} from "@lucide/vue";
import CollapsibleCard from "../components/CollapsibleCard.vue";
import ExecutionModeSelector from "../components/ExecutionModeSelector.vue";
import ModeWarningBanner from "../components/ModeWarningBanner.vue";
import JobLifecycleActions from "../components/JobLifecycleActions.vue";
import JobWorkspace from "../components/JobWorkspace.vue";
import LocationPicker from "../components/LocationPicker.vue";
import HistoryRoundProfile from "../components/HistoryRoundProfile.vue";
import ResultHistoryDrawer from "../components/ResultHistoryDrawer.vue";
import OneClickScreenDialog, {
  type OneClickFilterGroup,
  crossPlatformDedupeEnabled,
} from "../components/OneClickScreenDialog.vue";
import StepNavigator from "../components/StepNavigator.vue";
import ContinuePlatformGuide from "../components/ContinuePlatformGuide.vue";
import ScreenRoundActions from "../components/ScreenRoundActions.vue";
import ScreenRecrawlProgress from "../components/ScreenRecrawlProgress.vue";
import PendingRecrawlCapsule from "../components/PendingRecrawlCapsule.vue";
import TaskProgress from "../components/TaskProgress.vue";
import { useScreenRoundFlow } from "../composables/useScreenRoundFlow";
import { withoutRecrawl } from "../screenFlow";
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
import { useDiscoveryState } from "../composables/useDiscoveryState";
import { useDiscoveryWorkflow } from "../composables/useDiscoveryWorkflow";
import { useDiscoverySearch } from "../composables/useDiscoverySearch";
import { useDiscoveryExecution } from "../composables/useDiscoveryExecution";
import { useDiscoveryTasks } from "../composables/useDiscoveryTasks";
import { useDiscoveryResults } from "../composables/useDiscoveryResults";

type StepId = "upload" | "search" | "screen" | "results";
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

interface TaskSnapshot {
  status: "running" | "done" | "failed" | "paused" | "cancelled" | string;
  progress?: Record<string, unknown>;
  logs?: string[];
  error?: string;
  result?: PipelineResult;
  started_at?: number;
  finished_at?: number;
  // 切片7：统一状态接口字段（FR-037/SC-006）
  stage?: string;
  success_count?: number;
  fail_count?: number;
  unstarted_count?: number;
  total?: number;
  kept_count?: number;
  dropped_count?: number;
  pending_count?: number;
  source_total?: number;
  pause_info?: { error_code?: string; error_reason?: string } | null;
  execution_config?: Record<string, unknown> | null;
  scraped_count?: number;
  // T510：任务自身平台，供 TaskProgress 展示真实平台徽章（http-api.md L201）
  platform?: Platform;
  /** 一键链路标记：抓取任务完成后前端自动接续 AI 筛选。 */
  auto_screen?: boolean;
}


const props = defineProps<{ profileId: string }>();
const emit = defineEmits<{
  notify: [notice: Notice];
  "profile-created": [profile: CandidateProfile];
  // Task 009：详情生命周期 action 成功后上抛，App 刷新当前 profile 提醒。
  "job-feedback-changed": [payload: { profileId: string; jobId: string }];
  // 顶栏本轮状态胶囊：纯展示数据，空闲时上抛 null（不新增任何请求）。
  "round-status": [payload: RoundStatusPayload | null];
  // D7：岗位发现流程检测未登录，引导用户去账号面板打开浏览器窗口登录。
  "open-browser-accounts": [];
}>();

const state = useDiscoveryState(props, emit);
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
  scrapeBusy,
  scrapeSnapshot,
  screenBusy,
  pausingScreen,
  switchAccounts,
  switchAccountId,
  screenSnapshot,
  screenTaskId,
  recrawlBusy,
  recrawlTaskId,
  recrawlSnapshot,
  recrawlRetryCount,
  scrapeCompleted,
  resultLoaded,
  finishedPartial,
  recrawlPlatformGuide,
  exportBusy,
  finishSaveBusy,
  cancelBusy,
  historyScreenBusy,
  restoredTaskHint,
  pausedRunId,
  interruptedRunId,
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
  oneClickDisabled,
  searchPanelsOpen,
  pollTimer,
  scopeLocked,
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
  historyBackToLatest,
  confirmHistoryDelete,
  cancelHistoryDelete,
  deleteHistoryRound,
  archiveHistoryLatest,
} = state;

// 共享依赖容器：跨域函数经 deps 调用时解析（roundFlow 最后回填）。
const shared: Record<string, unknown> = {
  emit,
  props,
};
const workflow = useDiscoveryWorkflow(state, shared);
const search = useDiscoverySearch(state, shared);
const execution = useDiscoveryExecution(state, shared);
const tasks = useDiscoveryTasks(state, shared);
const results = useDiscoveryResults(state, shared);

// 跨域函数接线（composable 内经 deps.X 调用时解析，此处赋值生效）
shared.notify = workflow.notify;
shared.enterSearchStep = workflow.enterSearchStep;
shared.enterScreenStep = workflow.enterScreenStep;
shared.clearWorkflowState = workflow.clearWorkflowState;
shared.startScrape = execution.startScrape;
shared.openOneClickDialog = execution.openOneClickDialog;
shared.restoreRunningTask = execution.restoreRunningTask;
shared.finishPausedTask = execution.finishPausedTask;
shared.continueAiScreen = execution.continueAiScreen;
shared.cancelScrape = execution.cancelScrape;
shared.startAiScreen = execution.startAiScreen;
shared.cancelActiveTasksForNewRound = tasks.cancelActiveTasksForNewRound;
shared.clearLatestResult = results.clearLatestResult;
shared.loadLatestResult = results.loadLatestResult;
shared.setPipelineResult = results.setPipelineResult;
shared.fetchMergedLatestResult = results.fetchMergedLatestResult;
shared.returnToLatest = results.returnToLatest;
shared.restoreLocationsFromContext = results.restoreLocationsFromContext;
shared.jobId = results.jobId;
shared.setDraftPlatform = search.setDraftPlatform;
shared.loadCityCatalog = search.loadCityCatalog;
shared.loadFilterLabels = search.loadFilterLabels;
shared.refreshScopePreview = search.refreshScopePreview;
shared.requireProfileConfirmed = search.requireProfileConfirmed;
shared.validateProfileForScreen = search.validateProfileForScreen;
shared.showLoginGuide = search.showLoginGuide;
shared.isLoginErrorCode = search.isLoginErrorCode;
shared.mergeRecrawlUpdates = tasks.mergeRecrawlUpdates;
shared.pollRecrawl = tasks.pollRecrawl;
shared.pollTask = tasks.pollTask;
shared.saveScrapedOnlySnapshot = tasks.saveScrapedOnlySnapshot;
shared.enrichPausedSnapshot = tasks.enrichPausedSnapshot;
shared.isCompletedTaskStatus = tasks.isCompletedTaskStatus;

// 024：档位/规模风险警示（黄色警示区数据源）。
// 极限警告：仅极限档；大任务警告：任何档位，按新口径总页数 >30（scopePreview 未确认时为 false）。
const extremeWarning = computed(() => executionSelection.value === "extreme");
const largeTaskWarning = computed(() => Boolean(
  scopePreview.value?.planned_pages && scopePreview.value.planned_pages > 30,
));

// 模板绑定：composable 返回值解构
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

const roundFlow = reactive(useScreenRoundFlow({
  refs: {
    filterValues,
    keywords,
    selectedKeywords,
    cityText,
    profileSummary,
    profileFacts,
    profileConfirmed,
    scrapeTaskId,
    screenTaskId,
    pausedRunId,
    interruptedRunId,
    screenBusy,
    pausingScreen,
    screenSnapshot: screenSnapshot as unknown as Ref<ApiTaskSnapshot | null>,
    recrawlBusy,
    recrawlTaskId,
    recrawlSnapshot: recrawlSnapshot as unknown as Ref<ApiTaskSnapshot | null>,
    finishedPartial,
    activeStep,
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
    resetWorkflow: tasks.resetWorkflow,
    loadLatestResult: results.loadLatestResult,
    notify: workflow.notify,
  },
}));
shared.roundFlow = roundFlow;

watch(activeStep, (step) => {
  if (step === "results") {
    resultsPageSeen.value = true;
    clearWorkflowState();
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

watch(() => props.profileId, () => {
  if (!pausedRunId.value && !scrapeBusy.value && !screenBusy.value && !recrawlBusy.value) {
    void loadLatestResult();
  }
});

onBeforeUnmount(() => {
  persistWorkflowState();
  if (pollTimer.value) window.clearTimeout(pollTimer.value);
  document.removeEventListener("keydown", handleLifecycleDialogKeydown);
});

watch(profileSummary, () => {
  if (roundFlow.suppressProfileWatch) return;
  profileConfirmed.value = false;
});

watch(
  () => scrapeSnapshot.value?.status === "paused" && Boolean(scrapeTaskId.value),
  (visible) => { if (visible) void loadSwitchAccounts(); },
);

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

onMounted(() => {
  restoreWorkflowState();
  void loadAdvancedSettings();
  void loadFilterLabels();
  void loadCityCatalog();
  void restoreRunningTask().finally(() => {
    restoreSaved02State();
    if (!unfinishedWorkflowRestored.value && !activeTaskRestored.value
      && !scrapeBusy.value && !screenBusy.value && !recrawlBusy.value) {
      void loadLatestResult();
    }
  });
});
</script>
<template>
  <main
    class="view-shell"
    :class="{ 'results-view': activeStep === 'results' }"
    data-testid="discovery-view"
  >
    <div v-if="restoredTaskHint" class="restore-banner" role="status">
      <LoaderCircle :size="16" class="spin" aria-hidden="true" />
      <span>{{ restoredTaskHint }}</span>
      <button type="button" class="restore-close" aria-label="关闭提示" @click="restoredTaskHint = ''">×</button>
    </div>
    <div
      class="platform-segment"
      role="tablist"
      aria-label="新任务目标平台"
      :data-testid="`platform-current-${draftPlatform}`"
      :data-loaded-schema-platform="schemaRef?.platform || ''"
      :data-loaded-city-platform="cityCatalogRef?.platform || ''"
    >
      <button
        v-for="platform in (['boss', 'zhilian'] as const)"
        :key="platform"
        type="button"
        role="tab"
        :aria-selected="draftPlatform === platform"
        :class="['platform-segment-btn', { active: draftPlatform === platform }]"
        :data-testid="`platform-segment-${platform}`"
        :disabled="scopeLocked"
        :title="scopeLocked ? '任务进行中，平台已锁定' : undefined"
        @click="requestDraftPlatform(platform)"
      >{{ platform === 'boss' ? 'BOSS' : '智联' }}</button>
    </div>
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
          <button v-if="historyMode && isScrapedOnly" class="button primary" type="button" data-testid="screen-from-history" :disabled="historyScreenBusy" @click="startScreenFromHistory">
            <LoaderCircle v-if="historyScreenBusy" class="spin" :size="16" aria-hidden="true" />
            {{ historyScreenBusy ? "正在载入…" : "开始 AI 筛选" }}
          </button>
          <button v-if="historyMode" class="button secondary" type="button" data-testid="back-to-latest" @click="returnToLatest">
            <RotateCcw :size="17" aria-hidden="true" />回到最新
          </button>
          <button
            class="button secondary"
            type="button"
            data-testid="export-result-csv"
            :disabled="exportBusy || !resultLoaded"
            @click="exportResultCsv"
          >
            <LoaderCircle v-if="exportBusy" class="spin" :size="17" aria-hidden="true" />
            <Download v-else :size="17" aria-hidden="true" />
            {{ exportBusy ? "导出中…" : "导出 CSV" }}
          </button>
          <button class="button secondary" type="button" :disabled="Boolean(roundFlow.busyAction)" @click="roundFlow.confirmNewRound()">
            <LoaderCircle v-if="roundFlow.busyAction === 'new-round'" class="spin" :size="17" aria-hidden="true" />
            <RotateCcw v-else :size="17" aria-hidden="true" />
            {{ roundFlow.busyAction === 'new-round' ? "重置中…" : "开始新一轮" }}
          </button>
        </div>
      </header>

      <section v-if="activeStep === 'upload'" class="content-card workflow-card upload-layout">
        <div class="workflow-copy">
          <span class="card-kicker">简历只会发往你配置的 AI 服务</span>
          <h2>上传后生成建议，不替你做最终决定</h2>
          <p>分析会得到关键词、城市和六类筛选条件。每一项都可以在后续步骤调整。</p>
          <ul class="feature-list">
            <li><Check :size="17" aria-hidden="true" />抓取前确认关键词和城市</li>
            <li><Check :size="17" aria-hidden="true" />筛选前确认六类业务条件</li>
            <li><Check :size="17" aria-hidden="true" />AI 失败进入待确认，不伪装成匹配</li>
          </ul>
        </div>

        <div class="upload-form">
          <label
            class="file-drop"
            :class="{ active: dragActive, chosen: selectedFile }"
            @dragover.prevent="dragActive = true"
            @dragleave.prevent="dragActive = false"
            @drop.prevent="handleDrop"
          >
            <input
              type="file"
              accept=".txt,.pdf,.docx"
              data-testid="resume-input"
              @change="chooseFile"
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
          <button
            class="button ghost wide-button"
            type="button"
            @click="analysisReady = true; enterSearchStep()"
          >
            跳过简历，直接手动搜索
          </button>
        </div>
      </section>

      <section v-else-if="activeStep === 'search'" class="workflow-stack search-layout">
        <CollapsibleCard title="哪些词用于广泛抓取？" v-model="searchPanelsOpen" :class="{ locked: scopeLocked }">
          <template #prefix>
            <Search :size="17" aria-hidden="true" />
          </template>
          <template #summary>
            <span v-if="scopeLocked" class="lock-chip" role="status">{{ scrapeBusy || recrawlBusy ? '抓取中 · 范围已锁定' : screenBusy ? '筛选中 · 范围已锁定' : '范围已锁定' }}</span>
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

        <CollapsibleCard class="advanced-panel" title="高级执行设置" v-model="searchPanelsOpen">
          <template #prefix>
            <SlidersHorizontal :size="17" aria-hidden="true" />
          </template>
          <template #actions>
            <button class="button secondary adv-save-btn" type="button" :disabled="advancedBusy" @click="saveAdvancedSettings">
              <LoaderCircle v-if="advancedBusy" class="spin" :size="15" aria-hidden="true" />
              {{ advancedBusy ? "保存中…" : "保存高级设置" }}
            </button>
          </template>
          <div class="adv-groups">
          <ExecutionModeSelector
            :model-value="executionSelection"
            :busy="advancedBusy"
            :disabled="!scopePreview"
            @update:model-value="selectExecutionMode"
          />
          <ModeWarningBanner
            :extreme-warning="extremeWarning"
            :large-task-warning="largeTaskWarning"
          />
          <p class="adv-mode-summary" data-testid="adv-mode-summary">{{ executionModeSummary }}</p>
          <div class="adv-fields">
          <div class="adv-group">
            <p class="adv-group-title">列表抓取</p>
            <div class="advanced-grid">
              <label class="field-label"><span>每组合翻页数 <i class="tip" :data-tip="pagesValue > 10 ? '范围 1~10。每个关键词×城市组合抓多少页，BOSS 最多返回 10 页（300 条），超出可能无新数据' : '范围 1~10。每个关键词×城市组合抓多少页'">?</i></span><input v-model.number="advancedSettings.pages" data-testid="pages-per-combination" type="number" min="1" :disabled="scopeLocked" @change="clampAdvanced('pages')"></label>
              <label class="field-label"><span>组合间延迟（秒） <i class="tip" data-tip="范围由当前模式版本提供。两个搜索组合之间等待多久，实际会±5秒随机抖动">?</i></span><input v-model.number="advancedSettings.inter_combo_delay" type="number" :min="advancedRange('inter_combo_delay')[0]" :max="advancedRange('inter_combo_delay')[1]" @change="clampAdvanced('inter_combo_delay')"></label>
            </div>
          </div>
          <!-- 详情抓取 -->
          <div class="adv-group">
            <p class="adv-group-title">详情抓取（JD）</p>
            <div class="advanced-grid">
              <label class="field-label"><span>每批抓取数量 <i class="tip" data-tip="范围由当前模式版本提供。每批交给浏览器抓JD的岗位数">?</i></span><input v-model.number="advancedSettings.detail_batch_size" data-testid="detail-batch-size" type="number" :min="advancedRange('detail_batch_size')[0]" :max="advancedRange('detail_batch_size')[1]" @change="clampAdvanced('detail_batch_size')"></label>
              <label class="field-label"><span>岗位间隔（秒） <i class="tip" data-tip="范围由当前模式版本提供。抓完一个岗位详情后等待再抓下一个">?</i></span><input v-model.number="advancedSettings.detail_interval" type="number" :min="advancedRange('detail_interval')[0]" :max="advancedRange('detail_interval')[1]" @change="clampAdvanced('detail_interval')"></label>
              <label class="field-label"><span>重置频率 <i class="tip" data-tip="范围由当前模式版本提供。每抓多少个详情后重置会话计数器">?</i></span><input v-model.number="advancedSettings.detail_reset_every" type="number" :min="advancedRange('detail_reset_every')[0]" :max="advancedRange('detail_reset_every')[1]" @change="clampAdvanced('detail_reset_every')"></label>
              <label class="field-label"><span>批次冷却（秒） <i class="tip" data-tip="范围由当前模式版本提供。两批详情抓取之间的休息时间">?</i></span><input v-model.number="advancedSettings.detail_batch_cooldown" type="number" :min="advancedRange('detail_batch_cooldown')[0]" :max="advancedRange('detail_batch_cooldown')[1]" @change="clampAdvanced('detail_batch_cooldown')"></label>
              <label class="field-label"><span>并发 Tab 数 <i class="tip" data-tip="范围由当前模式版本提供。同时常驻多少个浏览器 tab 抓 JD（1-10）">?</i></span><input v-model.number="advancedSettings.detail_tab_pool_size" data-testid="detail-tab-pool-size" type="number" :min="advancedRange('detail_tab_pool_size')[0]" :max="advancedRange('detail_tab_pool_size')[1]" @change="clampAdvanced('detail_tab_pool_size')"></label>
            </div>
          </div>
          <!-- AI 筛选 -->
          <div class="adv-group">
            <p class="adv-group-title">AI 筛选</p>
            <div class="advanced-grid">
              <label class="field-label"><span>粗筛每批数量 <i class="tip" data-tip="范围由当前模式版本提供。粗筛每次发送的岗位摘要数">?</i></span><input v-model.number="advancedSettings.screen_batch_size" type="number" :min="advancedRange('screen_batch_size')[0]" :max="advancedRange('screen_batch_size')[1]" @change="clampAdvanced('screen_batch_size')"></label>
              <label class="field-label"><span>粗筛并发数 <i class="tip" data-tip="范围由当前模式版本提供。粗筛同时发送的 AI 请求数">?</i></span><input v-model.number="advancedSettings.screen_concurrency" type="number" :min="advancedRange('screen_concurrency')[0]" :max="advancedRange('screen_concurrency')[1]" @change="clampAdvanced('screen_concurrency')"></label>
              <label class="field-label"><span>精筛每批数量 <i class="tip" data-tip="范围由当前模式版本提供。精筛每次发送的完整 JD 数">?</i></span><input v-model.number="advancedSettings.match_batch_size" type="number" :min="advancedRange('match_batch_size')[0]" :max="advancedRange('match_batch_size')[1]" @change="clampAdvanced('match_batch_size')"></label>
              <label class="field-label"><span>精筛并发数 <i class="tip" data-tip="范围由当前模式版本提供。精筛同时发送的 AI 请求数">?</i></span><input v-model.number="advancedSettings.match_concurrency" type="number" :min="advancedRange('match_concurrency')[0]" :max="advancedRange('match_concurrency')[1]" @change="clampAdvanced('match_concurrency')"></label>
            </div>
          </div>
          </div>
          </div>
        </CollapsibleCard>

        <TaskProgress :snapshot="scrapeSnapshot" kind="scrape" :task-id="scrapeTaskId" />
        <div
          v-if="loginGuide.visible"
          class="login-guide"
          data-testid="login-guide"
          role="status"
        >
          <p>
            {{ loginGuide.platform === 'boss' ? 'BOSS' : '智联' }} 尚未登录：请打开账号
            <strong>{{ loginGuide.accountName || '当前账号' }}</strong> 的
            {{ loginGuide.platform === 'boss' ? 'BOSS' : '智联' }} 窗口登录后，再重新开始任务。
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
            当前平台（{{ draftPlatform === 'boss' ? 'BOSS' : '智联' }}）已禁用新建任务，请切换到可用平台。
          </p>
          <button class="button primary one-click-cta" type="button" data-testid="start-one-click" :disabled="oneClickDisabled" @click="openOneClick">
            <Play :size="20" aria-hidden="true" />开始筛选并 AI 优化
          </button>
          <div class="one-click-secondary-actions">
          <button class="button primary" type="button" data-testid="start-scrape" :disabled="draftPlatformDisabled || pipelineBusy" @click="handleStartScrapeClick">
            <Search v-if="!scrapeBusy && !cancelBusy" :size="18" aria-hidden="true" />
            <LoaderCircle v-else-if="cancelBusy" class="spin" :size="18" aria-hidden="true" />
            <Square v-else :size="18" aria-hidden="true" />
            {{ cancelBusy ? "正在停止…" : scrapeBusy ? "停止抓取" : "单独抓取" }}
          </button>
          <label v-if="scrapeSnapshot && scrapeSnapshot.status === 'paused' && scrapeTaskId"
                 class="resume-account-picker" data-testid="scrape-continue-account-picker"
                 style="display:inline-flex;align-items:center;gap:6px">
            <span>继续账号</span>
            <select v-model="switchAccountId" data-testid="scrape-continue-account"
                    :disabled="scrapeBusy || switchAccounts.length <= 1"
                    style="min-width:120px">
              <option value="">使用原账号</option>
              <option v-for="acc in switchAccounts" :key="acc.id" :value="acc.id">{{ acc.name }}</option>
            </select>
          </label>
          <button v-if="scrapeSnapshot && scrapeSnapshot.status === 'paused' && scrapeTaskId"
                  class="button secondary" type="button" data-testid="continue-scrape"
                  :disabled="scrapeBusy || finishSaveBusy || cancelBusy" @click="continueScrape(switchAccountId || undefined)">
            <LoaderCircle v-if="scrapeBusy" class="spin" :size="15" aria-hidden="true" />
            {{ scrapeBusy ? "正在继续…" : "从断点继续" }}
          </button>
          <button v-if="scrapeSnapshot && scrapeSnapshot.status === 'paused' && pausedRunId"
                  class="button danger" type="button" data-testid="cancel-paused-scrape"
                  :disabled="cancelBusy || finishSaveBusy"
                  @click="cancelPausedTask(pausedRunId)">
            <LoaderCircle v-if="cancelBusy" class="spin" :size="15" aria-hidden="true" />
            {{ cancelBusy ? "取消中…" : "取消任务" }}
          </button>
          <button v-if="scrapeSnapshot && scrapeSnapshot.status === 'paused' && (pausedRunId || scrapeTaskId)"
                  class="button danger" type="button" data-testid="finish-paused-scrape"
                  :disabled="finishSaveBusy || cancelBusy"
                  @click="finishPausedTask(pausedRunId || scrapeTaskId)">
            <LoaderCircle v-if="finishSaveBusy" class="spin" :size="15" aria-hidden="true" />
            {{ finishSaveBusy ? "正在保存…" : "结束并保存结果" }}
          </button>
          <button v-if="scrapeSnapshot && (scrapeSnapshot.status === 'failed' || scrapeSnapshot.status === 'running') && scrapeTaskId"
                  class="button danger" type="button" data-testid="finish-active-scrape"
                  :disabled="finishSaveBusy || cancelBusy"
                  @click="finishPausedTask(scrapeTaskId)">
            <LoaderCircle v-if="finishSaveBusy" class="spin" :size="15" aria-hidden="true" />
            {{ finishSaveBusy ? "正在保存…" : "结束并保存结果" }}
          </button>
          <button v-if="interruptedRunId" class="button danger" type="button" data-testid="finish-interrupted-scrape" :disabled="finishSaveBusy || cancelBusy" @click="finishPausedTask(interruptedRunId)">
            <LoaderCircle v-if="finishSaveBusy" class="spin" :size="15" aria-hidden="true" />
            {{ finishSaveBusy ? "正在保存…" : "结束并保存结果" }}
          </button>
          <button v-if="scrapeCompleted" class="button secondary" type="button" data-testid="continue-to-screen" @click="enterScreenStep()">
            进行确认AI筛选条件
          </button>
          <button v-if="scrapeCompleted && !resultLoaded && !screenBusy" class="button primary" type="button" data-testid="view-scraped-only" @click="viewScrapedOnly">
            直接查看结果
          </button>
          </div>
        </div>
      </section>

      <section v-else-if="activeStep === 'screen'" class="workflow-stack">
        <CollapsibleCard title="确认 6 类筛选条件" v-model="screenPanelOpen">
          <template #prefix>
            <Filter :size="17" aria-hidden="true" />
          </template>
          <template #summary>
            <span v-if="screenSummaryChips.length" class="summary-chips">
              <span v-for="chip in screenSummaryChips" :key="chip.label" class="summary-chip">{{ chip.label }}: {{ chip.value }}</span>
            </span>
            <span v-else class="selection-summary">未设置筛选条件</span>
          </template>
          <template #actions>
            <div class="workflow-actions screen-card-actions">
              <ScreenRoundActions
                :action="withoutRecrawl(roundFlow.screenAction)"
                :busy="Boolean(roundFlow.busyAction)"
                :busy-action="roundFlow.busyAction"
                :busy-label="roundFlow.busyAction === 'pause' ? '正在暂停…' : roundFlow.busyAction === 'continue' ? '正在继续…' : ''"
                :finish-busy="roundFlow.busyAction === 'finish' || finishSaveBusy"
                :disabled="roundFlow.screenAction.kind === 'start' && (draftPlatformDisabled || !scrapeCompleted)"
                :show-finish-save="roundFlow.screenAction.kind === 'pause' || roundFlow.screenAction.kind === 'continue'"
                @pause="roundFlow.pauseScreen()"
                @continue="roundFlow.continueScreen()"
                @start="roundFlow.startScreen()"
                @finish-save="finishScreenSave()"
              />
            </div>
          </template>
          <div class="filter-groups">
            <fieldset v-for="group in filterGroups" :key="group.key" class="filter-group">
              <legend>{{ group.label }}</legend>
              <div class="chip-grid compact">
                <button
                  v-if="group.sentinel"
                  class="choice-chip"
                  :class="{ selected: !(filterValues[draftPlatform][group.key] || []).length }"
                  type="button"
                  :disabled="Boolean(screenBusy || screenTaskId || pausedRunId || interruptedRunId || finishedPartial)"
                  :aria-pressed="!(filterValues[draftPlatform][group.key] || []).length"
                  @click="filterValues[draftPlatform][group.key] = []"
                >{{ group.sentinel.label }}</button>
                <button
                  v-for="([label, code]) in group.options"
                  :key="code"
                  class="choice-chip"
                  :class="{ selected: (filterValues[draftPlatform][group.key] || []).includes(code) }"
                  type="button"
                  :disabled="Boolean(screenBusy || screenTaskId || pausedRunId || interruptedRunId || finishedPartial)"
                  :aria-pressed="(filterValues[draftPlatform][group.key] || []).includes(code)"
                  @click="toggleFilter(group.key, code)"
                >{{ label }}</button>
              </div>
            </fieldset>
          </div>
        </CollapsibleCard>

        <ContinuePlatformGuide v-if="!historyMode && roundFlow.continueGuide" :guide="roundFlow.continueGuide" @choose="roundFlow.chooseContinuePlatform" @cancel="roundFlow.cancelContinueGuide" />

        <TaskProgress :snapshot="screenSnapshot" kind="screen" :task-id="screenTaskId" />
        <ScreenRecrawlProgress v-if="recrawlSnapshot || recrawlBusy" :snapshot="recrawlSnapshot" :task-id="recrawlTaskId" :action="roundFlow.recrawlAction" :busy="Boolean(roundFlow.busyAction)" :busy-action="roundFlow.busyAction" :busy-label="roundFlow.busyAction === 'pause-recrawl' ? '正在暂停重抓…' : ''" :show-finish-save="roundFlow.recrawlAction.kind === 'pause-recrawl' || (roundFlow.recrawlAction.kind === 'continue-recrawl' && roundFlow.recrawlStatus !== 'paused')" @pause-recrawl="roundFlow.pauseRecrawl()" @continue-recrawl="roundFlow.continueRecrawl()" @finish-save="roundFlow.finishRecrawl()" />
      </section>

      <section
        v-else
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
        <div v-if="!historyMode && !resultLoaded" class="latest-empty" data-testid="latest-result-empty">
          暂无结果：开始新一轮并将最新结果保存后，这里会显示最新轮次。
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
          <span v-if="!isScrapedOnly" class="command-note" aria-hidden="true">判定依据：你的简历关键词 · 两阶段判断</span>
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
          :jobs="currentJobs"
          :empty-message="currentEmptyMessage"
          :defer-mobile-detail="Boolean(recrawlSnapshot && recrawlSnapshot.status === 'paused')"
          :platform-filter="historyMode ? '' : resultPlatformFilter"
          :result-epoch="resultEpoch"
          @update:platform-filter="onResultPlatformFilterChange"
        >
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
      :items="historyItems"
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
    />

    <!-- 岗位轨迹浮窗：居中弹窗，内容为原生命周期卡片全部能力 -->
    <Transition name="dialog">
      <div
        v-if="lifecycleDialogOpen && lifecycleDialogJob"
        class="dialog-backdrop lifecycle-dialog-backdrop"
        data-testid="lifecycle-dialog"
        @click.self="closeLifecycleDialog"
      >
      <section class="dialog-panel lifecycle-dialog" role="dialog" aria-modal="true" aria-label="岗位轨迹">
        <header class="lifecycle-dialog-header">
          <h2>岗位轨迹</h2>
          <button
            class="icon-button"
            type="button"
            aria-label="关闭岗位轨迹浮窗"
            data-testid="lifecycle-dialog-close"
            @click="closeLifecycleDialog"
          >
            <X :size="18" aria-hidden="true" />
          </button>
        </header>
        <JobLifecycleActions
          :profile-id="profileId"
          :job="lifecycleDialogJob"
          @job-feedback-changed="onJobFeedbackChanged"
        />
      </section>
      </div>
    </Transition>

    <OneClickScreenDialog
      :open="oneClickOpen"
      :platform="draftPlatform"
      :groups="oneClickGroups"
      v-model="filterValues[draftPlatform]"
      :has-old-result="hasOldResult"
      @close="oneClickOpen = false"
      @confirm="confirmOneClick"
    />
  </main>
</template>
<style scoped>
.adv-mode-summary {
  margin: -6px 0 14px;
  padding: 9px 11px;
  border: 1px solid var(--hair-2);
  border-radius: 7px;
  background: var(--panel-2);
  color: var(--ink-3);
  font-size: 12px;
  line-height: 1.55;
}

.history-round-marker {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid var(--hair);
  border-radius: 8px;
  background: var(--panel);
  color: var(--text-soft);
  font-size: 0.86rem;
}

.latest-empty {
  margin: 0 0 16px;
  padding: 18px;
  border: 1px dashed var(--hair-2);
  border-radius: 8px;
  background: var(--panel);
  color: var(--text-soft);
  text-align: center;
}

/* 搜索页有 fixed 地点浮层：只淡入不位移，避免 transform 成为 fixed 定位容器 */
.workflow-stack.search-layout {
  animation: stage-fade .22s ease both;
}
</style>
