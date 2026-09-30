import { computed, onBeforeUnmount, ref, watch, type Ref } from "vue";
import type { ConditionSnapshotV2, Notice, Platform, TaskSnapshot as ApiTaskSnapshot } from "../types";
import { errorMessage } from "../api";
import { platformLabel } from "../discovery";
import type { DiscoveryState, StepId } from "./useDiscoveryState";
import { setThemePlatform } from "./useTheme";
import {
  isParallelFlowStartStaleError,
  isActiveParallelFlow,
  registerDiscoveryParallelRecovery,
  type ParallelFlowState,
  type ParallelSelection,
  type useDiscoveryParallelFlow,
} from "./useDiscoveryParallelFlow";
import { useDiscoveryFlowPresentation } from "./useDiscoveryFlowPresentation";
import type { useDiscoverySceneState } from "./useDiscoverySceneState";

/** A Flow is active when either its envelope or one of its Tracks is active. */
export function hasActiveDiscoveryFlow(flow: ParallelFlowState | null | undefined): boolean {
  return isActiveParallelFlow(flow);
}

type ParallelFlowController = ReturnType<typeof useDiscoveryParallelFlow>;
type SceneStore = ReturnType<typeof useDiscoverySceneState>;
type FlowResultsController = {
  loadLatestResult: (options?: { preservePresentation?: boolean }) => Promise<unknown>;
};

/**
 * Keep the result source projection beside the Flow mode rules.  DiscoveryView
 * only wires these refs into useDiscoveryResults; it must not duplicate the
 * ownership checks that decide whether the current result belongs to Flow.
 */
export function createDiscoveryFlowResultProjection(options: {
  state: DiscoveryState;
  flow: ParallelFlowController;
  parallelMode: Ref<boolean>;
}) {
  const { state, flow, parallelMode } = options;
  const currentResultsFlowId = computed(() => {
    const current = flow.flow.value;
    if (!current) return "";
    if (parallelMode.value && current.selection === "all") return current.id;
    if (!parallelMode.value && current.selection === state.draftPlatform.value) return current.id;
    return "";
  });
  const currentResultsFlowSelection = computed(() => {
    const current = flow.flow.value;
    if (!current) return "";
    if (parallelMode.value && current.selection === "all") return current.selection;
    if (!parallelMode.value && current.selection === state.draftPlatform.value) return current.selection;
    return "";
  });
  return { currentResultsFlowId, currentResultsFlowSelection };
}

export interface DiscoveryFlowCoordinatorOptions {
  state: DiscoveryState;
  flow: ParallelFlowController;
  parallelMode: Ref<boolean>;
  results: FlowResultsController;
  sceneStore: SceneStore;
  profileId: () => string;
  emitIslandNotice: (payload: { id: string; title: string; detail?: string; target?: "results" | "task" }) => void;
  notify: (message: string, tone?: Notice["tone"]) => void;
  requestDraftPlatform: (platform: Exclude<ParallelSelection, "all">) => unknown;
  openOneClick: () => unknown;
  hasLiveTaskState: () => boolean;
  resetWorkflow: () => Promise<boolean>;
  abandonRound: () => Promise<boolean>;
  maybeAutoStartNewRound: () => Promise<boolean>;
  restoreWorkflowState: () => unknown;
  restoreSaved02State: () => unknown;
  loadAdvancedSettings: () => unknown;
  loadFilterLabels: () => unknown;
  loadCityCatalog: () => unknown;
  restoreRunningTask: () => Promise<unknown>;
}

/**
 * Owns the page-facing coordination around the asynchronous Flow boundary.
 * The view supplies state/actions and only exposes the returned refs/handlers.
 */
