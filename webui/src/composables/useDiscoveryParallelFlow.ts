import { computed, getCurrentInstance, onBeforeUnmount, onMounted, reactive, ref, type Ref } from "vue";
import type { ConditionSnapshotV2, LocationCondition, Platform, PlatformFilterSchema, PlatformFilterValues, UnifiedFilterValues } from "../types";
import { apiRequest, errorMessage } from "../api";
import {
  crossPlatformDedupeEnabled,
  type OneClickFilterGroup,
} from "../components/OneClickScreenDialog.vue";
import { ACTIVE_TRACK_STATUSES, buildSearchScriptParams, platformLabel } from "../discovery";
import {
  applyUnifiedToPlatforms,
  applyUnifiedFieldToPlatforms,
  buildConditionSnapshot,
  normalizePlatformValues,
  normalizeUnifiedValues,
  projectResumeSemantic,
  resolveMappedValues,
  validateConditionSnapshot,
  UNIFIED_FILTER_FIELDS,
} from "../parallelFilterMapping";
import type { FlowResultProjectionTrack } from "./useDiscoveryFlowPresentation";

export type ParallelSelection = "all" | Platform;

export interface ParallelTrackState {
  id: string;
  flow_id: string;
  platform: Platform;
  scrape_run_id?: string | null;
  screen_run_id?: string | null;
  result_run_id?: string | null;
  confirmed_filters_snapshot?: Record<string, unknown> | ConditionSnapshotV2;
  submission_snapshot?: {
    script_params?: Record<string, unknown>;
    scope_digest?: string;
    auto_screen?: boolean;
    auto_screen_fields?: Record<string, unknown>;
    auto_screen_profile?: string;
    auto_screen_facts?: Record<string, unknown>;
    profile_summary?: string;
    profile_facts?: Record<string, unknown>;
    cross_platform_dedupe?: boolean;
  } | null;
  status: string;
  stage: string;
  error_code?: string | null;
  reason?: string | null;
  [key: string]: unknown;
}

type ApiTaskSnapshot = Record<string, unknown>;

export interface FlowResultsProjection {
  flow_id?: string;
  profile_id?: string;
  selection?: ParallelSelection;
  status?: string;
  tracks?: FlowResultProjectionTrack[];
  jobs?: Array<Record<string, unknown>>;
  screened_count?: number;
  [key: string]: unknown;
}

export interface ParallelFlowState {
  id: string;
  profile_id: string;
  selection: ParallelSelection;
  status?: string;
  tracks: ParallelTrackState[];
  [key: string]: unknown;
}

const ACTIVE_FLOW_STATUSES = new Set(["queued", "running", "paused", "interrupted"]);
const RESETTABLE_ACTIVE_FLOW_STATUSES = new Set(["paused", "interrupted"]);
// 活体 worker 的词汇只有一份，落在树干阶段卡口径（排队中 / 运行中）。
const LIVE_WORKER_FLOW_STATUSES = new Set<string>(ACTIVE_TRACK_STATUSES);

/**
 * A Flow owns the round while either its envelope or one of its Tracks is
 * active.  The envelope check matters during the short queued/running window
 * in which the server has not materialized Track rows yet.
 *
 * 这是「本轮还没结束」的谓词（锁范围、锁提交新任务），不是「现在有活体任务在跑」；
 * 判活一律用 flowHasLiveWorker。
 */
export function isActiveParallelFlow(flow: ParallelFlowState | null | undefined): boolean {
  return Boolean(
    flow && (
      ACTIVE_FLOW_STATUSES.has(String(flow.status || ""))
      || flow.tracks.some((track) => ACTIVE_FLOW_STATUSES.has(String(track.status || "")))
    ),
  );
}

/**
 * 本轮此刻是否真的有 worker 在跑：只有「排队中 / 运行中」算活体。
 *
 * 「已中断」「已暂停」说的是同一件事的另一半——这一轮还没结束（继续锁住本轮范围、
 * 不给改平台、不给提交新任务），但没有任何活体任务在跑。判活（是否允许占用 02/03
 * 实时画面、是否拒绝把本轮结果交给 04）必须用这个谓词，不能用 isActiveParallelFlow，
 * 否则一条被服务重启打断的流程会把结果页永久关在门外。
 */
