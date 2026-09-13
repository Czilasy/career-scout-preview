import { ref, type Ref } from "vue";
import type { CityPanelScene, PageScene, Platform, SceneIdentity } from "../types";

const SESSION_KEY_PREFIX = "career-scout-workflow:";
const SESSION_LIVE_MARKER_PREFIX = "career-scout-scene-live:";
// Spec041 返工：轮次身份独立存档。它必须跨刷新稳定（同一轮刷新后仍接回同一份现场），
// 又不能跟着 task_id / 结果 run id 走（旧实现用任务 id 当身份，抓取→筛选→结果每推进一步
// 就换一次键，组件把阶段推进误当成身份切换，现场被重置成空）。
const ROUND_EPOCH_KEY_PREFIX = "career-scout-round-epoch:";
const SCENE_VERSION = 2;

const currentScenes = new Map<string, PageScene>();
const historyScenes = new Map<string, Map<string, PageScene>>();
// 轮次结果 run id：历史轮现场按结果 run id 存取；开新一轮时用它给旧轮归档。
const roundRunIds = new Map<string, string>();
const roundEpochs = new Map<string, string>();
const restoredProfiles = new Set<string>();
const clearedProfiles = new Set<string>();
let activeIdentity: SceneIdentity | null = null;
/** 当前画像的轮次身份（响应式：换轮时组件随之落到新身份的空现场）。 */
const roundEpoch = ref("");
let roundEpochProfile = "";

function identityKey(identity: SceneIdentity): string {
  return `${identity.profileId}::${identity.runEpoch}::${identity.platform}`;
}

function profilePlatformKey(identity: SceneIdentity): string {
  return `${identity.profileId}::${identity.platform}`;
}

function emptyPageScene(): PageScene {
  return {
    profileInputHeight: null,
    profileInputWidth: null,
    profileInputContent: "",
    cityPanels: {},
    cardOpenStates: {},
    cardScrollTops: {},
    sortKey: "default",
    listFilterDraft: {
      salary: [],
      experience: [],
      degree: [],
      welfare: [],
    },
    visibleCount: 0,
    selectedJobKey: null,
    userSelectedDetail: false,
    detailOpen: true,
    jdScrollTop: 0,
    listScrollTop: 0,
  };
}

function cloneCityPanel(panel: CityPanelScene): CityPanelScene {
  return {
    open: Boolean(panel.open),
    cityCode: String(panel.cityCode ?? ""),
    districts: Array.isArray(panel.districts)
      ? panel.districts.map((district) => ({
          ...district,
          children: district.children?.map((child) => ({ ...child })),
        }))
      : [],
  };
}

function cloneScene(scene: PageScene): PageScene {
  return {
    profileInputHeight: scene.profileInputHeight,
    profileInputWidth: Number.isFinite(scene.profileInputWidth) ? scene.profileInputWidth : null,
    profileInputContent: String(scene.profileInputContent ?? ""),
    cityPanels: Object.fromEntries(
      Object.entries(scene.cityPanels ?? {}).map(([city, panel]) => [city, cloneCityPanel(panel)]),
    ),
    cardOpenStates: { ...(scene.cardOpenStates ?? {}) },
    cardScrollTops: { ...(scene.cardScrollTops ?? {}) },
    sortKey: scene.sortKey ?? "default",
    listFilterDraft: {
      salary: [...(scene.listFilterDraft?.salary ?? [])],
      experience: [...(scene.listFilterDraft?.experience ?? [])],
      degree: [...(scene.listFilterDraft?.degree ?? [])],
      welfare: [...(scene.listFilterDraft?.welfare ?? [])],
    },
    visibleCount: Number.isFinite(scene.visibleCount) ? scene.visibleCount : 0,
    selectedJobKey: scene.selectedJobKey ?? null,
    userSelectedDetail: Boolean(scene.userSelectedDetail),
    detailOpen: scene.detailOpen === undefined ? true : Boolean(scene.detailOpen),
    jdScrollTop: Number.isFinite(scene.jdScrollTop) ? scene.jdScrollTop : 0,
    listScrollTop: Number.isFinite(scene.listScrollTop) ? scene.listScrollTop : 0,
  };
}

