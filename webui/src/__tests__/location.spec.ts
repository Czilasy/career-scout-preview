import {
  buildLocationPayload,
  locationCombinationCount,
  locationLabel,
  locationSummary,
  normalizeLocationDraft,
  cleanJobLocation,
} from "../location";
import type { LocationCondition } from "../types";

function location(overrides: Partial<LocationCondition> = {}): LocationCondition {
  return {
    platform: "boss",
    city_name: "上海",
    city_code: "101020100",
    district_name: "浦东新区",
    district_code: "310115",
    ...overrides,
  };
}

describe("location pure functions", () => {
  it("builds city / district / business labels", () => {
    expect(locationLabel(location())).toBe("上海 · 浦东新区");
    expect(locationLabel(location({ business_name: "张江", business_code: "39" })))
      .toBe("上海 · 浦东新区 · 张江");
  });

  it("normalizes draft by platform and city and deduplicates", () => {
    const result = normalizeLocationDraft("boss", "上海", [
      location(),
      location(),
      location({ district_name: "徐汇区", district_code: "310104" }),
      location({ platform: "zhilian" }),
    ]);
    expect(result).toHaveLength(2);
    expect(result.every((item) => item.platform === "boss")).toBe(true);
    expect(result[0].label).toBe("上海 · 浦东新区");
  });

  it("builds payload without label and with optional business", () => {
    const payload = buildLocationPayload([
      location({ business_name: "北蔡", business_code: "154" }),
      location({ district_name: "徐汇区", district_code: "310104" }),
    ]);
    expect(payload[0]).not.toHaveProperty("label");
    expect(payload[0].business_code).toBe("154");
    expect(payload[1]).not.toHaveProperty("business_code");
  });

  it("counts only district-level locations", () => {
    expect(locationCombinationCount([location(), location()])).toBe(2);
    expect(locationCombinationCount([{} as LocationCondition])).toBe(0);
  });

  it("joins summaries with Chinese separator", () => {
    expect(locationSummary([
      location(),
      location({ district_name: "徐汇区", district_code: "310104" }),
    ])).toBe("上海 · 浦东新区、徐汇区");
    expect(locationSummary([])).toBe("");
    expect(locationSummary(null)).toBe("");
  });

  it("removes empty location dots", () => {
    expect(cleanJobLocation("上海··")).toBe("上海");
    expect(cleanJobLocation("东莞··")).toBe("东莞");
    expect(cleanJobLocation("上海 · 浦东新区 · 张江")).toBe("上海 · 浦东新区 · 张江");
    expect(cleanJobLocation("北京·朝阳区")).toBe("北京 · 朝阳区");
    expect(cleanJobLocation("")).toBe("");
    expect(cleanJobLocation(null)).toBe("");
  });
});
