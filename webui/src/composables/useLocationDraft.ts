// B054 地点草稿：按平台独立保存，城市切换/平台切换不串码值。
import { reactive, toValue, type MaybeRefOrGetter } from "vue";
import type { LocationCondition, Platform } from "../types";
import { normalizeLocationDraft } from "../location";

type PlatformDrafts = Record<Platform, Record<string, LocationCondition[]>>;

const byProfile = reactive<Record<string, PlatformDrafts>>({});
const restoredProfiles = new Set<string>();
const STORAGE_PREFIX = "career-scout-location-draft:";

function emptyDrafts(): PlatformDrafts {
  return { boss: {}, zhilian: {} };
}

function sessionStore(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

function restoreProfile(key: string): PlatformDrafts {
  if (restoredProfiles.has(key)) return byProfile[key] || emptyDrafts();
  restoredProfiles.add(key);
  const drafts = emptyDrafts();
  try {
    const parsed = JSON.parse(sessionStore()?.getItem(`${STORAGE_PREFIX}${key}`) || "{}");
    for (const platform of ["boss", "zhilian"] as const) {
      const cities = parsed?.[platform];
      if (!cities || typeof cities !== "object") continue;
      for (const [city, conditions] of Object.entries(cities)) {
        if (!Array.isArray(conditions)) continue;
        const normalized = normalizeLocationDraft(platform, city, conditions as LocationCondition[]);
        if (normalized.length) drafts[platform][city] = normalized;
      }
    }
  } catch {
    // 会话草稿损坏时回到空草稿，后端地点目录仍是权威来源。
  }
  byProfile[key] = drafts;
  return drafts;
}

function persistProfile(key: string): void {
  try {
    sessionStore()?.setItem(`${STORAGE_PREFIX}${key}`, JSON.stringify(byProfile[key] || emptyDrafts()));
  } catch {
    // sessionStorage 是页面恢复增强；不可用时仍保留当前内存草稿。
  }
}

export function useLocationDraft(profileId?: MaybeRefOrGetter<string>) {
  const currentProfileId = () => String(profileId ? toValue(profileId) : "__default__");
  const current = (): PlatformDrafts => {
    const key = currentProfileId();
    return restoreProfile(key);
  };

  function getLocations(platform: Platform, city: string): LocationCondition[] {
    return current()[platform][city] || [];
  }

  function setLocations(
    platform: Platform,
    city: string,
    conditions: LocationCondition[],
  ): void {
    const normalized = normalizeLocationDraft(platform, city, conditions);
    if (normalized.length) current()[platform][city] = normalized;
    else delete current()[platform][city];
    persistProfile(currentProfileId());
  }

  function clearLocations(platform: Platform, city: string): void {
    delete current()[platform][city];
    persistProfile(currentProfileId());
  }

  function allLocations(platform: Platform, cities: string[]): LocationCondition[] {
    return cities.flatMap((city) => current()[platform][city] || []);
  }

  function reset(): void {
    byProfile[currentProfileId()] = emptyDrafts();
    restoredProfiles.add(currentProfileId());
    persistProfile(currentProfileId());
  }

  return {
    get byPlatform() { return current(); },
    getLocations,
    setLocations,
    clearLocations,
    allLocations,
    reset,
  };
}