function historyScene(scene: PageScene): PageScene {
  const copy = cloneScene(scene);
  copy.listFilterDraft = emptyPageScene().listFilterDraft;
  return copy;
}

function storage(): Storage | null {
  if (typeof globalThis === "undefined" || !("sessionStorage" in globalThis)) return null;
  try {
    return globalThis.sessionStorage;
  } catch (error) {
    console.warn("[Spec041] 无法访问现场会话存储", error);
    return null;
  }
}

function clearProfileMemory(profileId: string): void {
  for (const key of currentScenes.keys()) {
    if (key.startsWith(`${profileId}::`)) currentScenes.delete(key);
  }
  for (const key of historyScenes.keys()) {
    if (key.startsWith(`${profileId}::`)) historyScenes.delete(key);
  }
  // 会话存档被清空时轮次身份也要重新生成：否则会拿着一份已经没有现场的旧身份键。
  // （轮次身份另有自己的存档键，正常切画像/重挂载时会在 ensureRoundEpoch 里取回同一值。）
  roundEpochs.delete(profileId);
  for (const key of roundRunIds.keys()) {
    if (key.startsWith(`${profileId}::`)) roundRunIds.delete(key);
  }
}

function newRoundEpochToken(): string {
  return `round-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function roundRunIdKey(profileId: string, epoch: string): string {
  return `${profileId}::${epoch}`;
}

function readStoredRoundEpoch(profileId: string): string {
  const store = storage();
  if (!store) return "";
  try {
    return String(store.getItem(`${ROUND_EPOCH_KEY_PREFIX}${profileId}`) || "");
  } catch {
    return "";
  }
}

function writeStoredRoundEpoch(profileId: string, epoch: string): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(`${ROUND_EPOCH_KEY_PREFIX}${profileId}`, epoch);
  } catch (error) {
    console.warn("[Spec041] 无法写入轮次身份", error);
  }
}

function syncRoundEpochRef(profileId: string, epoch: string): void {
  if (roundEpochProfile === profileId && roundEpoch.value === epoch) return;
  roundEpochProfile = profileId;
  roundEpoch.value = epoch;
}

function markProfileLive(profileId: string, store: Storage | null): void {
  if (!store) return;
  try {
    store.setItem(`${SESSION_LIVE_MARKER_PREFIX}${profileId}`, "1");
  } catch (error) {
    console.warn("[Spec041] 无法标记现场会话", error);
  }
}

function readStoredWorkflow(profileId: string): Record<string, unknown> {
  const store = storage();
  if (!store) return {};
  try {
    const raw = store.getItem(`${SESSION_KEY_PREFIX}${profileId}`);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
  } catch (error) {
    console.warn("[Spec041] 读取现场会话存档失败", error);
    return {};
  }
}

function setCurrent(identity: SceneIdentity, scene: PageScene): void {
  currentScenes.set(identityKey(identity), cloneScene(scene));
}

function historyMap(identity: SceneIdentity): Map<string, PageScene> {
  const key = profilePlatformKey(identity);
  let map = historyScenes.get(key);
  if (!map) {
    map = new Map<string, PageScene>();
    historyScenes.set(key, map);
  }
  return map;
}

export function useDiscoverySceneState() {
  function ensureProfileRestored(profileId: string): void {
    const store = storage();
    const markerKey = `${SESSION_LIVE_MARKER_PREFIX}${profileId}`;
    if (restoredProfiles.has(profileId)) {
      // 测试隔离、用户主动清空会话或浏览器清理站点数据后，不能把上一次
      // 挂载留在模块内存里的现场继续带进来。标记随 sessionStorage 一起消失，
      // 正常同一页内的反复读取则不会触发清理。
      if (!store || store.getItem(markerKey) !== null) return;
      clearProfileMemory(profileId);
      restoredProfiles.delete(profileId);
      clearedProfiles.delete(profileId);
    }
    if (clearedProfiles.has(profileId) && activeIdentity?.profileId === profileId) {
      restoredProfiles.add(profileId);
      markProfileLive(profileId, store);
      return;
    }
    restoredProfiles.add(profileId);
    clearedProfiles.delete(profileId);
    restoreFromSession(profileId);
    markProfileLive(profileId, store);
  }

  function getCurrent(identity: SceneIdentity): PageScene {
    ensureProfileRestored(identity.profileId);
    activeIdentity = { ...identity };
    return cloneScene(currentScenes.get(identityKey(identity)) ?? emptyPageScene());
  }

  function saveCurrent(identity: SceneIdentity, patch: Partial<PageScene>): void {
    ensureProfileRestored(identity.profileId);
    activeIdentity = { ...identity };
    const next = {
      ...emptyPageScene(),
      ...(currentScenes.get(identityKey(identity)) ?? {}),
      ...patch,
    } as PageScene;
    setCurrent(identity, next);
    // 刷新不依赖组件是否来得及卸载；现场一变就落到会话存档。
    persistToSession(identity.profileId);
  }

  function getHistory(runId: string, identity: SceneIdentity | null = activeIdentity): PageScene {
    if (!identity) return emptyPageScene();
    ensureProfileRestored(identity.profileId);
    const stored = historyMap(identity).get(runId);
    return historyScene(stored ?? emptyPageScene());
  }

  function saveHistory(
    runId: string,
    patch: Partial<PageScene>,
    identity: SceneIdentity | null = activeIdentity,
  ): void {
    if (!identity) return;
    ensureProfileRestored(identity.profileId);
    activeIdentity = { ...identity };
    const existing = historyMap(identity).get(runId) ?? emptyPageScene();
    historyMap(identity).set(
      runId,
      historyScene({ ...existing, ...patch } as PageScene),
    );
    persistToSession(identity.profileId);
  }

  function clearForProfileSwitch(): void {
    if (!activeIdentity) return;
    const profileId = activeIdentity.profileId;
    if (clearedProfiles.has(profileId) && !restoredProfiles.has(profileId)) return;
    persistToSession(profileId);
    clearProfileMemory(profileId);
    restoredProfiles.delete(profileId);
    clearedProfiles.add(profileId);
  }

  /**
   * 取回当前画像的轮次身份（无则新建并持久化）。
   *
   * 一个业务轮次从上传、抓取、筛选到结果完成期间身份不变；刷新后从会话存档取回
   * 同一值，因此现场接得回来。只有开新一轮（`rotateRoundEpoch`）才换发新身份。
   */
  function ensureRoundEpoch(profileId: string): string {
    if (!profileId) return "";
    const cached = roundEpochs.get(profileId);
    if (cached) {
      syncRoundEpochRef(profileId, cached);
      return cached;
    }
    const epoch = readStoredRoundEpoch(profileId) || newRoundEpochToken();
    roundEpochs.set(profileId, epoch);
    writeStoredRoundEpoch(profileId, epoch);
    syncRoundEpochRef(profileId, epoch);
    return epoch;
  }

  /**
   * 开新一轮：换发新的轮次身份，让新轮从干净默认现场开始。
   *
   * 调用顺序固定：先 `archiveCurrentForNewRound` 归档旧轮现场，再调用本函数；
   * 否则旧轮现场会被当成"新身份下的现场"丢掉。
   */
  function rotateRoundEpoch(profileId: string): string {
    if (!profileId) return "";
    const epoch = newRoundEpochToken();
    roundEpochs.set(profileId, epoch);
    writeStoredRoundEpoch(profileId, epoch);
    syncRoundEpochRef(profileId, epoch);
    return epoch;
  }

  /** 记录某轮的结果 run id（历史轮现场按结果 run id 存取）。 */
  function noteRoundRunId(profileId: string, epoch: string, runId: string): void {
    if (!profileId || !epoch || !runId) return;
    const epochKey = roundRunIdKey(profileId, epoch);
    if (roundRunIds.get(epochKey) === runId) return;
    roundRunIds.set(epochKey, runId);
    persistToSession(profileId);
  }

  /** 查某轮已知的结果 run id（历史轮现场键 / 开新轮归档键）。 */
  function resolveRoundRunId(profileId: string, epoch: string): string {
    return roundRunIds.get(roundRunIdKey(profileId, epoch)) || "";
  }

  /**
   * 开新一轮：当前轮现场降级为该轮的历史查看现场。
   *
   * 历史轮浏览按结果 run id 取现场（`getHistory(runId)`），所以归档键优先用
   * `historyRunId`；轮次还没产出结果（放弃本轮）时退回轮次身份本身。
   */
  function archiveCurrentForNewRound(
    oldRunEpoch: string,
    historyRunId = "",
    platform?: Platform,
  ): void {
    if (!activeIdentity) return;
    const oldIdentity: SceneIdentity = {
      ...activeIdentity,
      runEpoch: oldRunEpoch,
      platform: platform || activeIdentity.platform,
    };
    const oldKey = identityKey(oldIdentity);
    const stored = currentScenes.get(oldKey);
    if (stored) {
      const historyKey = historyRunId
        || resolveRoundRunId(oldIdentity.profileId, oldRunEpoch)
        || oldRunEpoch;
      historyMap(oldIdentity).set(historyKey, historyScene(stored));
    }
    currentScenes.delete(oldKey);
    roundRunIds.delete(roundRunIdKey(oldIdentity.profileId, oldRunEpoch));
    persistToSession(oldIdentity.profileId);
  }

  function persistToSession(profileId: string): void {
    const store = storage();
    if (!store) return;
    const current: Record<string, PageScene> = {};
    for (const [key, scene] of currentScenes.entries()) {
      if (key.startsWith(`${profileId}::`)) current[key] = cloneScene(scene);
    }
    const history: Record<string, Record<string, PageScene>> = {};
    for (const [key, scenes] of historyScenes.entries()) {
      if (!key.startsWith(`${profileId}::`)) continue;
      history[key] = Object.fromEntries(
        [...scenes.entries()].map(([runId, scene]) => [runId, historyScene(scene)]),
      );
    }
    const runIds: Record<string, string> = {};
    for (const [key, runId] of roundRunIds.entries()) {
      if (key.startsWith(`${profileId}::`)) runIds[key] = runId;
    }
    try {
      const existing = readStoredWorkflow(profileId);
      store.setItem(
        `${SESSION_KEY_PREFIX}${profileId}`,
        JSON.stringify({
          ...existing,
          pageScene: { version: SCENE_VERSION, current, history, runIds },
        }),
      );
      markProfileLive(profileId, store);
    } catch (error) {
      console.warn("[Spec041] 写入现场会话存档失败", error);
    }
  }

  function restoreFromSession(profileId: string): void {
    const saved = readStoredWorkflow(profileId).pageScene;
    if (!saved || typeof saved !== "object") return;
    const payload = saved as {
      version?: unknown;
      current?: Record<string, PageScene>;
      history?: Record<string, Record<string, PageScene>>;
      runIds?: Record<string, string>;
    };
    if (payload.version !== SCENE_VERSION) return;
    clearProfileMemory(profileId);
    for (const [key, scene] of Object.entries(payload.current ?? {})) {
      if (key.startsWith(`${profileId}::`)) currentScenes.set(key, cloneScene(scene));
    }
    for (const [key, entries] of Object.entries(payload.history ?? {})) {
      if (!key.startsWith(`${profileId}::`)) continue;
      historyScenes.set(
        key,
        new Map(Object.entries(entries).map(([runId, scene]) => [runId, historyScene(scene)])),
      );
    }
    for (const [key, runId] of Object.entries(payload.runIds ?? {})) {
      if (key.startsWith(`${profileId}::`) && runId) roundRunIds.set(key, String(runId));
    }
    restoredProfiles.add(profileId);
    markProfileLive(profileId, storage());
  }

  return {
    getCurrent,
    saveCurrent,
    getHistory,
    saveHistory,
    clearForProfileSwitch,
    archiveCurrentForNewRound,
    restoreFromSession,
    persistToSession,
    roundEpoch: roundEpoch as Ref<string>,
    ensureRoundEpoch,
    rotateRoundEpoch,
    noteRoundRunId,
    resolveRoundRunId,
  };
}
