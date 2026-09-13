// Spec041 后续（用户拍板 2026-09-13）：第 2 页的输入是"一份、复用的输入"。
//
// 需求原话：可能重新回到第 2 页、切换平台继续点执行，就是复用第 2 页的内容，
// 用户懒得重新打字；第 2 页的东西要一直在。所以：
// - 一份：关键词/自定义关键词/城市/自定义城市/画像文本不再按平台分格，两个平台共用；
// - 一直在：浏览器本地存一份（跨标签页、跨重启），同时写进数据库（按画像一行）；
//   打开应用先用数据库回填；本地已有更新的内容则以本地为准并写回数据库；
// - 仍按画像隔离：换画像各有一份，互不影响。
// 第 3 页筛选草稿与区县/商圈码不在这里：区县码各平台不同，仍按平台各存各的。
import { reactive, toValue, type MaybeRefOrGetter } from "vue";

import { apiRequest } from "../api";
import type { Platform } from "../types";

/** 第 2 页的共用输入（一份，不按平台分）。 */
export interface SearchDraftSlot {
  /** 关键词列表（含推荐标记）。 */
  keywords: Array<{ word: string; recommended: boolean }>;
  /** 已勾选关键词。 */
  selectedKeywords: string[];
  /** 关键词输入框里未提交的文本。 */
  customKeyword: string;
  /** 城市文本（逗号分隔）。 */
  cityText: string;
  /** 城市输入框里未提交的文本。 */
  customCity: string;
  /** 求职画像文本（第 2 页画像框）。 */
  profileSummary: string;
}

const STORAGE_PREFIX = "career-scout-search-draft:";
/** 输入停下来多久后写数据库（防抖）。 */
const REMOTE_SAVE_DELAY_MS = 600;

const byProfile = reactive<Record<string, SearchDraftSlot>>({});
/** 已从本地存档恢复过（或确认不存在）的画像。 */
const restoredProfiles = new Set<string>();
/** 本地存档里确实存在过输入数据的画像（用于区分"空输入"与"没有存档"）。 */
const persistedProfiles = new Set<string>();
/** 已用数据库内容回填过的画像（每个会话只回填一次）。 */
const hydratedProfiles = new Set<string>();
const remoteSaveTimers = new Map<string, ReturnType<typeof setTimeout>>();
/** 远端同步开关：测试或离线场景关闭，只走本地。 */
let remoteEnabled = true;

/** 测试或离线场景关闭数据库同步（默认开启）。 */
export function setPage2DraftRemoteEnabled(enabled: boolean): void {
  remoteEnabled = enabled;
}

function emptySlot(): SearchDraftSlot {
  return {
    keywords: [],
    selectedKeywords: [],
    customKeyword: "",
    cityText: "",
    customCity: "",
    profileSummary: "",
  };
}

/** 这一份输入里是否有用户内容（用于"本地优先于数据库"的判定）。 */
function hasContent(slot: Partial<SearchDraftSlot> | null | undefined): boolean {
  if (!slot) return false;
  return Boolean(
    (Array.isArray(slot.keywords) && slot.keywords.length)
    || (Array.isArray(slot.selectedKeywords) && slot.selectedKeywords.length)
    || String(slot.customKeyword || "").trim()
    || String(slot.cityText || "").trim()
    || String(slot.customCity || "").trim()
    || String(slot.profileSummary || "").trim(),
  );
}

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function normalizeSlot(raw: Record<string, unknown>): SearchDraftSlot {
  const keywords = Array.isArray(raw.keywords)
    ? raw.keywords
      .map((item) => (item && typeof item === "object"
        ? {
          word: String((item as Record<string, unknown>).word || ""),
          recommended: Boolean((item as Record<string, unknown>).recommended),
        }
        : { word: String(item ?? ""), recommended: false }))
      .filter((item) => item.word)
    : [];
  const selectedKeywords = Array.isArray(raw.selectedKeywords)
    ? raw.selectedKeywords.map(String).filter(Boolean)
    : [];
  return {
    keywords,
    selectedKeywords,
    customKeyword: String(raw.customKeyword ?? ""),
    cityText: String(raw.cityText ?? ""),
    customCity: String(raw.customCity ?? ""),
    profileSummary: String(raw.profileSummary ?? ""),
  };
}

