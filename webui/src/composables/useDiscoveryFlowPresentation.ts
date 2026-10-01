import { computed, reactive, ref, type Ref } from "vue";
import {
  ACTIVE_TRACK_STATUSES,
  STAGE_IN_FLIGHT_STATUSES,
  UNREADABLE_STAGE_STATUS,
  stageStatusLabel,
} from "../discovery";
import { deriveTrackActionBar, type SharedPrimaryAction } from "../screenFlow";
import type { Platform, TaskSnapshot as ApiTaskSnapshot } from "../types";

export type FlowStepId = "search" | "screen" | "results";
export type FlowProgressKind = "scrape" | "screen";

export interface FlowProgressItem {
  platform: Platform;
  trackId: string;
  kind: FlowProgressKind;
  stage: FlowProgressKind;
  status: string;
  runId: string;
  /**
   * 这一段自己的定稿快照：轨道状态能不能往本段压，这里已经判完并落进 status，
   * 轨道行拿到的就是它要渲染的那一句，不再二次派生（同一张卡两头说两句话的源头）。
   */
  snapshot: ApiTaskSnapshot;
  enteredAt: number;
  /**
   * 这张卡是否代表这条线当前所在的那一段（stage 与 durable run id 一起判）。
   * 缺省视为「是」，保持旧调用方口径不变；为「否」时轨道状态不再往本段快照上压，
   * 头部与卡体就都说本段那一句（拿不到证据时是兜底口径，见下方 stageStatus）。
   */
  carriesLineState: boolean;
  /** 头部徽章与卡体共用的一句话状态词（唯一词表在 discovery.ts）。 */
  statusLabel: string;
  /** 这一条线的动作事实：与单平台同一份派生，按轨道各算一份。 */
  action: SharedPrimaryAction;
  showFinishSave: boolean;
  showCancel: boolean;
  cancelLabel: string;
  /** 本行「结束并保存」交给既有 run 级收尾路径的 run id。 */
  finishRunId: string;
  finishTestId: string;
  cancelTestId: string;
}

export interface FlowPresentationTrack {
  id: string;
  platform: Platform;
  scrape_run_id?: string | null;
  screen_run_id?: string | null;
  result_run_id?: string | null;
  status: string;
  stage: string;
  [key: string]: unknown;
}

