// Spec 044 B100：常用搜索配置包的前端领域编排。
//
// 分层：view/components → useSearchPackages → apiRequest。组件不直接拼请求，
// 也不直接操作数据库语义；页面把第二页 refs 与三个接线动作注入进来，本模块
// 负责快照生成、先校验后一次性应用、配置包列表管理与失败通知。
//
// 三条硬边界：
// - 只有用户显式调用 saveCurrent 才会写配置包 API，且每次都创建新包；
// - 选择配置包先完整校验再一次性提交，失败留在原地、不部分回填；
// - 不读不写第三页筛选，也不按平台拆分。
import { computed, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from "vue";

import { apiRequest, errorMessage } from "../api";
import type {
  Notice,
  SearchPackage,
  SearchPackageCity,
  SearchPackageKeywords,
  SearchPackagePayload,
  SearchPackageProfile,
  SearchPackageSummary,
} from "../types";

/** 第二页可被配置包恢复的字段（与后端快照一一对应）。 */
export interface SearchPackageDraft {
  keywords: SearchPackageKeywords;
  city: SearchPackageCity;
  profile: SearchPackageProfile;
}

/** 配置包读写的第二页 refs：由页面传入，本模块不自己持有页面状态。 */
export interface SearchPackageRefs {
  keywords: Ref<Array<{ word: string; recommended: boolean }>>;
  selectedKeywords: Ref<string[]>;
  customKeyword: Ref<string>;
  cityText: Ref<string>;
  customCity: Ref<string>;
  profileSummary: Ref<string>;
  profileFacts: Ref<Record<string, unknown>>;
}

/** 与页面其余部分的接线：共享草稿、步骤切换与通知。 */
export interface SearchPackageHooks {
  /** 把回填后的第二页内容写进现有共享草稿槽。 */
  persistDraft(): void;
  /** 选择提交失败时，把共享草稿恢复为应用前的内容。 */
  restoreDraft?(): void;
  /** 选择提交失败时，把页面停留位置恢复为应用前的步骤。 */
  restoreStep?(): void;
  /** 回填全部成功后最后一步：进入第二页。 */
  enterSearchStep(): void;
  notify(message: string, tone: Notice["tone"]): void;
}

  /** 当前选择所属的用户上下文：新轮/画像/新分析会清掉当前标记。 */
export interface SearchPackageContext {
  profileId?: MaybeRefOrGetter<string>;
  roundKey?: MaybeRefOrGetter<string>;
  analysisKey?: MaybeRefOrGetter<unknown>;
  /** 非空时代表用户当前选择的简历；清空选择不新增失效语义。 */
  fileKey?: MaybeRefOrGetter<string | null | undefined>;
}

export const SEARCH_PACKAGE_PAYLOAD_VERSION = 1;
const DEFAULT_PACKAGE_NAME = "常用搜索配置";
const MAX_NAME_LENGTH = 80;
const UNUSABLE_MESSAGE = "这套配置无法完整读取，请重新保存";
const LIST_FALLBACK = "常用配置加载失败，请重试";
const SAVE_FALLBACK = "常用配置保存失败，请重试";
const RENAME_FALLBACK = "重命名失败，请重试";
const DELETE_FALLBACK = "删除失败，请重试";

/** 与后端同口径的默认名称：首个关键词 + 城市文本，缺失时回退通用名。 */
export function defaultPackageName(
  keywords: SearchPackageKeywords,
  city: SearchPackageCity,
): string {
  const word = String(
    keywords.selected[0] || keywords.candidates[0]?.word || "",
  ).trim();
  const cityText = String(city.text || "").trim();
  if (word && cityText) return `${word} · ${cityText}`.slice(0, MAX_NAME_LENGTH);
  return (word || cityText || DEFAULT_PACKAGE_NAME).slice(0, MAX_NAME_LENGTH);
}

/** 错误体里的用户可读文案优先；拿不到就用兜底文案。 */
function packageErrorMessage(error: unknown, fallback: string): string {
  const payload = (error as { payload?: Record<string, unknown> })?.payload;
  const body = payload?.error;
  const message = body && typeof body === "object"
    ? (body as Record<string, unknown>).message
    : "";
  if (typeof message === "string" && message.trim()) return message;
  return errorMessage(error, fallback);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

const PROFILE_FACT_STRING_KEYS = [
  "degree", "graduation_date", "week_off", "overtime", "work_pattern",
  "experience_years_source",
] as const;
const PROFILE_FACT_NUMBER_KEYS = ["experience_years", "graduation_year"] as const;
const PROFILE_FACT_STRING_LIST_KEYS = ["core_skills", "languages", "companies"] as const;

function pickStrings(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .map((item) => item.trim());
  return items.length ? [...new Set(items)] : undefined;
}

function pickTextRecord(value: unknown, keys: readonly string[]): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  const result: Record<string, unknown> = {};
  for (const key of keys) {
    if (typeof value[key] === "string" && String(value[key]).trim()) {
      result[key] = String(value[key]).trim();
    }
  }
  return Object.keys(result).length ? result : null;
}

function normalizeProfileFacts(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) return {};
  const result: Record<string, unknown> = {};
  for (const key of PROFILE_FACT_STRING_KEYS) {
    const raw = value[key];
    if (typeof raw === "string" && raw.trim()) result[key] = raw.trim();
  }
  if (value.job_type === "全职" || value.job_type === "实习"
      || value.job_type === "兼职" || value.job_type === "未体现") {
    result.job_type = value.job_type;
  }
  if (value.degree_type === "统招" || value.degree_type === "非统招") {
    result.degree_type = value.degree_type;
  }
  for (const key of PROFILE_FACT_NUMBER_KEYS) {
    const raw = value[key];
    if (typeof raw === "number" && Number.isFinite(raw)) result[key] = raw;
  }
  for (const key of PROFILE_FACT_STRING_LIST_KEYS) {
    const items = pickStrings(value[key]);
    if (items) result[key] = items;
  }
  if (Array.isArray(value.projects)) {
    const projects = value.projects
      .map((item) => pickTextRecord(item, ["name", "role", "stack", "summary"]))
      .filter((item): item is Record<string, unknown> => Boolean(item?.name));
    if (projects.length) result.projects = projects;
  }
  if (Array.isArray(value.employment_history)) {
    const history = value.employment_history.map((raw) => {
      if (!isRecord(raw)) return null;
      const item = pickTextRecord(raw, ["company", "role", "evidence", "start_date", "end_date"]);
      if (!item?.company && !item?.role) return null;
      if (typeof raw.confidence === "number" && Number.isFinite(raw.confidence)
          && raw.confidence >= 0 && raw.confidence <= 1) item.confidence = raw.confidence;
      if (raw.current === true) item.current = true;
      return item;
    }).filter((item): item is Record<string, unknown> => Boolean(item));
    if (history.length) result.employment_history = history;
  }
  if (Array.isArray(value.education_history)) {
    const history = value.education_history.map((raw) => {
      if (!isRecord(raw)) return null;
      const item = pickTextRecord(raw, ["school", "degree", "major", "evidence", "graduation_date"]);
      if (!item?.school && !item?.degree && !item?.major) return null;
      if (typeof raw.confidence === "number" && Number.isFinite(raw.confidence)
          && raw.confidence >= 0 && raw.confidence <= 1) item.confidence = raw.confidence;
      return item;
    }).filter((item): item is Record<string, unknown> => Boolean(item));
    if (history.length) result.education_history = history;
  }
  return result;
}

