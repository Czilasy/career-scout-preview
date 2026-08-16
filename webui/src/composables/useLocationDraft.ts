// B054 地点草稿：按平台独立保存，城市切换/平台切换不串码值。
import { reactive } from "vue";
import type { LocationCondition, Platform } from "../types";
import { normalizeLocationDraft } from "../location";

const byPlatform = reactive<Record<Platform, Record<string, LocationCondition[]>>>({
  boss: {},
  zhilian: {},
});

export function useLocationDraft() {
  function getLocations(platform: Platform, city: string): LocationCondition[] {
    return byPlatform[platform][city] || [];
  }

  function setLocations(
    platform: Platform,
    city: string,
    conditions: LocationCondition[],
  ): void {
    const normalized = normalizeLocationDraft(platform, city, conditions);
    if (normalized.length) byPlatform[platform][city] = normalized;
    else delete byPlatform[platform][city];
  }

  function clearLocations(platform: Platform, city: string): void {
    delete byPlatform[platform][city];
  }

  function allLocations(platform: Platform, cities: string[]): LocationCondition[] {
    return cities.flatMap((city) => byPlatform[platform][city] || []);
  }

  function reset(): void {
    byPlatform.boss = {};
    byPlatform.zhilian = {};
  }

  return {
    byPlatform,
    getLocations,
    setLocations,
    clearLocations,
    allLocations,
    reset,
  };
}