export function flowHasLiveWorker(flow: ParallelFlowState | null | undefined): boolean {
  return Boolean(
    flow && (
      LIVE_WORKER_FLOW_STATUSES.has(String(flow.status || ""))
      || flow.tracks.some((track) => LIVE_WORKER_FLOW_STATUSES.has(String(track.status || "")))
    ),
  );
}

/**
 * The server may have created a Flow after the user has already switched
 * profiles.  That Flow is real durable state, but it must never be presented
 * as the newly selected profile's active Flow.  Keep the created payload on a
 * distinct error so the caller can expose it instead of silently dropping it.
 */
export class ParallelFlowStartStaleError extends Error {
  readonly code = "FLOW_START_STALE" as const;

  constructor(
    readonly profileId: string,
    readonly flow: ParallelFlowState,
  ) {
    super("流程启动意图已过期，旧画像流程已创建");
    this.name = "ParallelFlowStartStaleError";
  }
}

export function isParallelFlowStartStaleError(error: unknown): error is ParallelFlowStartStaleError {
  return Boolean(error && typeof error === "object" && (error as { code?: unknown }).code === "FLOW_START_STALE");
}

type ApiRequest = typeof apiRequest;

/**
 * 动作失败原因的用户可读口径：后端已给中文原因时原样使用；只剩内部机器码
 * （error_code / 异常名 / 字段名）时回落到中文兜底，不把内部码递到界面上。
 */
function readableActionReason(reason: unknown, fallback: string): string {
  const message = errorMessage(reason, fallback);
  return /[\u4e00-\u9fff]/.test(message) ? message : fallback;
}

interface ParallelFlowOptions {
  profileId: string | Ref<string | null | undefined> | (() => string | null | undefined);
  request?: ApiRequest;
  pollIntervalMs?: number;
  /**
   * 轨道动作被拒绝时的一次性用户提示出口（文案 = 平台名 + 用户可读原因）。
   * 本 composable 不认识通知通道，由装配处接到界面；一次主动操作至多回调一次。
   */
  onActionError?: (message: string) => void;
}

interface ParallelSearchContext {
  keywords: string[];
  cities: string[];
  locations: Partial<Record<Platform, LocationCondition[]>>;
  profileSummary?: string;
  profileFacts?: Record<string, unknown>;
  selection?: ParallelSelection;
  resumeSemantic?: Record<string, unknown> | null;
}

type ConditionSource = "uninitialized" | "resume" | "restored" | "edited";

interface PrepareDialogOptions {
  isCurrent?: () => boolean;
}