function restoreProfile(profileKey: string): void {
  const store = storage();
  if (restoredProfiles.has(profileKey)) {
    // 存档被清空（测试隔离 / 用户清理站点数据）后，不能把上一份内存输入
    // 继续当成这次会话的输入；重新按存档恢复（通常是空输入）。
    if (byProfile[profileKey] && store) {
      const raw = (() => {
        try {
          return store.getItem(`${STORAGE_PREFIX}${profileKey}`);
        } catch {
          return null;
        }
      })();
      if (raw !== null) return;
      delete byProfile[profileKey];
      persistedProfiles.delete(profileKey);
    } else {
      return;
    }
  }
  restoredProfiles.add(profileKey);
  let found = false;
  let parsed: Record<string, unknown> | null = null;
  try {
    const raw = store?.getItem(`${STORAGE_PREFIX}${profileKey}`) ?? null;
    if (raw) {
      parsed = JSON.parse(raw) as Record<string, unknown>;
      found = true;
    }
  } catch {
    // 存档损坏：回到空输入，用户重新填写即可，不阻断页面。
    parsed = null;
  }
  byProfile[profileKey] = parsed ? normalizeSlot(parsed) : emptySlot();
  if (found) persistedProfiles.add(profileKey);
  else persistedProfiles.delete(profileKey);
}

function persistProfile(profileKey: string): void {
  try {
    const slot = byProfile[profileKey];
    if (!slot) return;
    storage()?.setItem(`${STORAGE_PREFIX}${profileKey}`, JSON.stringify(slot));
    persistedProfiles.add(profileKey);
  } catch {
    // localStorage 只是本地兜底；不可用时内存里的输入仍然有效。
  }
}

/** 防抖写数据库：失败不打断输入（本地那份还在，下次编辑再试）。 */
function scheduleRemoteSave(profileKey: string): void {
  if (!remoteEnabled || !profileKey) return;
  const existing = remoteSaveTimers.get(profileKey);
  if (existing !== undefined) clearTimeout(existing);
  remoteSaveTimers.set(profileKey, setTimeout(() => {
    remoteSaveTimers.delete(profileKey);
    const slot = byProfile[profileKey];
    if (!slot) return;
    void apiRequest(`/api/profiles/${encodeURIComponent(profileKey)}`, {
      method: "PATCH",
      json: { page2_draft: { ...slot } },
    }).catch(() => { /* 离线/服务不可用：本地兜底，下次输入再同步 */ });
  }, REMOTE_SAVE_DELAY_MS));
}

/**
 * 用数据库里的内容回填第 2 页输入（打开应用 / 切画像时调用）。
 *
 * 本地有更新的内容（这个浏览器里刚敲的）→ 以本地为准，并把它写回数据库；
 * 本地为空（换浏览器 / 清过站点数据）→ 用数据库那份回填。
 */
export function hydratePage2Draft(
  profileId: string,
  payload?: Record<string, unknown> | null,
): void {
  const profileKey = String(profileId || "");
  if (!profileKey || hydratedProfiles.has(profileKey)) return;
  hydratedProfiles.add(profileKey);
  restoreProfile(profileKey);
  if (hasContent(byProfile[profileKey])) {
    scheduleRemoteSave(profileKey);
    return;
  }
  if (!payload || typeof payload !== "object") return;
  const restored = normalizeSlot(payload);
  if (!hasContent(restored)) return;
  byProfile[profileKey] = restored;
  persistedProfiles.add(profileKey);
  persistProfile(profileKey);
}

export function useSearchDraftSlots(profileId?: MaybeRefOrGetter<string>) {
  const currentKey = () => String(profileId ? toValue(profileId) : "__default__");

  /** 这份共用输入（两个平台同一份）。 */
  function sharedSlot(): SearchDraftSlot {
    const profileKey = currentKey();
    restoreProfile(profileKey);
    return byProfile[profileKey] || emptySlot();
  }

  /** 平台参数保留为调用面兼容：内容两个平台共用，是同一份。 */
  function slot(_platform: Platform): SearchDraftSlot {
    return sharedSlot();
  }

  function set(_platform: Platform, patch: Partial<SearchDraftSlot>): void {
    Object.assign(sharedSlot(), patch);
    const profileKey = currentKey();
    persistProfile(profileKey);
    scheduleRemoteSave(profileKey);
  }

  function clearShared(): void {
    const profileKey = currentKey();
    byProfile[profileKey] = emptySlot();
    restoredProfiles.add(profileKey);
    persistProfile(profileKey);
    scheduleRemoteSave(profileKey);
  }

  function reset(_platform: Platform): void {
    clearShared();
  }

  function resetAll(): void {
    clearShared();
  }

  /** 本地是否有这份输入（用于"本地优先于旧快照"的恢复判定）。 */
  function hasStoredSlots(): boolean {
    const profileKey = currentKey();
    restoreProfile(profileKey);
    return persistedProfiles.has(profileKey);
  }

  return {
    slot,
    set,
    reset,
    resetAll,
    hasStoredSlots,
    /** 只读投影：两个平台指向同一份共用输入。 */
    get byPlatform() {
      return { boss: sharedSlot(), zhilian: sharedSlot() };
    },
  };
}