export interface FlowResultProjectionTrack {
  id?: string;
  platform: Platform;
  result_run_id?: string | null;
  jobs?: Array<Record<string, unknown>>;
  dropped?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface FlowPresentationInput {
  flowId?: string;
  id?: string;
  tracks: FlowPresentationTrack[];
}

export interface FlowPresentationDeps {
  fetchTaskState?: (runId: string, platform: Platform) => Promise<ApiTaskSnapshot | null>;
  fetchFlowResults?: (flowId: string) => Promise<{ tracks?: FlowResultProjectionTrack[] } | null>;
  refreshResults?: (preservePresentation?: boolean) => Promise<unknown>;
  /** notify the host when a background retry has queued a user-visible notice */
  onNoticeReady?: () => void;
}

const RESULT_REFRESH_RETRY_DELAY_MS = 1000;
const RESULT_REFRESH_MAX_RETRIES = 3;
const SCRAPE_READY_STATUSES = new Set([
  "done", "completed", "completed_with_pending", "partial", "succeeded", "scraped_only",
]);
/** 线停在 AI 段（含收尾/完成）的阶段名。 */
const AI_FAILURE_STAGES = new Set(["ai", "screen", "ai_screen", "complete"]);
/** 轨道级问题态：整条线停了，停在哪个阶段由交接证据决定，不由状态决定。 */
const TRACK_FAILURE_STATUSES = new Set(["failed", "paused", "interrupted", "unavailable"]);
/**
 * 本段既不是当前段、又拿不到自己的证据时的中性口径（卡体显示「等待开始」）。
 * 只适用于从没拿到 run 身份的段；已经跑过却读不到状态的段走 UNREADABLE_STAGE_STATUS。
 * 在飞清单与轨道活动态清单不在这里重复：唯一一份在 discovery.ts。
 */
const NEUTRAL_STAGE_STATUS = "queued";

/**
 * 「这一段是不是这条线当前所在的那一段」的唯一判定。
 * 只读可变 stage 会漏掉后端已确立的一态——durable run id 优先于 stage：
 * AI 硬停可以把 stage 留在抓取段而把具体任务绑进 screen_run_id
 *（webui/flow_task_coordinator.py `_target_run_ids`），绑定 screen_run_id 与写
 * stage='ai' 分两步落库（webui/flow_service.py `mark_scrape_complete`，中间可被
 * 轮询读到），取消只写 status、stage 用 COALESCE 不推进（webui/store_flow_state.py）。
 * 所以：screen_run_id 已绑定或 stage 已是 AI 族 = AI 段为当前段，否则抓取段为当前段。
 * 收哪张卡、可达性投影、头部徽章归谁，全部走这一个谓词，不再各写一份阶段清单。
 */
function isAiCurrentStage(track: FlowPresentationTrack): boolean {
  return AI_FAILURE_STAGES.has(String(track.stage || "")) || Boolean(track.screen_run_id);
}

function ownsTrackState(kind: FlowProgressKind, track: FlowPresentationTrack): boolean {
  return kind === "scrape" ? !isAiCurrentStage(track) : isAiCurrentStage(track);
}

function isTrackFailure(status: string): boolean {
  return TRACK_FAILURE_STATUSES.has(status);
}

/**
 * 这一段最终说哪一句（轨道状态与阶段快照的唯一合流点，原先在轨道行组件里另算一份）。
 * - 不承载轨道状态的卡只说自己那一段：轨道问题态不得压回它头上——它既不是当前段，
 *   又没有本段证据时上面已经把快照换成兜底口径，压回去就是替整条线背「已中断」。
 * - 轨道只能把一张卡往下压（暂停/终态），不能把已经跑完的阶段说成还在跑：
 *   快照仍写在飞态而整条线已经不在活动态时，以线为准。
 * 在飞与活动态清单都不在这里重复：唯一一份在 discovery.ts。
 */
function stageStatusOf(
  snapshotStatus: string,
  trackStatus: string,
  carriesLineState: boolean,
): string {
  if (!carriesLineState) return snapshotStatus || UNREADABLE_STAGE_STATUS;
  if (STAGE_IN_FLIGHT_STATUSES.includes(snapshotStatus) && !ACTIVE_TRACK_STATUSES.includes(trackStatus)) {
    return trackStatus;
  }
  return snapshotStatus || trackStatus;
}

interface RuntimeState {
  hydrated: boolean;
  hydratedAt: number;
  manualHold: boolean;
  unlocked: Set<FlowStepId>;
  seenResults: Set<string>;
  seenResultProjections: Set<string>;
  scrapeOrderIndex: Map<string, number>;
  screenOrderIndex: Map<string, number>;
}

function snapshotForTrack(
  track: FlowPresentationTrack,
  runId: string | null | undefined,
): ApiTaskSnapshot | null {
  if (!runId) return null;
  const raw = track[`${String(runId)}:snapshot`];
  return raw && typeof raw === "object" ? raw as ApiTaskSnapshot : null;
}

function fallbackSnapshot(track: FlowPresentationTrack): ApiTaskSnapshot {
  const trackRecord = track as Record<string, unknown>;
  const reason = track.reason || trackRecord.error || undefined;
  return {
    status: track.status || "unknown",
    progress: {},
    logs: [],
    error_code: trackRecord.error_code,
    reason,
    error: reason,
  } as ApiTaskSnapshot;
}

function hasVisibleJobs(track: FlowPresentationTrack): boolean {
  const jobs = (track as Record<string, unknown>).jobs;
  return Array.isArray(jobs) && jobs.length > 0;
}

export interface FlowPresentationOptions {
  flow: Ref<FlowPresentationInput | null>;
  /**
   * Project Flow reachability into the page owner's navigation guard.
   * 第二条形参是这条投影所属的 Flow 身份：页面守卫用它判断「还是不是同一条 Flow」，
   * 同一条 Flow 的解锁集合只增不减，换到另一条 Flow 时水位重新起步。
   */
  projectReachableSteps?: (steps: Set<FlowStepId> | null, flowId: string) => void;
  /** The page state owns navigation; Flow only requests a transition through this callback. */
  navigateStep?: (step: FlowStepId) => void;
  deps?: FlowPresentationDeps;
}

export function useDiscoveryFlowPresentation(input: FlowPresentationOptions) {
  const deps = input.deps || {} as FlowPresentationDeps;
  const runtime = reactive<Record<string, RuntimeState>>({});
  const scrapeItems = ref<FlowProgressItem[]>([]);
  const screenItems = ref<FlowProgressItem[]>([]);
  const enabledSteps = ref<Set<FlowStepId>>(new Set(["search"]));
  const highestUnlocked = ref<FlowStepId>("search");
  const manualHold = ref(false);
  const notices = ref<Array<{ id: string; platform: Platform; runId: string }>>([]);
  const projectedTracks = ref<FlowPresentationTrack[] | null>(null);

  const flowId = computed(() => input.flow.value?.flowId || input.flow.value?.id || "");
  const tracks = computed(() => projectedTracks.value || input.flow.value?.tracks || []);
  let refreshGeneration = 0;
  let resultRefreshRetryTimer: ReturnType<typeof setTimeout> | null = null;
  let resultRefreshRetryFlowId = "";
  let resultRefreshRetryAttempts = 0;
  let navigationHandler = input.navigateStep;
  const projectReachableSteps = input.projectReachableSteps;

  function isCurrentRefresh(generation: number, id: string): boolean {
    return generation === refreshGeneration && flowId.value === id;
  }

  function clearResultRefreshRetry(resetAttempts = false): void {
    if (resultRefreshRetryTimer !== null) {
      clearTimeout(resultRefreshRetryTimer);
      resultRefreshRetryTimer = null;
    }
    if (resetAttempts) {
      resultRefreshRetryFlowId = "";
      resultRefreshRetryAttempts = 0;
    }
  }

  function stop(): void {
    refreshGeneration += 1;
    clearResultRefreshRetry(true);
    notices.value = [];
  }

  function scheduleResultRefreshRetry(generation: number, id: string): void {
    if (!isCurrentRefresh(generation, id) || resultRefreshRetryTimer !== null) return;
    if (resultRefreshRetryFlowId !== id) {
      resultRefreshRetryFlowId = id;
      resultRefreshRetryAttempts = 0;
    }
    if (resultRefreshRetryAttempts >= RESULT_REFRESH_MAX_RETRIES) return;
    resultRefreshRetryAttempts += 1;
    resultRefreshRetryTimer = setTimeout(() => {
      resultRefreshRetryTimer = null;
      if (!isCurrentRefresh(generation, id) || flowId.value !== resultRefreshRetryFlowId) return;
      void apply(true).catch(() => {});
    }, RESULT_REFRESH_RETRY_DELAY_MS);
  }

  async function hydrateResultProjection(id: string, generation: number): Promise<boolean> {
    const baseTracks = input.flow.value?.tracks || [];
    if (!isCurrentRefresh(generation, id)) return false;
    projectedTracks.value = baseTracks;
    if (!deps.fetchFlowResults) return true;
    let projection: { tracks?: FlowResultProjectionTrack[] } | null = null;
    try { projection = await deps.fetchFlowResults(id); } catch { return false; }
    if (!isCurrentRefresh(generation, id) || !projection?.tracks?.length) return true;
    projectedTracks.value = baseTracks.map((track) => {
      const resultTrack = projection?.tracks?.find((candidate) => candidate.id === track.id || candidate.platform === track.platform);
      if (!resultTrack) return track;
      const projectionFields = { ...resultTrack } as Record<string, unknown>;
      // The current Flow owns control state (for example a still-pausable scrape).
      // Results may add failure/message metadata, but must not turn that live
      // control into a terminal item merely because its projection arrived first.
      delete projectionFields.id;
      delete projectionFields.platform;
      delete projectionFields.status;
      delete projectionFields.stage;
      if (["failed", "unavailable", "interrupted"].includes(String(resultTrack.status || ""))) {
        projectionFields.status = resultTrack.status;
        if (resultTrack.stage !== undefined) projectionFields.stage = resultTrack.stage;
      }
      return { ...track, ...projectionFields, id: track.id, platform: track.platform };
    });
    return true;
  }

  function stateFor(id: string): RuntimeState {
    if (!runtime[id]) runtime[id] = {
      hydrated: false,
      hydratedAt: 0,
      manualHold: false,
      unlocked: new Set(["search"]),
      seenResults: new Set(),
      seenResultProjections: new Set(),
      scrapeOrderIndex: new Map(),
      screenOrderIndex: new Map(),
    };
    return runtime[id];
  }

  function resetRuntime(nextFlowId?: string) {
    if (nextFlowId && runtime[nextFlowId]) return;
    for (const key of Object.keys(runtime)) delete runtime[key];
    scrapeItems.value = [];
    screenItems.value = [];
    enabledSteps.value = new Set(["search"]);
    highestUnlocked.value = "search";
    manualHold.value = false;
    notices.value = [];
  }

  function resetNavigation(): void {
    refreshGeneration += 1;
    clearResultRefreshRetry(true);
    projectedTracks.value = null;
    resetRuntime();
    projectReachableSteps?.(null, "");
  }

  function timestamp(value: unknown): number {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") {
      const numeric = Number(value);
      if (Number.isFinite(numeric) && value.trim() !== "") return numeric;
      const parsed = Date.parse(value);
      if (Number.isFinite(parsed)) return parsed;
    }
    return 0;
  }