export function useDiscoveryParallelFlow(options: ParallelFlowOptions) {
  const request = options.request || apiRequest;
  function currentProfileId(): string {
    const source = options.profileId;
    const value = typeof source === "function"
      ? source()
      : source && typeof source === "object" && "value" in source
        ? source.value
        : source;
    return String(value || "").trim();
  }
  let observedProfileId = currentProfileId();
  let profileGeneration = 0;
  function profileSnapshot(): { id: string; generation: number } {
    const id = currentProfileId();
    if (id !== observedProfileId) {
      observedProfileId = id;
      profileGeneration += 1;
    }
    return { id, generation: profileGeneration };
  }
  function isCurrentProfile(profileId: string, generation: number): boolean {
    const current = profileSnapshot();
    return current.id === profileId && current.generation === generation;
  }
  const flow = ref<ParallelFlowState | null>(null);
  const loading = ref(false);
  const operatingPlatform = ref<Platform | null>(null);
  const error = ref("");
  // A failed read leaves the last known Flow available for context, but it is
  // no longer safe to mutate.  Consumers use this bit to keep the old view
  // read-only until an authoritative refresh succeeds.
  const stale = ref(false);
  const platformValues = reactive<Record<Platform, PlatformFilterValues>>({
    boss: {},
    zhilian: {},
  });
  const unifiedValues = reactive<UnifiedFilterValues>({
    salary: [], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [],
  });
  const polling = ref(false);
  const available = ref(false);
  const platformGroups = reactive<Partial<Record<Platform, OneClickFilterGroup[]>>>({
    boss: [],
    zhilian: [],
  });
  const platformSchemas = reactive<Partial<Record<Platform, PlatformFilterSchema>>>({
    boss: undefined,
    zhilian: undefined,
  });
  const searchContext = ref<ParallelSearchContext | null>(null);
  // Resume semantic projection is a one-time initializer. Once a V2 snapshot
  // or a user edit has supplied condition state, reopening the dialog must not
  // derive a fresh platform draft from the old resume semantic.
  const conditionSource = ref<ConditionSource>("uninitialized");
  const flowResultsCache = new Map<string, { flow: ParallelFlowState | null; promise: Promise<FlowResultsProjection | null> }>();
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let refreshGeneration = 0;
  let prepareGeneration = 0;

  const tracks = computed(() => ({
    boss: flow.value?.tracks.find((track) => track.platform === "boss") || null,
    zhilian: flow.value?.tracks.find((track) => track.platform === "zhilian") || null,
  }));
  const trackList = computed(() => Object.values(tracks.value).filter(Boolean) as ParallelTrackState[]);

  function isFlowOwnedScrapeTask(taskId: string): boolean {
    const current = flow.value;
    return Boolean(
      taskId
      && current?.profile_id === currentProfileId()
      && current.selection === "all"
      && current.tracks.some((track) => track.scrape_run_id === taskId),
    );
  }

  const hasActiveTrack = computed(() => isActiveParallelFlow(flow.value));
  // 本轮未结束（hasActiveTrack）与此刻有活体 worker（hasLiveWorker）是两件事：
  // 前者锁范围与提交，后者才决定实时画面与结果接回。
  const hasLiveWorker = computed(() => flowHasLiveWorker(flow.value));

  const canStartNewRound = computed(() => !stale.value && !hasActiveTrack.value);

  // A paused/restart-interrupted Flow has no active worker that must remain
  // on the page; reset can send its durable run ids through the cancellation
  // contract and close the stale Track.  Queued/running envelopes remain
  // disabled in the UI, while the reset path still verifies them server-side.
  const canResetNewRound = computed(() => {
    if (stale.value || !flow.value) return !stale.value;
    const statuses = [
      flow.value.status,
      ...flow.value.tracks.map((track) => track.status),
    ].map((status) => String(status || "")).filter(Boolean);
    return !statuses.some((status) => (
      ACTIVE_FLOW_STATUSES.has(status)
      && !RESETTABLE_ACTIVE_FLOW_STATUSES.has(status)
    ));
  });

  function clearFlowStatus(): void {
    stale.value = false;
    error.value = "";
  }

  function clearFlowError(): void {
    // Mode changes may leave a stale Flow in place for read-only context.  Do
    // not unlock it here; only an authoritative refresh may clear `stale`.
    error.value = "";
  }

  function markFlowReadFailure(reason: unknown): void {
    stale.value = true;
    error.value = errorMessage(reason, "流程状态读取失败");
  }

  function setPlatformFilters(
    platform: Platform,
    values: Record<string, string[]>,
  ) {
    platformValues[platform] = normalizePlatformValues(platform, values);
    conditionSource.value = "edited";
  }

  function setUnifiedFilters(field: keyof UnifiedFilterValues, values: string[]) {
    if (!UNIFIED_FILTER_FIELDS.includes(field)) return;
    const next = applyUnifiedFieldToPlatforms(field, values, platformValues, platformSchemas);
    unifiedValues[field] = normalizeUnifiedValues({ [field]: values })[field];
    platformValues.boss = next.boss;
    platformValues.zhilian = next.zhilian;
    conditionSource.value = "edited";
  }

  function createConditionSnapshot(
    finalValues: Partial<Record<Platform, PlatformFilterValues>> = platformValues,
  ): ConditionSnapshotV2 {
    return buildConditionSnapshot(unifiedValues, finalValues, platformSchemas);
  }

  function restoreConditionSnapshot(snapshot: ConditionSnapshotV2): void {
    const validated = validateConditionSnapshot(snapshot);
    Object.assign(unifiedValues, validated.unifiedValues);
    for (const platform of ["boss", "zhilian"] as const) {
      platformValues[platform] = {
        ...(validated.platformValues[platform] || {}),
      };
    }
    conditionSource.value = "restored";
  }

  function resetConditionState(): void {
    prepareGeneration += 1;
    for (const field of UNIFIED_FILTER_FIELDS) unifiedValues[field] = [];
    platformValues.boss = {};
    platformValues.zhilian = {};
    searchContext.value = null;
    conditionSource.value = "uninitialized";
  }

  function clearPolling() {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    polling.value = false;
  }

  function groupsFromSchema(response: unknown): OneClickFilterGroup[] {
    const fields = (response as { fields?: unknown } | null)?.fields;
    if (!Array.isArray(fields)) return [];
    return fields
      .filter((field): field is {
        key: string;
        label: string;
        multiple?: boolean;
        options?: unknown[];
      } => Boolean(field && typeof field === "object" && typeof (field as { key?: unknown }).key === "string"))
      .map((field) => {
        const options = Array.isArray(field.options) ? field.options : [];
        const sentinel = options.find((item) => {
          const option = item as { value?: unknown; label?: unknown };
          return option.value === "0" || option.label === "不限" || option.label === "全部";
        }) as { value?: unknown; label?: unknown } | undefined;
        return {
          key: field.key,
          label: field.label,
          multiple: field.multiple !== false,
          sentinel: sentinel
            ? { label: String(sentinel.label || "不限"), code: String(sentinel.value || "0") }
            : null,
          options: options
            .map((item) => item as { value?: unknown; label?: unknown })
            .filter((item) => item.value != null && item.label != null)
            .filter((item) => !sentinel || String(item.value) !== String(sentinel.value))
            .map((item) => [String(item.label), String(item.value)] as [string, string]),
        };
      })
      .filter((group) => group.options.length || group.sentinel);
  }

  async function loadPlatformGroups() {
    await Promise.all(([
      "boss",
      "zhilian",
    ] as Platform[]).map(async (platform) => {
      try {
        const response = await request<unknown>(
          `/api/filter-labels?platform=${encodeURIComponent(platform)}`,
        );
        if (response && typeof response === "object" && Array.isArray((response as { fields?: unknown }).fields)) {
          platformSchemas[platform] = response as PlatformFilterSchema;
        } else {
          delete platformSchemas[platform];
        }
        platformGroups[platform] = groupsFromSchema(response);
      } catch {
        delete platformSchemas[platform];
        platformGroups[platform] = [];
      }
    }));
    return platformGroups;
  }

  function syncDrafts(values: Partial<Record<Platform, Record<string, string[]>>>) {
    for (const platform of ["boss", "zhilian"] as Platform[]) {
      if (values[platform]) setPlatformFilters(platform, values[platform] || {});
    }
  }

  async function prepareDialog(
    values: Partial<Record<Platform, Record<string, string[]>>> = {},
    context?: ParallelSearchContext,
    options: PrepareDialogOptions = {},
  ) {
    const generation = ++prepareGeneration;
    const profile = profileSnapshot();
    const expectedRefreshGeneration = refreshGeneration + 1;
    const isCurrentPreparation = () => Boolean(
      profile.id
      && generation === prepareGeneration
      && refreshGeneration === expectedRefreshGeneration
      && isCurrentProfile(profile.id, profile.generation)
      && (!options.isCurrent || options.isCurrent()),
    );
    await Promise.allSettled([refresh({ isCurrent: isCurrentPreparation }), loadPlatformGroups()]);
    if (!isCurrentPreparation()) {
      return platformGroups;
    }
    searchContext.value = context || null;
    if (context?.selection === "all") {
      if (conditionSource.value === "uninitialized" && context.resumeSemantic) {
        const projected = projectResumeSemantic(context.resumeSemantic, platformSchemas);
        Object.assign(unifiedValues, projected.unifiedValues);
        platformValues.boss = projected.platformValues.boss;
        platformValues.zhilian = projected.platformValues.zhilian;
        conditionSource.value = "resume";
      }
    } else {
      syncDrafts(values);
    }
    return platformGroups;
  }

  async function refresh(options: { isCurrent?: () => boolean } = {}) {
    const profile = profileSnapshot();
    const generation = ++refreshGeneration;
    const profileId = profile.id;
    if (!profileId) return null;
    try {
      const response = await request<{ flow?: ParallelFlowState | null }>(
        `/api/flows/current?profile_id=${encodeURIComponent(profileId)}`,
      );
      if (generation !== refreshGeneration || !isCurrentProfile(profileId, profile.generation)
        || options.isCurrent && !options.isCurrent()) return flow.value;
      // 能力由接口成功响应确认；flow 是否存在只表示当前是否有可恢复流程。
      available.value = true;
      const restoredFlow = response.flow || null;
      flow.value = restoredFlow;
      await hydrateTrackSnapshots();
      if (generation !== refreshGeneration || !isCurrentProfile(profileId, profile.generation)
        || options.isCurrent && !options.isCurrent()) {
        if (options.isCurrent && !options.isCurrent() && flow.value === restoredFlow) {
          clearPolling();
          flow.value = null;
        }
        return flow.value;
      }
      if (!hasActiveTrack.value) clearPolling();
      clearFlowStatus();
      return flow.value;
    } catch (reason: unknown) {
      if (generation === refreshGeneration && isCurrentProfile(profileId, profile.generation)
        && (!options.isCurrent || options.isCurrent())) {
        markFlowReadFailure(reason);
      }
      throw reason;
    }
  }

  async function hydrateTrackSnapshots() {
    const profileId = currentProfileId();
    if (!profileId) return;
    const targetFlow = flow.value;
    const runs = (targetFlow?.tracks || []).flatMap((track) => {
      const entries: Array<{ key: string; runId: string; trackId: string }> = [];
      for (const field of ["scrape_run_id", "screen_run_id"] as const) {
        const runId = track[field];
        if (runId) entries.push({ key: `${runId}:snapshot`, runId: String(runId), trackId: track.id });
      }
      return entries;
    });
    if (!runs.length) return;
    await Promise.allSettled(runs.map(async (entry) => {
      const snapshot = await request<ApiTaskSnapshot>(`/api/task-state/${encodeURIComponent(entry.runId)}?profile_id=${encodeURIComponent(profileId)}`);
      if (profileId !== currentProfileId() || flow.value !== targetFlow) return;
      const track = targetFlow?.tracks.find((item) => item.id === entry.trackId);
      if (track) track[entry.key] = snapshot;
    }));
  }

  async function fetchTaskState(runId: string): Promise<ApiTaskSnapshot | null> {
    const profileId = currentProfileId();
    if (!runId || !profileId) return null;
    return request<ApiTaskSnapshot>(
      `/api/task-state/${encodeURIComponent(runId)}?profile_id=${encodeURIComponent(profileId)}`,
    );
  }

  async function fetchFlowResults(flowId?: string): Promise<FlowResultsProjection | null> {
    const id = String(flowId || flow.value?.id || "").trim();
    const profileId = currentProfileId();
    if (!id || !profileId) return null;
    const cached = flowResultsCache.get(id);
    if (cached?.flow === flow.value) return cached.promise;
    const promise = request<{ results?: FlowResultsProjection }>(
      `/api/flows/${encodeURIComponent(id)}/results?profile_id=${encodeURIComponent(profileId)}`,
    ).then((response) => response.results || null);
    const entry = { flow: flow.value, promise };
    flowResultsCache.set(id, entry);
    void promise.catch(() => {
      if (flowResultsCache.get(id) === entry) flowResultsCache.delete(id);
    });
    return promise;
  }

  function startPolling() {
    clearPolling();
    polling.value = true;
    void refresh().catch((reason: unknown) => {
      if (!error.value) error.value = errorMessage(reason, "流程状态读取失败");
    });
    pollTimer = setInterval(() => {
      void refresh().catch((reason: unknown) => {
        if (!error.value) error.value = errorMessage(reason, "流程状态读取失败");
      });
    }, Math.max(250, options.pollIntervalMs || 2000));
  }

  async function start(
    selection: ParallelSelection = "all",
    startKey?: string,
    overrides?: { snapshot?: ConditionSnapshotV2 },
  ) {
    if (loading.value) throw new Error("流程启动中");
    const profile = profileSnapshot();
    const platforms: Platform[] = selection === "all" ? ["boss", "zhilian"] : [selection];
    const profileId = profile.id;
    if (!profileId) throw new Error("当前画像不可用");
    if (!canStartNewRound.value) {
      throw new Error("当前流程仍在运行或暂停");
    }
    loading.value = true;
    error.value = "";
    try {
      const crossPlatformDedupe = crossPlatformDedupeEnabled();
      const frozenSnapshot = selection === "all"
        ? (overrides?.snapshot ? validateConditionSnapshot(overrides.snapshot) : createConditionSnapshot())
        : null;
      if (frozenSnapshot) {
        for (const platform of platforms) {
          setPlatformFilters(platform, frozenSnapshot.platformValues[platform] || {});
        }
      }
      const response = await request<{ flow: ParallelFlowState }>("/api/flows", {
        method: "POST",
        json: {
          profile_id: profileId,
          selection,
          start_key: startKey,
          confirmed_filters: Object.fromEntries(
            platforms.map((platform) => [platform, selection === "all"
              ? frozenSnapshot
              : platformValues[platform]]),
          ),
        },
      });
      if (!isCurrentProfile(profileId, profile.generation)) {
        throw new ParallelFlowStartStaleError(profileId, response.flow);
      }
      flow.value = response.flow;
      const context = searchContext.value;
      if (context) {
        await Promise.all((platforms).map(async (platform) => {
          if (!isCurrentProfile(profileId, profile.generation)) return;
          try {
            await request(`/api/execute-search`, {
              method: "POST",
              json: {
                profile_id: profileId,
                flow_id: response.flow.id,
                platform,
                script_params: buildSearchScriptParams(
                  context.keywords || [],
                  context.cities || [],
                  context.locations[platform] || [],
                ),
                profile_summary: context.profileSummary || "",
                profile_facts: context.profileFacts || {},
                auto_screen: true,
                auto_screen_fields: selection === "all"
              ? frozenSnapshot?.platformValues[platform] || {}
              : platformValues[platform],
                auto_screen_profile: context.profileSummary || "",
                auto_screen_facts: context.profileFacts || {},
                cross_platform_dedupe: crossPlatformDedupe,
              },
            });
          } catch (reason: unknown) {
            // The API persists the Track failure before returning its error.
            // Keep the client message safe and let the following refresh read
            // that durable state instead of replacing it with a local guess.
            error.value = `${platformLabel(platform)}：${errorMessage(
              reason,
              "平台任务提交失败",
            )}`;
          }
        }));
        if (!isCurrentProfile(profileId, profile.generation)) {
          if (flow.value === response.flow) flow.value = null;
          throw new ParallelFlowStartStaleError(profileId, response.flow);
        }
        await refresh();
      }
      if (!isCurrentProfile(profileId, profile.generation)) {
        if (flow.value === response.flow) flow.value = null;
        throw new ParallelFlowStartStaleError(profileId, response.flow);
      }
      startPolling();
      return flow.value;
    } catch (reason: unknown) {
      error.value = errorMessage(reason, "流程启动失败");
      throw reason;
    } finally {
      loading.value = false;
    }
  }

  async function operate(platform: Platform, action: "pause" | "resume" | "stop") {
    if (!flow.value) throw new Error("当前没有流程");
    const profileId = currentProfileId();
    if (!profileId) throw new Error("当前画像不可用");
    if (stale.value) {
      const message = error.value || "流程状态暂不可确认，当前仅可查看";
      throw new Error(message);
    }
    operatingPlatform.value = platform;
    error.value = "";
    try {
      const response = await request<{ flow: ParallelFlowState }>(
        `/api/flows/${encodeURIComponent(flow.value.id)}/tracks/${platform}/${action}`,
        {
          method: "POST",
          json: { profile_id: profileId },
        },
      );
      flow.value = response.flow;
      return flow.value;
    } catch (reason: unknown) {
      const actionError = errorMessage(reason, "流程操作失败");
      // 动作被拒绝先给用户一句话提示（pause / resume / stop 同一条路径），
      // 再补权威状态；异常继续上抛，调用方保留原有失败处理。
      options.onActionError?.(`${platformLabel(platform)}：${readableActionReason(reason, "流程操作失败")}`);
      try {
        await refresh();
        // The action error remains useful even when the authoritative refresh
        // succeeds: the user still needs to know why the action was rejected.
        error.value = actionError;
      } catch (refreshReason: unknown) {
        // Preserve both facts when neither the action nor the follow-up read
        // can establish the server's authoritative state.
        stale.value = true;
        error.value = `${actionError}；状态刷新失败：${errorMessage(refreshReason, "流程状态读取失败")}`;
      }
      throw reason;
    } finally {
      operatingPlatform.value = null;
    }
  }

  /**
   * 轨道行上传来的动作出口：既有动作条的 kind 落到后端既有的三种轨道操作上。
   * 树干不认识任何平台，也不新造端点——「终止本轨」就是这条线的 stop。
   */
  async function operateTrack(
    platform: Platform,
    action: "pause" | "continue" | "start" | "recrawl" | "pause-recrawl" | "continue-recrawl" | "pause-scrape" | "continue-scrape" | "cancel",
  ) {
    if (action === "cancel") return operate(platform, "stop");
    if (action === "pause" || action === "pause-scrape") return operate(platform, "pause");
    if (action === "continue" || action === "continue-scrape") return operate(platform, "resume");
    return null;
  }

  function restore(next: ParallelFlowState | null) {
    clearPolling();
    try {
      if (next && (!Array.isArray(next.tracks) || next.tracks.some((track) => !track || typeof track !== "object"))) {
        throw new Error("流程恢复失败：流程轨道数据无效");
      }
      flow.value = next;
      if (next?.selection === "all") {
        let restoredSnapshot = false;
        for (const platform of ["boss", "zhilian"] as Platform[]) {
          const track = next.tracks.find((item) => item.platform === platform);
          const snapshot = track?.confirmed_filters_snapshot;
          if (!restoredSnapshot && snapshot && typeof snapshot === "object"
              && (snapshot as Record<string, unknown>).snapshotVersion === 2) {
            try {
              restoreConditionSnapshot(snapshot as ConditionSnapshotV2);
              restoredSnapshot = true;
            } catch {
              // Preserve the existing V1 fallback below for malformed legacy data.
            }
          }
          if (!restoredSnapshot && snapshot && typeof snapshot === "object") {
            setPlatformFilters(platform, snapshot as PlatformFilterValues);
          } else if (snapshot && typeof snapshot === "object") {
            // V2 snapshot was restored from the complete envelope above.
          }
        }
      }
      const hasActiveTrackRow = Boolean(next?.tracks.some((track) =>
        ACTIVE_FLOW_STATUSES.has(String(track.status || "")),
      ));
      if (isActiveParallelFlow(next) && (!next?.tracks.length || hasActiveTrackRow)) {
        startPolling();
      }
      clearFlowStatus();
    } catch (reason: unknown) {
      stale.value = true;
      const detail = errorMessage(reason, "流程恢复失败");
      error.value = detail.includes("流程恢复失败") ? detail : `流程恢复失败：${detail}`;
    }
  }

  if (getCurrentInstance()) onBeforeUnmount(clearPolling);

  return {
    flow,
    tracks,
    trackList,
    loading,
    operatingPlatform,
    error,
    stale,
    polling,
    available,
    platformGroups,
    platformSchemas,
    platformValues,
    unifiedValues,
    hasActiveTrack,
    hasLiveWorker,
    canStartNewRound,
    canResetNewRound,
    clearFlowError,
    setPlatformFilters,
    setUnifiedFilters,
    createConditionSnapshot,
    restoreConditionSnapshot,
    resetConditionState,
    refresh,
    fetchTaskState,
    fetchFlowResults,
    loadPlatformGroups,
    prepareDialog,
    syncDrafts,
    startPolling,
    clearPolling,
    start,
    operate,
    operateTrack,
    isFlowOwnedScrapeTask,
    restore,
  };
}