export function useDiscoveryFlowCoordinator(options: DiscoveryFlowCoordinatorOptions) {
  const { state, flow, parallelMode, results } = options;
  let userModeIntent = 0;
  let profileRestoreInProgress: number | null = null;
  let flowNavigationOwned = false;
  let restoreNavigationPending = Boolean(state.restoredWorkflowSnapshot.value?.activeStep);
  let dialogPreparationGeneration = 0;
  let disposed = false;

  function ownsParallelFlowNavigation(): boolean {
    return parallelMode.value && flow.flow.value?.selection === "all";
  }

  const recoveryParallelMode = computed({
    get: () => parallelMode.value,
    set: (value: boolean) => {
      if (userModeIntent === 0) {
        parallelMode.value = value;
        return;
      }
      // A late recovery response must not reintroduce a completed
      // single-platform Flow after the user explicitly chose 全部.
      if (value && flow.flow.value?.selection !== "all" && !flow.hasActiveTrack.value) {
        flow.restore(null);
      }
    },
  });

  function selectParallelMode(selection: "all" | Platform): void {
    const leavingAllFlow = selection !== "all" && flow.flow.value?.selection === "all";
    userModeIntent += 1;
    invalidate();
    flow.clearFlowError();
    if (selection === "all" && flow.flow.value?.selection !== "all" && !flow.hasActiveTrack.value) {
      flow.restore(null);
    }
    parallelMode.value = selection === "all";
    if (selection !== "all") options.requestDraftPlatform(selection);
    if (leavingAllFlow && state.activeStep.value === "results") void results.loadLatestResult();
  }

  let intent = 0;

  function invalidate(): void {
    intent += 1;
  }

  function beginProfileSwitch(): number {
    invalidate();
    parallelMode.value = true;
    flow.restore(null);
    return intent;
  }

  function isCurrent(requestIntent: number): boolean {
    return !disposed && requestIntent === intent;
  }

  function applyProfileRestore(
    restored: ParallelFlowState | null,
    requestIntent?: number,
  ): boolean {
    if (requestIntent !== undefined && !isCurrent(requestIntent)) return false;
    if (!restored) {
      parallelMode.value = true;
      return true;
    }
    // An explicitly recovered single-platform Flow owns the initial mode even
    // when its Track is already terminal. Null/error paths stay on 全部.
    parallelMode.value = restored.selection === "all";
    return true;
  }

  async function prepare(
    selection: ParallelSelection,
    request: (requestIntent: number) => Promise<unknown>,
    isRequestCurrent: (requestIntent: number) => boolean = () => true,
  ): Promise<boolean> {
    const requestIntent = ++intent;
    parallelMode.value = selection === "all";
    await request(requestIntent);
    if (!isCurrent(requestIntent) || !isRequestCurrent(requestIntent)) return false;

    const restored = flow.flow.value;
    if (selection === "all") {
      // A completed legacy single-platform Flow returned by preparation is
      // history, not the user's new 全部现场.
      if (restored && restored.selection !== "all" && !hasActiveDiscoveryFlow(restored)) {
        flow.restore(null);
      }
      parallelMode.value = true;
    }
    return true;
  }

  // 历史列表行只说用户能感知的一件事：这一轮的条件已经冻结、按当时条件跑。
  // 原始 JSON、内部映射版本号与英文字段码都不属于列表行（它们既读不懂，又会整坨
  // 成为该轮按钮的可访问名）；具体冻结了哪些条件留在点开该轮后的详情里。
  function frozenFlowTrackMessage(track: (typeof state.historyStore.flowItems.value)[number]["tracks"][number]): string {
    const raw = track.confirmed_filters_snapshot;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return "";
    if (!Object.keys(raw).length) return "";
    return "筛选条件已按当时冻结";
  }

  const historyFlowItems = computed(() => state.historyStore.flowItems.value.map((flowItem) => ({
    ...flowItem,
    tracks: flowItem.tracks.map((track) => {
      const frozen = frozenFlowTrackMessage(track);
      return frozen ? { ...track, message: [track.message, frozen].filter(Boolean).join(" · ") } : track;
    }),
  })));

  function drainFlowPresentationNotices(): void {
    let notice = flowPresentation.consumeNotice();
    while (notice) {
      options.emitIslandNotice({
        id: notice.id,
        title: `${platformLabel(notice.platform)}结果已加入`,
        detail: "当前流程的新结果已原地合入",
        target: "results",
      });
      notice = flowPresentation.consumeNotice();
    }
  }

  const flowPresentation = useDiscoveryFlowPresentation({
    flow: computed(() => flow.flow.value || null),
    projectReachableSteps: (steps) => {
      if (ownsParallelFlowNavigation()) state.setFlowReachableSteps(steps);
    },
    navigateStep: (step) => {
      if (ownsParallelFlowNavigation()) state.navigateStep(step, { source: "flow" });
    },
    deps: {
      fetchTaskState: (runId) => flow.fetchTaskState(runId) as Promise<ApiTaskSnapshot | null>,
      fetchFlowResults: async (flowId) => flow.flow.value?.id === flowId ? flow.fetchFlowResults(flowId) : null,
      refreshResults: (preservePresentation = true) => results.loadLatestResult({ preservePresentation }),
      onNoticeReady: drainFlowPresentationNotices,
    },
  });

  const flowFailureNotice = computed(() => {
    if (!parallelMode.value || flow.flow.value?.selection !== "all") return "";
    const tracks = (state.pipelineResult.value as (typeof state.pipelineResult.value & {
      flow_tracks?: Array<Record<string, unknown>>;
    }) | null)?.flow_tracks || [];
    return tracks
      .filter((track) => ["failed", "unavailable", "interrupted"].includes(String(track.status || ""))
        || track.unfinished_ai_screening === true)
      .map((track) => `${platformLabel(String(track.platform || ""))}：${String(track.message || track.reason || track.error || "AI 筛选未完成")}`)
      .join("；");
  });
  const flowStatusNotice = computed(() => flow.error.value || (
    flow.stale.value
      ? "流程状态暂不可确认，当前流程仅可查看，Track 操作和新一轮已暂停"
      : ""
  ));

  function refreshFlowPresentation(): void {
    if (!ownsParallelFlowNavigation()) return;
    flowNavigationOwned = true;
    const presentationIntent = intent;
    void flowPresentation.refresh().then(() => {
      if (isCurrent(presentationIntent)) drainFlowPresentationNotices();
    }).catch(() => {});
  }

  // 全平台 Flow 的唯一「进入 04 就载入本轮结果」触发点（触发路径只有这一条，不再加第二套）。
  // 必须 post flush：视图里「已进 04 页」的置位与这里同为 activeStep 监听，注册先后决定回调
  // 顺序，pre flush 时它会先跑，而 loadLatestResult 的闸门要求 resultsPageSeen 已置位——
  // 于是恢复现场进 04 的结果永远载入不进来。
  watch(state.activeStep, (step) => {
    if (step === "results") void results.loadLatestResult({ preservePresentation: Boolean(flow.flow.value?.id) });
  }, { flush: "post" });
  watch(
    [parallelMode, () => flow.flow.value?.id, () => flow.flow.value?.selection, state.draftPlatform],
    ([allPlatforms, flowId, selection]) => {
      // A terminal single-platform Flow is still the authoritative result
      // source. Its refresh mutates the Flow before the mode projection is
      // applied, so load once after the projection settles rather than
      // falling back to the legacy latest-result endpoint.
      if (!allPlatforms && flowId && selection === state.draftPlatform.value
        && flow.flow.value?.selection === state.draftPlatform.value
        && !hasActiveDiscoveryFlow(flow.flow.value)) {
        void results.loadLatestResult({ preservePresentation: true });
      }
    },
    { flush: "post" },
  );
  watch(() => flow.flow.value, () => {
    if (ownsParallelFlowNavigation()) {
      refreshFlowPresentation();
    } else if (flowNavigationOwned) {
      flowNavigationOwned = false;
      flowPresentation.resetNavigation();
      state.setFlowReachableSteps(null);
      state.setNavigationManualHold(false);
      restoreNavigationPending = false;
    }
  }, { flush: "post", immediate: true });
  watch(flow.hasActiveTrack, (active) => state.setFlowActive(active), { immediate: true });
  // 「本轮未结束」（hasActiveTrack：含已中断、已暂停）与「此刻有活体 worker」
  // （hasLiveWorker：轨道排队中/运行中）是两件事，必须各自投影：前者锁范围、定落点，
  // 后者才决定 04 能否接回本轮结果、迟到响应能否覆盖实时现场。
  watch(flow.hasLiveWorker, (live) => state.setFlowLiveWorker(live), { immediate: true });
  watch(
    [parallelMode, () => flow.flow.value?.selection, flowPresentation.unlockedSteps, flowPresentation.hydrated],
    ([allPlatforms, selection, projected, hydrated]) => {
      const ownsFlow = allPlatforms && selection === "all";
      if (ownsFlow) {
        if (!flowNavigationOwned && flow.flow.value) refreshFlowPresentation();
        flowNavigationOwned = true;
      } else {
        if (flowNavigationOwned) flowPresentation.resetNavigation();
        flowNavigationOwned = false;
        state.setFlowReachableSteps(null);
        state.setNavigationManualHold(false);
        if (!parallelMode.value || selection !== undefined) restoreNavigationPending = false;
      }
      if (ownsFlow) state.setFlowReachableSteps(hydrated ? projected : null);
      if (ownsFlow && hydrated && restoreNavigationPending) {
        restoreNavigationPending = false;
        const restoredStep = state.restoredWorkflowSnapshot.value?.activeStep;
        if (typeof restoredStep === "string") state.reconcileActiveStep(restoredStep);
      }
    },
    { deep: true, immediate: true },
  );
  function syncDiscoveryTheme(): void {
    if (parallelMode.value) setThemePlatform("all");
    else if (state.viewPlatform.value === "boss" || state.viewPlatform.value === "zhilian") {
      setThemePlatform(state.viewPlatform.value);
    }
  }
  watch([parallelMode, state.viewPlatform, () => flow.flow.value?.selection], syncDiscoveryTheme, { flush: "post", immediate: true });
  // Legacy recovery may publish a brand theme after the Flow; keep Flow-owned all neutral.
  watch([state.activeTaskRestored, state.scrapeSnapshot, state.screenSnapshot, state.recrawlSnapshot, state.pipelineResult], () => {
    if (parallelMode.value && flow.flow.value?.selection === "all") setThemePlatform("all");
  }, { flush: "post" });
  watch([state.activeStep, flowPresentation.highestUnlocked], ([step, highest]) => {
    if (!flowPresentation.hydrated.value) return;
    const rank: Record<StepId, number> = { upload: 0, search: 1, screen: 2, results: 3 };
    if (rank[step] < rank[highest]) {
      flowPresentation.setManualHold(true);
      state.setNavigationManualHold(true);
    } else if (rank[step] === rank[highest]) {
      flowPresentation.setManualHold(false);
      state.setNavigationManualHold(false);
    }
  });

  const parallelDialogPreparing = ref(false);
  const conditionSnapshot = ref<ConditionSnapshotV2 | null>(flow.createConditionSnapshot());
  const parallelMappingError = ref("");
  const parallelPlatformGroups = computed(() => ({
    boss: flow.platformGroups.boss?.length ? flow.platformGroups.boss : state.oneClickGroups.value,
    zhilian: flow.platformGroups.zhilian?.length ? flow.platformGroups.zhilian : state.oneClickGroups.value,
  }));

  function parallelDialogInputKey(): string {
    try {
      return JSON.stringify({
        profileId: options.profileId(),
        draftPlatform: state.draftPlatform.value,
        selectedKeywords: [...state.selectedKeywords.value],
        cities: [...state.effectiveSearchCities.value],
        locations: {
          boss: state.locationDraft.allLocations("boss", state.cityList.value),
          zhilian: state.locationDraft.allLocations("zhilian", state.cityList.value),
        },
        profileSummary: state.profileSummary.value,
        profileFacts: state.profileFacts.value,
      });
    } catch {
      return "dialog-input-unserializable";
    }
  }

  function invalidateDialogPreparation(): void {
    dialogPreparationGeneration += 1;
    parallelDialogPreparing.value = false;
  }

  function prepareParallelDialog(): void {
    if (!parallelMode.value || !state.oneClickOpen.value || parallelDialogPreparing.value) return;
    const generation = ++dialogPreparationGeneration;
    const inputKey = parallelDialogInputKey();
    parallelDialogPreparing.value = true;
    const isCurrentPreparation = (requestIntent: number) => isCurrent(requestIntent)
      && generation === dialogPreparationGeneration
      && inputKey === parallelDialogInputKey()
      && state.oneClickOpen.value;
    void prepare("all", (requestIntent) => flow.prepareDialog(
      { boss: {}, zhilian: {} },
      {
        keywords: state.selectedKeywords.value,
        cities: state.effectiveSearchCities.value,
        locations: {
          boss: state.locationDraft.allLocations("boss", state.cityList.value),
          zhilian: state.locationDraft.allLocations("zhilian", state.cityList.value),
        },
        profileSummary: state.profileSummary.value,
        profileFacts: state.profileFacts.value,
        selection: "all",
        resumeSemantic: state.resumeAnalysis.value?.semantic || null,
      },
      { isCurrent: () => isCurrentPreparation(requestIntent) },
    ), isCurrentPreparation).then((applied) => {
      if (applied) conditionSnapshot.value = flow.createConditionSnapshot();
    }).catch((error) => {
      if (isCurrentPreparation(intent)) {
        parallelMappingError.value = errorMessage(error, "平台筛选条件已变化，请更新后重试");
      }
    }).finally(() => {
      if (generation === dialogPreparationGeneration) parallelDialogPreparing.value = false;
    });
  }

  function openOneClickWithParallel(): void {
    parallelMappingError.value = "";
    options.openOneClick();
    prepareParallelDialog();
  }

  watch(state.oneClickOpen, (open) => {
    if (open) prepareParallelDialog();
    else invalidateDialogPreparation();
  });
  watch(
    [
      state.selectedKeywords,
      state.cityText,
      state.customCity,
      () => state.locationDraft.byPlatform,
      state.profileSummary,
      state.profileFacts,
      state.draftPlatform,
    ],
    () => {
      if (state.oneClickOpen.value && parallelDialogPreparing.value) invalidateDialogPreparation();
    },
    { deep: true },
  );

  function handleUnifiedFilterChange(field: Parameters<typeof flow.setUnifiedFilters>[0], values: string[]): void {
    try {
      flow.setUnifiedFilters(field, values);
      conditionSnapshot.value = flow.createConditionSnapshot();
      parallelMappingError.value = "";
    } catch (error) {
      parallelMappingError.value = errorMessage(error, "平台筛选条件已变化，请更新后重试");
      options.notify(errorMessage(error, "平台筛选条件已变化，请更新后重试"), "error");
    }
  }

  function handleParallelPlatformChange(platform: Platform, field: string, values: string[]): void {
    flow.setPlatformFilters(platform, {
      ...flow.platformValues[platform],
      [field]: [...values],
    });
    conditionSnapshot.value = flow.createConditionSnapshot();
  }

  async function confirmParallelOneClick(fields: Record<Platform, Record<string, string[]>>): Promise<void> {
    if (parallelMappingError.value || parallelDialogPreparing.value || flow.loading.value) return;
    const requestIntent = intent;
    try {
      conditionSnapshot.value = flow.createConditionSnapshot(fields);
      if (flow.stale.value || !flow.canStartNewRound.value) return;
      const started = await flow.start("all", undefined, { snapshot: conditionSnapshot.value });
      if (!isCurrent(requestIntent) || !started) return;
      setThemePlatform("all");
      state.oneClickOpen.value = false;
    } catch (error) {
      if (isParallelFlowStartStaleError(error)) {
        options.notify(`旧画像流程 ${error.flow.id} 已创建，请切回原画像查看`, "warning");
        return;
      }
      parallelMappingError.value = errorMessage(error, "平台筛选条件已变化，请更新后重试");
      options.notify(errorMessage(error, "流程启动失败"), "error");
    }
  }

  function resetFlowNavigationProjection(): void {
    restoreNavigationPending = false;
    flow.restore(null);
    flow.resetConditionState();
    flowPresentation.resetNavigation();
    state.setFlowReachableSteps(null);
    state.setNavigationManualHold(false);
  }

  const resetWorkflowAfterSuccess = async (): Promise<void> => {
    const requestIntent = intent;
    if (await options.resetWorkflow() && isCurrent(requestIntent)) resetFlowNavigationProjection();
  };
  const abandonRound = async (): Promise<void> => {
    const requestIntent = intent;
    if (await options.abandonRound() && isCurrent(requestIntent)) resetFlowNavigationProjection();
  };
  const maybeAutoStartNewRound = async (): Promise<void> => {
    const requestIntent = intent;
    if (await options.maybeAutoStartNewRound() && isCurrent(requestIntent)) resetFlowNavigationProjection();
  };

  type ProfileFlowRecoveryStatus = "flow" | "empty" | "error" | "stale";

  async function refreshParallelFlowForProfile(requestIntent: number): Promise<ProfileFlowRecoveryStatus> {
    try {
      const restored = await flow.refresh({ isCurrent: () => isCurrent(requestIntent) });
      if (!isCurrent(requestIntent)) return "stale";
      flow.resetConditionState();
      flow.restore(restored);
      applyProfileRestore(restored, requestIntent);
      if (restored?.selection === "boss" || restored?.selection === "zhilian") {
        options.requestDraftPlatform(restored.selection);
      }
      return restored ? "flow" : "empty";
    } catch (error) {
      if (isCurrent(requestIntent)) options.notify(errorMessage(error, "当前画像流程恢复失败"), "warning");
      return isCurrent(requestIntent) ? "error" : "stale";
    }
  }

  watch(options.profileId, () => {
    const profileRestoreIntent = beginProfileSwitch();
    profileRestoreInProgress = profileRestoreIntent;
    invalidateDialogPreparation();
    options.sceneStore.clearForProfileSwitch();
    state.resetForProfileSwitch();
    restoreNavigationPending = false;
    flowPresentation.resetNavigation();
    state.historyStore.hide();
    state.historyStore.setProfile(options.profileId());
    options.restoreWorkflowState();
    restoreNavigationPending = typeof state.restoredWorkflowSnapshot.value?.activeStep === "string";
    void options.loadAdvancedSettings();
    void options.loadFilterLabels();
    void options.loadCityCatalog();
    let profileFlowRecoveryStatus: ProfileFlowRecoveryStatus = "stale";
    void refreshParallelFlowForProfile(profileRestoreIntent).then(async (status) => {
      profileFlowRecoveryStatus = status;
      // Successful Flow recovery may still hydrate a legacy task snapshot for
      // the existing progress panel. Error/stale recovery never continues
      // legacy work; only the empty branch below may start a new round.
      if ((status === "empty" || status === "flow") && isCurrent(profileRestoreIntent)) {
        await options.restoreRunningTask();
        if (!isCurrent(profileRestoreIntent)) return;
      }
    }).finally(() => {
      if (!isCurrent(profileRestoreIntent)) return;
      options.restoreSaved02State();
      if (profileFlowRecoveryStatus === "empty" && options.hasLiveTaskState()) parallelMode.value = false;
      profileRestoreInProgress = null;
      if (profileFlowRecoveryStatus === "empty"
        && !state.scrapeBusy.value && !state.screenBusy.value && !state.recrawlBusy.value) {
        void maybeAutoStartNewRound();
      }
    });
  });
  watch(state.activeTaskRestored, (restored) => {
    if (restored && !flow.flow.value && profileRestoreInProgress === null) parallelMode.value = false;
  });
  watch(state.pipelineResult, (result) => {
    if (result && !flow.flow.value && profileRestoreInProgress === null) parallelMode.value = false;
  });

  // Initial Flow/legacy recovery belongs to the same coordinator as profile
  // replacement recovery.  Capturing this intent at mount prevents a late
  // response from reattaching a single-platform Flow after an explicit mode
  // choice, and keeps Flow errors out of the legacy continuation path.
  registerDiscoveryParallelRecovery({
    parallelFlow: flow,
    parallelMode: recoveryParallelMode,
    requestDraftPlatform: options.requestDraftPlatform,
    loadAdvancedSettings: options.loadAdvancedSettings,
    loadFilterLabels: options.loadFilterLabels,
    loadCityCatalog: options.loadCityCatalog,
    restoreRunningTask: options.restoreRunningTask,
    restoreSaved02State: options.restoreSaved02State,
    maybeAutoStartNewRound: options.maybeAutoStartNewRound,
    scrapeBusy: state.scrapeBusy,
    screenBusy: state.screenBusy,
    recrawlBusy: state.recrawlBusy,
    getRecoveryIntent: () => intent,
  });

  onBeforeUnmount(() => {
    // Invalidate every async branch before stopping presentation polling. Any
    // response that settles after this point must be observationally inert.
    disposed = true;
    invalidate();
    // Task/legacy recovery shares the workflow epoch with round transitions.
    // Bump it at the component boundary so an already-started restore or
    // auto-new-round chain stops inside its next await, not just at this
    // coordinator's final projection write.
    state.invalidateWorkflowEpoch();
    invalidateDialogPreparation();
    profileRestoreInProgress = null;
    flowPresentation.stop();
  });

  return {
    flowPresentation,
    recoveryParallelMode,
    historyFlowItems,
    flowFailureNotice,
    flowStatusNotice,
    conditionSnapshot,
    parallelMappingError,
    parallelDialogPreparing,
    parallelPlatformGroups,
    selectParallelMode,
    openOneClickWithParallel,
    prepareParallelDialog,
    handleUnifiedFilterChange,
    handleParallelPlatformChange,
    confirmParallelOneClick,
    resetFlowNavigationProjection,
    resetWorkflowAfterSuccess,
    abandonRound,
    maybeAutoStartNewRound,
  };
}