  function firstEntered(
    track: FlowPresentationTrack,
    snapshot: ApiTaskSnapshot,
    kind: FlowProgressKind,
  ): number {
    const snapshotRecord = snapshot as Record<string, unknown>;
    const trackRecord = track as Record<string, unknown>;
    const candidates = [
      snapshotRecord.started_at,
      snapshotRecord[`${kind}_started_at`],
      snapshotRecord.stage_started_at,
      trackRecord[`${kind}_started_at`],
      trackRecord.stage_started_at,
      trackRecord.started_at,
      trackRecord.created_at,
      trackRecord.updated_at,
    ];
    for (const candidate of candidates) {
      const parsed = timestamp(candidate);
      if (parsed) return parsed;
    }
    return 0;
  }

  function unlock(step: FlowStepId, runtimeState: RuntimeState, allowAutoAdvance: boolean) {
    const changed = !runtimeState.unlocked.has(step);
    runtimeState.unlocked.add(step);
    const order: FlowStepId[] = ["search", "screen", "results"];
    highestUnlocked.value = order.filter((step) => runtimeState.unlocked.has(step)).at(-1) || "search";
    enabledSteps.value = new Set(runtimeState.unlocked);
    projectReachableSteps?.(new Set(enabledSteps.value), flowId.value);
    if (!changed || !allowAutoAdvance || !runtimeState.hydrated || runtimeState.manualHold) return;
    navigationHandler?.(step);
  }