interface ParallelFlowRecoveryDeps {
  parallelFlow: {
    refresh: (options?: { isCurrent?: () => boolean }) => Promise<ParallelFlowState | null>;
    restore: (next: ParallelFlowState | null) => void;
  };
  parallelMode: Ref<boolean>;
  requestDraftPlatform: (platform: Exclude<ParallelSelection, "all">) => unknown;
  loadAdvancedSettings: () => unknown;
  loadFilterLabels: () => unknown;
  loadCityCatalog: () => unknown;
  restoreRunningTask: () => Promise<unknown>;
  restoreSaved02State: () => unknown;
  maybeAutoStartNewRound: () => unknown;
  scrapeBusy: Ref<boolean>;
  screenBusy: Ref<boolean>;
  recrawlBusy: Ref<boolean>;
  /**
   * The coordinator's intent generation.  Initial recovery is allowed to
   * apply only while this value is unchanged; a user's mode choice therefore
   * invalidates a late response before it can restore a stale Flow.
   */
  getRecoveryIntent?: () => number;
}

export function registerDiscoveryParallelRecovery(deps: ParallelFlowRecoveryDeps) {
  onMounted(() => {
    const recoveryIntent = deps.getRecoveryIntent?.();
    const isCurrentRecovery = () => recoveryIntent === undefined
      || deps.getRecoveryIntent?.() === recoveryIntent;
    // Hydrate the current Flow before legacy task recovery.  The latter may
    // otherwise see a completed Flow-owned scrape as a legacy auto-screen
    // source during the first mount and submit a duplicate /api/ai-screen.
    const flowReady = deps.parallelFlow.refresh({ isCurrent: isCurrentRecovery }).then((restoredFlow) => {
      if (!isCurrentRecovery()) return "stale" as const;
      // Refresh mutates the controller before returning. For a terminal
      // all-platform Flow, restore a fresh object so the result cache observes
      // the second authoritative read used to settle terminal projections.
      const flowToApply = restoredFlow && restoredFlow.selection === "all"
        && restoredFlow.status === "done"
        ? { ...restoredFlow, tracks: restoredFlow.tracks.map((track) => ({ ...track })) }
        : restoredFlow;
      deps.parallelFlow.restore(flowToApply);
      if (!flowToApply) return "empty" as const;
      if (flowToApply.selection === "all") {
        deps.parallelMode.value = true;
        return "flow" as const;
      }
      deps.parallelMode.value = false;
      deps.requestDraftPlatform(flowToApply.selection);
      return "flow" as const;
    }).catch(() => {
      // 首帧默认“全部”保持不变；仅明确恢复到单平台时才切换视图。
      return "error" as const;
    });
    void deps.loadAdvancedSettings();
    void deps.loadFilterLabels();
    void deps.loadCityCatalog();
    void flowReady.then(async (status) => {
      try {
        // A successful Flow response may still carry a legacy task snapshot
        // needed by the existing progress panel.  It is safe to hydrate that
        // snapshot only after a successful authoritative read; errors/stale
        // responses never enter this branch.  Automatic new-round recovery is
        // kept stricter below and requires Flow=null.
        if ((status === "empty" || status === "flow") && isCurrentRecovery()) {
          await deps.restoreRunningTask();
          if (!isCurrentRecovery()) return;
        }
      } finally {
        if (!isCurrentRecovery()) return;
        deps.restoreSaved02State();
        // Only an authoritative current Flow=null result opens the legacy
        // recovery/new-round path. Error and stale responses stay read-only.
        if (status === "empty" && isCurrentRecovery()
          && !deps.scrapeBusy.value && !deps.screenBusy.value && !deps.recrawlBusy.value) {
          void deps.maybeAutoStartNewRound();
        }
      }
    }).catch(() => {
      // Legacy recovery reports its own visible state; never turn a rejected
      // legacy restore into another automatic recovery attempt here.
    });
  });
}