function sameJson(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right)
      && left.length === right.length
      && left.every((item, index) => sameJson(item, right[index]));
  }
  if (!isRecord(left) || !isRecord(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index] && sameJson(left[key], right[key]));
}

/**
 * 本地结构复核：服务端已校验过一次，这里再挡一次"整包不完整"，
 * 避免半包写进第二页。任一项不成立返回 null（整次选择按失败处理）。
 */
function validateLoadedPackage(raw: unknown): SearchPackage | null {
  if (!isRecord(raw)) return null;
  if (raw.payloadVersion !== SEARCH_PACKAGE_PAYLOAD_VERSION) return null;
  const keywords = raw.keywords;
  const city = raw.city;
  const profile = raw.profile;
  if (!isRecord(keywords) || !isRecord(city) || !isRecord(profile)) return null;
  if (!Array.isArray(keywords.candidates) || !Array.isArray(keywords.selected)) return null;
  if (typeof keywords.custom !== "string") return null;
  if (!keywords.candidates.every((item) => (
    isRecord(item) && typeof item.word === "string" && typeof item.recommended === "boolean"
  ))) return null;
  if (!keywords.selected.every((item) => typeof item === "string")) return null;
  if (typeof city.text !== "string" || typeof city.custom !== "string") return null;
  if (typeof profile.summary !== "string" || !isRecord(profile.facts)) return null;
  if (!sameJson(profile.facts, normalizeProfileFacts(profile.facts))) return null;
  if (typeof raw.id !== "string" || typeof raw.name !== "string") return null;
  return raw as unknown as SearchPackage;
}