  async function buildItems(
    runKey: "scrape_run_id" | "screen_run_id",
    kind: FlowProgressKind,
    runtimeState: RuntimeState,
    generation: number,
    id: string,
  ): Promise<FlowProgressItem[] | null> {
    // 排队中的轨道还没有 run 身份（store_flow_claims 只在 scrape_run_id 为空时允许
    // 认领），但它已经是用户可见的现场：刚提交「全部」流程的那一瞬两条轨道都还是
    // queued，不收进来 02/03 进度面板就整块不渲染——点了开始新一轮却什么都没有。
    const candidates = tracks.value
      .map((track, index) => ({ track, index, runId: track[runKey] ? String(track[runKey]) : "" }))
      .filter((entry) => {
        if (entry.runId) return true;
        const status = String(entry.track.status || "");
        if (kind === "screen" && hasVisibleJobs(entry.track)) return true;
        // 整条线还在活动态：排队中的两段都还没有现场，整条线都收；进行中的轨道
        // 只要本段就是线当前所在那一段就必须收——交接窗口里 mark_scrape_complete
        // 不带 screen_run_id（绑定发生在 begin_ai），此时 03 若因为「还没有 run」
        // 整块不渲染，按钮却仍按轨道态给暂停/停止，用户看到的是一条没有现场的线。
        if (ACTIVE_TRACK_STATUSES.includes(status)) {
          return status === "queued" || ownsTrackState(kind, entry.track);
        }
        if (!isTrackFailure(status)) return false;
        return ownsTrackState(kind, entry.track);
      });

    const fetched: Array<FlowProgressItem & { order: number; index: number }> = [];
    for (const entry of candidates) {
      // 单条 run 的状态读取会真实抛错：webui/task_state_api.py 对 run 不存在或画像
      // 不符返回 404，webui/src/api.ts 对非 2xx 抛 ApiError。一条读失败只降级那一张卡
      // （走已有的 stored/fallback 链），绝不让异常冒到 useDiscoveryFlowCoordinator
      // 的 .catch(() => {}) 里被吞掉——那样后半段的卡与解锁判定整块不执行，
      // 03/04 会长期锁死且用户无任何提示。与 hydrateResultProjection 同一口径。
      let remoteSnapshot: ApiTaskSnapshot | null | undefined = null;
      if (entry.runId) {
        try {
          remoteSnapshot = await deps.fetchTaskState?.(entry.runId, entry.track.platform);
        } catch {
          remoteSnapshot = null;
        }
      }
      if (!isCurrentRefresh(generation, id)) return null;
      const storedSnapshot = entry.runId ? snapshotForTrack(entry.track, entry.runId) : null;
      const hasOwnEvidence = Boolean(remoteSnapshot || storedSnapshot);
      let snapshot = remoteSnapshot || storedSnapshot || fallbackSnapshot(entry.track);
      // 轨道级状态归「线当前所在的那一段」（stage 与交接证据一起判）；不是当前段的
      // 卡只按本段口径显示。本段既不是当前段、又没有自己的证据时，fallback 抄的是
      // 轨道状态，直接用它就等于替整条线背「已中断/已停止/失败」——这种卡只说本段口径。
      const ownsLineState = ownsTrackState(kind, entry.track);
      if (!ownsLineState && !hasOwnEvidence) {
        // 「本段没有证据」有两种，只有一种配得上中性口径：
        // - 这一段从来没开始过（连 run 身份都没有）：说「等待开始」是事实。
        // - 这一段已经有 run 身份、只是这一轮读不到状态：说「等待开始」就把跑过的
        //   段说成还没开始，清空 progress 后卡体还会补一句「正在准备任务…」，
        //   同一张卡自相矛盾。换成读不到状态的兜底口径，头部与卡体同说「状态更新中」，
        //   也仍然不替整条线背「已中断/已停止/失败」。
        snapshot = {
          ...snapshot,
          status: entry.runId ? UNREADABLE_STAGE_STATUS : NEUTRAL_STAGE_STATUS,
          progress: {},
          error_code: undefined,
          reason: undefined,
          error: undefined,
        } as ApiTaskSnapshot;
      }
      const carriesLineState = ownsLineState
        || (hasOwnEvidence && STAGE_IN_FLIGHT_STATUSES.includes(String(snapshot.status || "")));
      const trackStatus = String(entry.track.status || snapshot.status || "unknown");
      const stageStatus = stageStatusOf(String(snapshot.status || ""), trackStatus, carriesLineState);
      const displaySnapshot = snapshot.status === stageStatus
        ? snapshot
        : ({ ...snapshot, status: stageStatus } as ApiTaskSnapshot);
      // 本行的收尾 run：AI 段用自己的 screen_run_id；交接窗口里 AI 段还没有 run 身份时，
      // 这一条线唯一已有的 run 就是抓取 run，收尾照旧落在那条 run 上。
      const finishRunId = entry.runId
        || (kind === "screen" ? String(entry.track.scrape_run_id || "") : "");
      const actionBar = deriveTrackActionBar({
        stage: kind,
        status: trackStatus,
        runId: entry.runId,
        finishRunId,
      });
      fetched.push({
        platform: entry.track.platform,
        trackId: entry.track.id,
        kind,
        stage: kind,
        status: trackStatus,
        runId: entry.runId,
        snapshot: displaySnapshot,
        statusLabel: stageStatusLabel(stageStatus, displaySnapshot.integrity?.conclusion),
        carriesLineState,
        action: actionBar.action,
        showFinishSave: actionBar.showFinishSave,
        showCancel: actionBar.showCancel,
        cancelLabel: actionBar.cancelLabel,
        finishRunId,
        finishTestId: `parallel-track-${entry.track.platform}-finish-save`,
        cancelTestId: `parallel-track-${entry.track.platform}-cancel`,
        enteredAt: firstEntered(entry.track, snapshot, kind),
        order: 0,
        index: entry.index,
      });
    }

    const orderIndex = kind === "scrape"
      ? runtimeState.scrapeOrderIndex
      : runtimeState.screenOrderIndex;
    const pending = fetched.filter((entry) => !orderIndex.has(entry.trackId));
    pending.sort((left, right) => {
      if (left.enteredAt && right.enteredAt && left.enteredAt !== right.enteredAt) {
        return left.enteredAt - right.enteredAt;
      }
      return left.index - right.index;
    });
    let nextOrder = 0;
    for (const value of orderIndex.values()) {
      if (value >= nextOrder) nextOrder = value + 1;
    }
    for (const entry of pending) {
      orderIndex.set(entry.trackId, nextOrder++);
    }
    fetched.forEach((entry) => { entry.order = orderIndex.get(entry.trackId)!; });
    fetched.sort((left, right) => left.order - right.order);
    return fetched.map(({ order: _order, index: _index, ...item }) => item);
  }

