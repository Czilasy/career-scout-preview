// B054 地点条件纯函数：标签、规范化、提交载荷与展示摘要。
import type { LocationCondition, Platform } from "./types";

export function locationLabel(location: Partial<LocationCondition>): string {
  const parts = [
    String(location.city_name || "").trim(),
    String(location.district_name || "").trim(),
  ];
  const business = String(location.business_name || "").trim();
  if (business) parts.push(business);
  return parts.filter(Boolean).join(" · ");
}

export function locationKey(location: Partial<LocationCondition>): string {
  const district = String(location.district_code || location.district_name || "");
  const business = String(location.business_code || "");
  return `${district}:${business}`;
}

export function sameLocation(
  a: Partial<LocationCondition>,
  b: Partial<LocationCondition>,
): boolean {
  return locationKey(a) === locationKey(b);
}

export function hasDistrict(location: Partial<LocationCondition>): boolean {
  return Boolean(location.district_code && location.district_name);
}

export function normalizeLocationDraft(
  platform: Platform,
  cityName: string,
  conditions: LocationCondition[],
): LocationCondition[] {
  if (!Array.isArray(conditions)) return [];
  const seen = new Set<string>();
  const result: LocationCondition[] = [];
  for (const raw of conditions) {
    if (!raw || typeof raw !== "object") continue;
    const city = String(raw.city_name || cityName || "").trim();
    const districtName = String(raw.district_name || "").trim();
    const districtCode = String(raw.district_code || "").trim();
    const businessName = String(raw.business_name || "").trim();
    const businessCode = String(raw.business_code || "").trim();
    if (city !== cityName || !districtName || !districtCode) continue;
    const key = `${districtCode}:${businessCode}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const condition: LocationCondition = {
      platform,
      city_name: cityName,
      city_code: String(raw.city_code || "").trim(),
      district_name: districtName,
      district_code: districtCode,
      business_name: businessName || undefined,
      business_code: businessCode || undefined,
    };
    condition.label = locationLabel(condition);
    result.push(condition);
  }
  return result;
}

export function buildLocationPayload(locations: LocationCondition[]): LocationCondition[] {
  if (!Array.isArray(locations)) return [];
  return locations
    .filter(hasDistrict)
    .map((location) => {
      const payload: LocationCondition = {
        platform: location.platform,
        city_name: location.city_name,
        city_code: location.city_code,
        district_name: location.district_name,
        district_code: location.district_code,
      };
      if (location.business_name && location.business_code) {
        payload.business_name = location.business_name;
        payload.business_code = location.business_code;
      }
      return payload;
    });
}

export function locationCombinationCount(locations: LocationCondition[]): number {
  return Array.isArray(locations) ? locations.filter(hasDistrict).length : 0;
}

export function locationSummary(locations: LocationCondition[] | undefined | null): string {
  if (!Array.isArray(locations)) return "";
  const labels = locations
    .filter(hasDistrict)
    .map((location) => locationLabel(location))
    .filter((label, index, all) => label && all.indexOf(label) === index);
  return labels.join("、");
}

/** 清理岗位地点里的空分隔点，如“上海··/东莞··”只剩“上海/东莞”。 */
export function cleanJobLocation(raw: unknown): string {
  if (typeof raw !== "string") return "";
  return raw
    .split("·")
    .map((part) => part.trim())
    .filter(Boolean)
    .join(" · ");
}