function packageDraft(loaded: SearchPackage): SearchPackageDraft {
  return {
    keywords: {
      candidates: loaded.keywords.candidates.map((item) => ({ ...item })),
      selected: [...loaded.keywords.selected],
      custom: loaded.keywords.custom,
    },
    city: { ...loaded.city },
    profile: { summary: loaded.profile.summary, facts: { ...loaded.profile.facts } },
  };
}

export function useSearchPackages(
  refs: SearchPackageRefs,
  hooks: SearchPackageHooks,
  context: SearchPackageContext = {},
) {
  const packages = ref<SearchPackageSummary[]>([]);
  const listBusy = ref(false);
  const listError = ref("");
  const currentPackageId = ref<string | null>(null);
  const currentPackageName = ref("");
  const saveBusy = ref(false);
  const selectBusy = ref(false);
  const manageBusy = ref(false);

  const contextValue = () => ({
    profileId: context.profileId ? String(toValue(context.profileId)) : "",
    roundKey: context.roundKey ? String(toValue(context.roundKey)) : "",
    analysisKey: context.analysisKey ? toValue(context.analysisKey) : null,
    fileKey: context.fileKey ? String(toValue(context.fileKey) || "") : "",
  });
  let identityContext = contextValue();
  let contextVersion = 0;
  let restoreToken = 0;
  const IDENTITY_STORAGE_PREFIX = "career-scout-search-package-identity:";

  function identityStorage(): Storage | null {
    try {
      return typeof localStorage === "undefined" ? null : localStorage;
    } catch {
      return null;
    }
  }

  function identityStorageKey(profileId = identityContext.profileId): string {
    return profileId ? `${IDENTITY_STORAGE_PREFIX}${profileId}` : "";
  }

  function clearCurrentIdentity(removeStored = true): void {
    if (removeStored) {
      const key = identityStorageKey();
      if (key) identityStorage()?.removeItem(key);
    }
    currentPackageId.value = null;
    currentPackageName.value = "";
  }

  function persistCurrentIdentity(): void {
    const key = identityStorageKey();
    if (!key || !currentPackageId.value) return;
    identityStorage()?.setItem(key, JSON.stringify({
      id: currentPackageId.value,
      name: currentPackageName.value,
      roundKey: identityContext.roundKey,
    }));
  }

  async function restoreCurrentIdentity(): Promise<void> {
    const key = identityStorageKey();
    if (!key) return;
    const restoreContext = captureContextVersion();
    const restoreRequest = ++restoreToken;
    const restoreDraftSnapshot = snapshotRefs().draft;
    try {
      const raw = identityStorage()?.getItem(key);
      const saved = raw ? JSON.parse(raw) as Record<string, unknown> : null;
      if (saved?.roundKey !== identityContext.roundKey
          || typeof saved.id !== "string" || !saved.id
          || typeof saved.name !== "string") {
        if (raw) identityStorage()?.removeItem(key);
        return;
      }
      const loaded = await apiRequest<SearchPackage>(
        `/api/search-packages/${encodeURIComponent(saved.id)}`,
      );
      if (restoreRequest !== restoreToken || !isCurrentContext(restoreContext)) return;
      if (loaded.id !== saved.id) {
        clearCurrentIdentity();
        return;
      }
      const validated = validateLoadedPackage(loaded);
      if (!validated) {
        clearCurrentIdentity();
        return;
      }
      if (!sameJson(restoreDraftSnapshot, snapshotRefs().draft)) {
        cancelPendingIdentityRestore();
        clearCurrentIdentity();
        return;
      }
      const snapshot = snapshotRefs();
      try {
        applyDraft(packageDraft(validated));
        hooks.persistDraft();
        currentPackageId.value = validated.id;
        currentPackageName.value = validated.name;
        persistCurrentIdentity();
      } catch {
        restoreRefs(snapshot);
        try { hooks.restoreDraft?.(); } catch { /* 保留恢复失败状态 */ }
        clearCurrentIdentity();
      }
    } catch {
      if (restoreRequest === restoreToken && isCurrentContext(restoreContext)) {
        clearCurrentIdentity();
      }
    }
  }

  function syncIdentityContext(): void {
    const next = contextValue();
    const profileOrRoundChanged = next.profileId !== identityContext.profileId
      || next.roundKey !== identityContext.roundKey;
    const analysisChanged = next.analysisKey !== identityContext.analysisKey;
    const fileChanged = Boolean(next.fileKey) && next.fileKey !== identityContext.fileKey;
    if (profileOrRoundChanged || analysisChanged || fileChanged) {
      clearCurrentIdentity();
      identityContext = {
        ...next,
        fileKey: next.fileKey || identityContext.fileKey,
      };
      contextVersion += 1;
    }
  }

  function captureContextVersion(): number {
    syncIdentityContext();
    return contextVersion;
  }

  function isCurrentContext(version: number): boolean {
    syncIdentityContext();
    return contextVersion === version;
  }

  function cancelPendingIdentityRestore(): void {
    restoreToken += 1;
  }

  if (context.profileId || context.roundKey || context.analysisKey || context.fileKey) {
    watch(
      () => [contextValue().profileId, contextValue().roundKey, contextValue().analysisKey, contextValue().fileKey],
      () => { syncIdentityContext(); },
      { flush: "sync" },
    );
    restoreCurrentIdentity();
  }

  /** 当前第二页内容 → 配置包快照（不读第三页筛选，也不带平台标识）。 */
  function buildDraft(): SearchPackageDraft {
    return {
      keywords: {
        candidates: refs.keywords.value.map((item) => ({
          word: String(item.word || ""),
          recommended: Boolean(item.recommended),
        })),
        selected: [...refs.selectedKeywords.value],
        custom: refs.customKeyword.value,
      },
      city: { text: refs.cityText.value, custom: refs.customCity.value },
      profile: {
        summary: refs.profileSummary.value,
        facts: normalizeProfileFacts(refs.profileFacts.value),
      },
    };
  }

  function buildPayload(name: string): SearchPackagePayload {
    const draft = buildDraft();
    return {
      name: String(name || "").trim(),
      payloadVersion: SEARCH_PACKAGE_PAYLOAD_VERSION,
      keywords: draft.keywords,
      city: draft.city,
      profile: draft.profile,
    };
  }

  /** 空名或空白名交给服务端生成默认名：本地先给一个可编辑的默认值。 */
  function defaultName(): string {
    const draft = buildDraft();
    return defaultPackageName(draft.keywords, draft.city);
  }

  function applyDraft(draft: SearchPackageDraft): void {
    refs.keywords.value = draft.keywords.candidates.map((item) => ({ ...item }));
    refs.selectedKeywords.value = [...draft.keywords.selected];
    refs.customKeyword.value = draft.keywords.custom;
    refs.cityText.value = draft.city.text;
    refs.customCity.value = draft.city.custom;
    refs.profileSummary.value = draft.profile.summary;
    refs.profileFacts.value = { ...draft.profile.facts };
  }

  function snapshotRefs() {
    return {
      draft: buildDraft(),
      packageId: currentPackageId.value,
      packageName: currentPackageName.value,
    };
  }

  function restoreRefs(snapshot: ReturnType<typeof snapshotRefs>): void {
    applyDraft(snapshot.draft);
    currentPackageId.value = snapshot.packageId;
    currentPackageName.value = snapshot.packageName;
    if (snapshot.packageId) persistCurrentIdentity();
    else clearCurrentIdentity();
  }

  function upsertSummary(pkg: SearchPackage): void {
    const summary: SearchPackageSummary = {
      id: pkg.id,
      name: pkg.name,
      createdAt: pkg.createdAt,
      updatedAt: pkg.updatedAt,
    };
    const index = packages.value.findIndex((item) => item.id === pkg.id);
    if (index >= 0) packages.value[index] = summary;
    else packages.value = [summary, ...packages.value];
  }

  // -- 列表与选择 ---------------------------------------------------------

  async function loadList(): Promise<boolean> {
    listBusy.value = true;
    listError.value = "";
    try {
      const data = await apiRequest<{ items: SearchPackageSummary[] }>(
        "/api/search-packages",
      );
      packages.value = Array.isArray(data?.items)
        ? data.items.filter((item) => item && typeof item.id === "string")
        : [];
      return true;
    } catch (error) {
      listError.value = packageErrorMessage(error, LIST_FALLBACK);
      hooks.notify(listError.value, "error");
      return false;
    } finally {
      listBusy.value = false;
    }
  }

  /** 选择一套配置包：先取回并完整校验，成功才一次性写第二页并切页。 */
  async function selectPackage(packageId: string): Promise<boolean> {
    if (selectBusy.value) return false;
    cancelPendingIdentityRestore();
    const requestContext = captureContextVersion();
    selectBusy.value = true;
    try {
      let raw: unknown;
      try {
        raw = await apiRequest<SearchPackage>(
          `/api/search-packages/${encodeURIComponent(packageId)}`,
        );
      } catch (error) {
        if (!isCurrentContext(requestContext)) return false;
        hooks.notify(packageErrorMessage(error, UNUSABLE_MESSAGE), "error");
        return false;
      }
      if (!isCurrentContext(requestContext)) return false;
      const loaded = validateLoadedPackage(raw);
      if (!loaded) {
        hooks.notify(UNUSABLE_MESSAGE, "error");
        return false;
      }
      const draft = packageDraft(loaded);
      const snapshot = snapshotRefs();
      try {
        applyDraft(draft);
        hooks.persistDraft();
        currentPackageId.value = loaded.id;
        currentPackageName.value = loaded.name;
        persistCurrentIdentity();
        hooks.enterSearchStep();
        hooks.notify("已使用常用配置", "success");
      } catch (error) {
        restoreRefs(snapshot);
        try { hooks.restoreDraft?.(); } catch { /* 保留原提交错误 */ }
        try { hooks.restoreStep?.(); } catch { /* 保留原提交错误 */ }
        hooks.notify(packageErrorMessage(error, UNUSABLE_MESSAGE), "error");
        return false;
      }
      return true;
    } finally {
      selectBusy.value = false;
    }
  }

  // -- 保存 ---------------------------------------------------------------

  async function createPackage(name: string, requestContext: number): Promise<SearchPackage | null> {
    try {
      const created = await apiRequest<SearchPackage>("/api/search-packages", {
        method: "POST",
        json: buildPayload(name),
      });
      if (!isCurrentContext(requestContext)) return null;
      upsertSummary(created);
      currentPackageId.value = created.id;
      currentPackageName.value = created.name;
      persistCurrentIdentity();
      hooks.notify("已保存常用配置", "success");
      return created;
    } catch (error) {
      if (!isCurrentContext(requestContext)) return null;
      hooks.notify(packageErrorMessage(error, SAVE_FALLBACK), "error");
      return null;
    }
  }

  /** 保存当前页面状态：每次都创建一套新的配置包。 */
  async function saveCurrent(name: string): Promise<SearchPackage | null> {
    if (saveBusy.value) return null;
    cancelPendingIdentityRestore();
    const requestContext = captureContextVersion();
    saveBusy.value = true;
    try {
      return await createPackage(name, requestContext);
    } finally {
      saveBusy.value = false;
    }
  }

  // -- 管理 ---------------------------------------------------------------

  async function renamePackage(packageId: string, name: string): Promise<boolean> {
    syncIdentityContext();
    if (manageBusy.value) return false;
    manageBusy.value = true;
    try {
      const updated = await apiRequest<SearchPackage>(
        `/api/search-packages/${encodeURIComponent(packageId)}/name`,
        { method: "PATCH", json: { name: String(name || "").trim() } },
      );
      upsertSummary(updated);
      if (currentPackageId.value === packageId) {
        currentPackageName.value = updated.name;
        persistCurrentIdentity();
      }
      return true;
    } catch (error) {
      hooks.notify(packageErrorMessage(error, RENAME_FALLBACK), "error");
      return false;
    } finally {
      manageBusy.value = false;
    }
  }

  /** 删除：只移除目标包；当前轮已回填的内容不动。 */
  async function deletePackage(packageId: string): Promise<boolean> {
    syncIdentityContext();
    if (manageBusy.value) return false;
    manageBusy.value = true;
    try {
      await apiRequest(
        `/api/search-packages/${encodeURIComponent(packageId)}`,
        { method: "DELETE" },
      );
      packages.value = packages.value.filter((item) => item.id !== packageId);
      if (currentPackageId.value === packageId) {
        clearCurrentIdentity();
      }
      return true;
    } catch (error) {
      hooks.notify(packageErrorMessage(error, DELETE_FALLBACK), "error");
      return false;
    } finally {
      manageBusy.value = false;
    }
  }

  const pickerProps = computed(() => ({
    packages: packages.value,
    listBusy: listBusy.value,
    listError: listError.value,
    manageBusy: manageBusy.value,
    currentPackageId: currentPackageId.value,
  }));
  const pickerEvents = {
    open: loadList,
    retry: loadList,
    select: selectPackage,
    rename: renamePackage,
    remove: deletePackage,
  };
  const saveProps = computed(() => ({
    defaultName: defaultName(),
    busy: saveBusy.value,
  }));
  const saveEvents = { save: saveCurrent };

  return {
    packages,
    listBusy,
    listError,
    loadList,
    selectPackage,
    selectBusy,
    currentPackageId,
    currentPackageName,
    defaultName,
    saveBusy,
    saveCurrent,
    renamePackage,
    deletePackage,
    manageBusy,
    pickerProps,
    pickerEvents,
    saveProps,
    saveEvents,
  };
}

export type SearchPackagesApi = ReturnType<typeof useSearchPackages>;