  type ResultChange = {
    id: string;
    platform: Platform;
    runId: string;
    notify: boolean;
    projectionKey: string;
    signature: string;
  };

  function collectNewResults(runtimeState: RuntimeState): ResultChange[] {
    const next: ResultChange[] = [];
    for (const track of tracks.value) {
      const runId = track.result_run_id;
      const record = track as Record<string, unknown>;
      const jobs = Array.isArray(record.jobs)
        ? record.jobs.map((job) => {
          const value = job && typeof job === "object" ? job as Record<string, unknown> : {};
          return [value.platform_job_id, value.job_id, value.id, value.canonical_url, value.verdict].map((item) => String(item || "")).join(":");
        })
        : [];
      const dropped = Array.isArray(record.dropped) ? record.dropped.length : 0;
      const projection = JSON.stringify([
        String(runId || ""), String(record.status || ""), String(record.message || ""),
        String(record.reason || ""), Boolean(record.unfinished_ai_screening), jobs, dropped,
      ]);
      const hasProjection = Boolean(runId || jobs.length || dropped || record.message || record.reason || record.unfinished_ai_screening || record.status === "failed");
      const projectionKey = `${flowId.value}:${track.platform}:${projection}`;
      if (!hasProjection || runtimeState.seenResultProjections.has(projectionKey)) continue;
      const stableRunId = String(runId || `projection-${projection}`);
      const signature = `${flowId.value}:${track.platform}:${stableRunId}`;
      if (runtimeState.seenResults.has(signature)) continue;
      next.push({
        id: signature,
        platform: track.platform,
        runId: stableRunId,
        projectionKey,
        signature,
        // A projection without a real result run is useful for in-place
        // refresh (including failed/unfinished tracks), but is not a
        // successful result arrival and must not notify the user as one.
        notify: Boolean(runId),
      });
    }
    return next;
  }

