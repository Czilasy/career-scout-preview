import { computed, onBeforeUnmount, ref, watch, type Ref } from "vue";
import type { ConditionSnapshotV2, Notice, Platform, TaskSnapshot as ApiTaskSnapshot } from "../types";
import { errorMessage, userFacingMessage } from "../api";
import { platformLabel, ACTIVE_TRACK_STATUSES, TRACK_PROBLEM_STATUSES } from "../discovery";
import { projectConditionChips } from "./useDiscoveryState";
import { trackHasDeliveredResult, type FlowPresentationTrack } from "./useDiscoveryFlowPresentation";
import { hasUnfinishedRound } from "./useDiscoveryState";
import type { DiscoveryState, StepId } from "./useDiscoveryState";
import { setThemePlatform } from "./useTheme";
import {
  isParallelFlowStartStaleError,
  hasUnfinishedParallelRound,
  registerDiscoveryParallelRecovery,
  type ParallelFlowState,
  type ParallelSelection,
  type ParallelTrackState,
  type useDiscoveryParallelFlow,
} from "./useDiscoveryParallelFlow";
import { useDiscoveryFlowPresentation } from "./useDiscoveryFlowPresentation";
import type { useDiscoverySceneState } from "./useDiscoverySceneState";

/** A Flow is active when either its envelope or one of its Tracks is active. */
export function hasActiveDiscoveryFlow(flow: ParallelFlowState | null | undefined): boolean {
  return hasUnfinishedParallelRound(flow);
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
  resetWorkflow: () => Promise<boolean>;
  abandonRound: () => Promise<boolean>;
  maybeAutoStartNewRound: () => Promise<boolean>;
  restoreWorkflowState: () => unknown;
  restoreSaved02State: (options?: { preserveResult?: boolean }) => unknown;
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
      if (value && flow.flow.value?.selection !== "all" && !flow.hasUnfinishedRound.value) {
        flow.restore(null);
      }
    },
  });

  function selectParallelMode(selection: "all" | Platform): void {
    const leavingAllFlow = selection !== "all" && flow.flow.value?.selection === "all";
    userModeIntent += 1;
    invalidate();
    flow.clearFlowError();
    if (selection === "all" && flow.flow.value?.selection !== "all" && !flow.hasUnfinishedRound.value) {
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

  const historyFlowItems = computed(() => state.historyStore.flowItems.value);

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
    projectReachableSteps: (steps, flowId) => {
      if (ownsParallelFlowNavigation()) state.setFlowReachableSteps(steps, flowId);
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

  watch(
    [flowPresentation.completedStages, parallelMode, () => flow.flow.value?.selection, state.draftPlatform],
    ([completed, allPlatforms, selection, draft]) => {
      const ownsCurrentRound = allPlatforms ? selection === "all" : selection === draft;
      state.setFlowCompletedSteps(ownsCurrentRound ? completed : null);
    },
    { immediate: true },
  );

  const flowFailureNotice = computed(() => {
    if (!parallelMode.value || flow.flow.value?.selection !== "all") return "";
    const tracks = (state.pipelineResult.value as (typeof state.pipelineResult.value & {
      flow_tracks?: Array<Record<string, unknown>>;
    }) | null)?.flow_tracks || [];
    return tracks
      .filter((track) => TRACK_PROBLEM_STATUSES.includes(String(track.status || ""))
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
  watch(flow.hasUnfinishedRound, (active) => state.setFlowActive(active), { immediate: true });
  // 「本轮未结束」（hasUnfinishedRound：含已中断、已暂停）与「此刻有活体 worker」
  // （hasLiveWorker：轨道排队中/运行中）是两件事，必须各自投影：前者锁范围、定落点，
  // 后者才决定 04 能否接回本轮结果、迟到响应能否覆盖实时现场。
  watch(flow.hasLiveWorker, (live) => state.setFlowLiveWorker(live), { immediate: true });
  // SPEC 046 FR-015：「能不能开新一轮」是第三个问题，成员与上面两份都不同（排队/运行/
  // 暂停锁、已中断放行），整棵树只有 useDiscoveryParallelFlow 那一份清单回答它。树干的
  // pipelineBusy 与 02 主启动按钮读这一份投影，不再由「本轮未结束」代答——那会让服务
  // 重启打断的轮次把主按钮永久锁死（收口第一单强阻断）。
  watch(flow.newRoundLocked, (locked) => state.setFlowLocksNewRound(locked), { immediate: true });
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
      // SPEC 046 V2 状态所有权：页面可达性唯一来源是 Flow 投影一处。水合还没完成时
      // 也要发布投影当前持有的这一份（同一 Flow 只增不减的解锁集合），不再用 null
      // 表示「还没查到」——null 落进守卫就是刷新/轮询间隙里把已解锁页重新锁住的空窗。
      if (ownsFlow) state.setFlowReachableSteps(projected, flow.flow.value?.id || "");
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

  // ---------------------------------------------------------------------------
  // SPEC 046 Edge Cases：任一平台被系统禁用新建任务时，「全部」必须在提交前挡住并
  // 指出不可用平台；可用平台仍可单独启动。
  // 缺陷现场：门禁只看草稿平台的 schema，选了「全部」时另一条线的禁用要等后端 503
  // 才知道，而且提示语把内部平台码直接吐给用户。可用性事实仍取既有的平台 schema
  // 投影（enabled_for_new_tasks，与草稿平台那条门禁同一来源），显示名走平台显示名
  // 投影——树干不写平台名，也不新造第二份可用性判定。「全部」的可用性要等平台侧
  // 真读过一次才知道，所以进「全部」就把各平台 schema 取回来。
  // ---------------------------------------------------------------------------
  const parallelDisabledPlatforms = computed<Platform[]>(() => Object.entries(flow.platformSchemas)
    .filter(([, schema]) => schema?.enabled_for_new_tasks === false)
    .map(([platform]) => platform) as Platform[]);

  const parallelStartBlockedNotice = computed(() => {
    if (!parallelMode.value || !parallelDisabledPlatforms.value.length) return "";
    const names = parallelDisabledPlatforms.value.map((platform) => platformLabel(platform)).join("、");
    return `「全部」需要各平台都能新建任务：${names} 已停用。可切换到可用平台单独开始这一轮。`;
  });

  // 主启动按钮的禁用判定合并在这一层：页面只绑定结果，不再自己拼「草稿平台门禁 ||
  // 全部门禁」这条口径（同一份事实在 openOneClickWithParallel / confirmParallelOneClick
  // 里各挡一次，三处必须同源）。
  const oneClickStartDisabled = computed(
    () => Boolean(state.oneClickDisabled.value || parallelStartBlockedNotice.value),
  );

  // 「全部」的可用性要等平台侧真读过一次才知道，所以在启动入口（02 页）就把各平台
  // schema 投影取回一次；已在其它步骤时不发这个请求，避免与单平台 schema 加载抢同一
  // 份在飞请求。可用性判定本身仍只读平台 schema 投影那一份事实。
  // 配置包恢复是唯一的例外（044：同一套配置包两个平台共用）：它只是把草稿回填到 02
  // 页，不代表用户要用「全部」开新一轮，因此这条路径不做逐平台预检；预检回到用户点
  // 提交入口那一次（对话框准备本来就读各平台 schema 一次，两份门禁共用同一份结果），
  // 门禁照旧——被禁用的平台绝不可能把流程发出去。落位标记只吃掉一次预检时机。
  let packageRestoreLandingPending = false;
  function skipAvailabilityPrecheckForPackageRestore(): void {
    packageRestoreLandingPending = true;
  }

  watch(
    [parallelMode, state.activeStep, () => Object.values(flow.platformSchemas).some(Boolean)],
    ([allPlatforms, step, availabilityKnown]) => {
      if (!allPlatforms || step !== "search" || availabilityKnown) return;
      if (packageRestoreLandingPending) {
        packageRestoreLandingPending = false;
        return;
      }
      void flow.loadPlatformGroups();
    },
    { immediate: true },
  );

  // 对话框已经打开时不可用事实才到达：把原因写进对话框既有的错误位，
  // 让「确认」按同一份事实保持禁用，不再让用户点下去才知道。
  watch(parallelStartBlockedNotice, (message) => {
    if (message && state.oneClickOpen.value) parallelMappingError.value = message;
  });

  // ---------------------------------------------------------------------------
  // SPEC 046 D-07：本轮范围锁定时，03 页不能只把话说成「只读」——界面也得真的锁住。
  // 锁定事实只有这一份：本轮（「全部」轮且按状态词表的唯一谓词判为未结束）已把条件
  // 交给轨道，条件草稿槽与用户看到的芯片就不再是同一件事（芯片按空草稿把「不限 /
  // 全部」点亮，汇总却写「未设置筛选条件」）。汇总读本轮冻结快照投影出的实际值，
  // 卡片与芯片读同一个 computed，不再各判一遍。
  // ---------------------------------------------------------------------------
  const roundConditionLocked = computed(() => parallelMode.value
    && flow.flow.value?.selection === "all"
    && flow.hasUnfinishedRound.value);

  // 本轮条件的唯一事实源是轨道的 confirmed_filters_snapshot：这里只把它投影成人话，
  // 不重算条件、不抄原始 JSON，也不去改芯片的渲染口径掩盖矛盾。
  function frozenPlatformValues(track: ParallelTrackState | undefined): Record<string, string[]> {
    const raw = track?.confirmed_filters_snapshot as Record<string, unknown> | undefined;
    if (!raw || typeof raw !== "object") return {};
    const platformValues = raw.platformValues as Record<string, Record<string, string[]>> | undefined;
    if (raw.snapshotVersion === 2) {
      return (platformValues && platformValues[state.draftPlatform.value]) || {};
    }
    // V1 的轨道快照就是纯字段对象（contracts/condition-snapshot.md 第 3 节）。
    return raw as Record<string, string[]>;
  }

  const roundConditionLockSummary = computed(() => {
    if (!roundConditionLocked.value) return "";
    const current = flow.flow.value;
    const frozenTracks = (current?.tracks || []).filter((track) => {
      const snapshot = track.confirmed_filters_snapshot;
      return Boolean(snapshot && typeof snapshot === "object" && Object.keys(snapshot).length);
    });
    if (!frozenTracks.length) return "";
    const frozenTrack = frozenTracks.find((track) => track.platform === state.draftPlatform.value)
      || frozenTracks[0];
    const chips = projectConditionChips(state.filterGroups.value, frozenPlatformValues(frozenTrack));
    const detail = chips.map((chip) => `${chip.label}：${chip.value}`).join("、");
    return detail
      ? `已按本轮确认条件锁定，当前只读——${detail}`
      : "已按本轮确认条件锁定，当前只读（本轮未设置筛选条件）";
  });

  // ---------------------------------------------------------------------------
  // SPEC 046 D-08：04 页在整轮未完成时开放是分轨合流的设计要求（先出结果的平台
  // 立刻可查），缺陷只是没说清。判定不另起一套：本轮是否未结束取状态词表的唯一
  // 谓词（hasUnfinishedRound），还在追赶的轨道取树干的活动态词表（排队中 / 进行中）；
  // 已中断 / 失败的轨道由既有的失败通知负责，这里不重复许诺它会完成。历史轮浏览的是
  // 已归档的那一份结果，本轮追赶的说法在这儿一并关掉，页面不再自己加一层 !historyMode。
  // ---------------------------------------------------------------------------
  const flowRoundPartialNotice = computed(() => {
    const current = flow.flow.value;
    if (!parallelMode.value || current?.selection !== "all" || !flow.hasUnfinishedRound.value) return "";
    if (state.historyMode.value) return "";
    const trackName = (track: FlowPresentationTrack) => platformLabel(String(track.platform || ""));
    // 「有没有结果」「还在不在跑」都读呈现层那一份轨道投影（与 04 的解锁判定同源），
    // 页面层不再自己数 result_run_id：失败但已持久化部分岗位的那条线同样算已有结果。
    const tracks = flowPresentation.flowTracks.value;
    const delivered = tracks.filter((track) => trackHasDeliveredResult(track));
    const catchingUp = tracks.filter((track) => !trackHasDeliveredResult(track)
      && ACTIVE_TRACK_STATUSES.includes(String(track.status || "")));
    if (!delivered.length || !catchingUp.length) return "";
    const arrived = delivered.map(trackName).filter(Boolean).join("、");
    const pending = catchingUp.map(trackName).filter(Boolean).join("、");
    return `本轮仍在进行，当前只含 ${arrived} 的结果；${pending} 完成后会原地加入，无需等待。`;
  });

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
      if (applied && parallelStartBlockedNotice.value) {
        parallelMappingError.value = parallelStartBlockedNotice.value;
      }
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
    if (parallelStartBlockedNotice.value) {
      options.notify(parallelStartBlockedNotice.value, "warning");
      return;
    }
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
    // 提交前最后一道：可用性事实到达得比对话框晚时也不能把请求发出去等 503。
    if (parallelStartBlockedNotice.value) {
      parallelMappingError.value = parallelStartBlockedNotice.value;
      options.notify(parallelStartBlockedNotice.value, "warning");
      return;
    }
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
      options.notify(userFacingMessage(error, "流程启动失败"), "error");
    }
  }

  function resetFlowNavigationProjection(): void {
    restoreNavigationPending = false;
    flow.resetForNewRound();
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
      options.restoreSaved02State({ preserveResult: profileFlowRecoveryStatus === "flow" });
      // SPEC 046 状态词表：画像下没有 Flow 时，本轮若仍未结束（含已暂停）由
      // legacy 单平台现场接管，不得留在并行模式——这里问的是问题 B，不是判活。
      if (profileFlowRecoveryStatus === "empty" && hasUnfinishedRound(state)) parallelMode.value = false;
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
    parallelStartBlockedNotice,
    oneClickStartDisabled,
    skipAvailabilityPrecheckForPackageRestore,
    roundConditionLocked,
    roundConditionLockSummary,
    flowRoundPartialNotice,
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