  function commitResultChanges(runtimeState: RuntimeState, changes: ResultChange[]): ResultChange[] {
    for (const change of changes) {
      runtimeState.seenResultProjections.add(change.projectionKey);
      runtimeState.seenResults.add(change.signature);
    }
    return changes;
  }

  async function apply(isRetry = false): Promise<void> {
    if (!isRetry) clearResultRefreshRetry(true);
    const generation = ++refreshGeneration;
    const id = flowId.value;
    if (!id) {
      if (!isCurrentRefresh(generation, id)) return;
      projectedTracks.value = null;
      resetRuntime();
      projectReachableSteps?.(null, "");
      return;
    }
    const projectionLoaded = await hydrateResultProjection(id, generation);
    if (!isCurrentRefresh(generation, id)) return;
    if (!projectionLoaded) scheduleResultRefreshRetry(generation, id);
    resetRuntime(id);
    const runtimeState = stateFor(id);
    if (!runtimeState.hydrated) runtimeState.hydratedAt = Date.now();
    // A page-level restore flag is not a Flow lifecycle boundary: an existing
    // Flow may hydrate after the page snapshot has already been restored, while
    // a new Flow on the same page must still get its own baseline. Runtime
    // hydration is therefore the sole source for first-mount suppression.
    const isFirstHydration = !runtimeState.hydrated;
    const hadAi = tracks.value.some((track) => track.screen_run_id);
    // A Flow may finish scraping before the AI Track is created (for example
    // after a refresh or a legacy Flow migration).  The scrape result is then
    // the authoritative hand-off point for Step 3; waiting for screen_run_id
    // would make the reachability guard circular and leave AI screening locked.
    const hadCompletedScrape = tracks.value.some((track) => (
      Boolean(track.scrape_run_id)
      && !track.screen_run_id
      && SCRAPE_READY_STATUSES.has(String(track.status || ""))
    ));
    const hadFailedAi = tracks.value.some((track) => (
      isTrackFailure(String(track.status || "")) && ownsTrackState("screen", track)
    ));
    const hadResult = tracks.value.some((track) => track.result_run_id || hasVisibleJobs(track));
    const nextScrapeItems = await buildItems("scrape_run_id", "scrape", runtimeState, generation, id);
    if (!isCurrentRefresh(generation, id) || !nextScrapeItems) return;
    scrapeItems.value = nextScrapeItems;
    const nextScreenItems = await buildItems("screen_run_id", "screen", runtimeState, generation, id);
    if (!isCurrentRefresh(generation, id) || !nextScreenItems) return;
    screenItems.value = nextScreenItems;
    if (!isCurrentRefresh(generation, id)) return;
    // A failed AI track with no result is still a recoverable hand-off to
    // Step 3.  Once the result projection already has jobs/result data, keep
    // the existing result-only reachability instead of reopening the screen.
    if (hadAi || hadCompletedScrape || (hadFailedAi && !hadResult)) {
      unlock("screen", runtimeState, !isFirstHydration);
    }
    if (hadResult) unlock("results", runtimeState, !isFirstHydration);
    const newResults = collectNewResults(runtimeState);
    if (isFirstHydration) {
      // Existing result runs are the baseline after refresh; do not emit ghosts
      // or reload the result workspace until a new signature appears.
    } else if (deps.refreshResults && newResults.length) {
      if (!isCurrentRefresh(generation, id)) return;
      try {
        await deps.refreshResults(true);
      } catch {
        if (isCurrentRefresh(generation, id)) scheduleResultRefreshRetry(generation, id);
        return;
      }
      if (!isCurrentRefresh(generation, id)) return;
      resultRefreshRetryAttempts = 0;
    }
    if (!isCurrentRefresh(generation, id)) return;
    const committedResults = commitResultChanges(runtimeState, newResults);
    if (!isFirstHydration && committedResults.length) {
      const readyNotices = committedResults
        .filter((entry) => entry.notify)
        .map(({ id: noticeId, platform, runId }) => ({ id: noticeId, platform, runId }));
      if (readyNotices.length) {
        notices.value.push(...readyNotices);
        if (isRetry) deps.onNoticeReady?.();
      }
    }
    runtimeState.hydrated = true;
  }

  function setManualHold(hold: boolean): void {
    const runtimeState = stateFor(flowId.value);
    runtimeState.manualHold = hold;
    manualHold.value = hold;
    if (!hold) {
      const target = highestUnlocked.value;
      if (!runtimeState.hydrated || runtimeState.manualHold) return;
      projectReachableSteps?.(new Set(enabledSteps.value), flowId.value);
      navigationHandler?.(target);
    }
  }

  function setNavigationHandler(handler: ((step: FlowStepId) => void) | undefined): void {
    navigationHandler = handler;
  }

  function consumeNotice(): { id: string; platform: Platform; runId: string } | null {
    return notices.value.shift() || null;
  }

  const runtimeFor = computed(() => stateFor(flowId.value));
  return {
    flowId,
    hydrated: computed(() => runtimeFor.value.hydrated),
    manualHold,
    unlockedSteps: enabledSteps,
    highestUnlocked,
    scrapeItems,
    screenItems,
    setManualHold,
    setNavigationHandler,
    resetNavigation,
    stop,
    consumeNotice,
    refresh: apply,
  };
}
