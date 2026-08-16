import { useLocationDraft } from "../useLocationDraft";
import type { LocationCondition } from "../../types";

function bossLocation(district = "浦东新区", code = "310115"): LocationCondition {
  return {
    platform: "boss",
    city_name: "上海",
    city_code: "101020100",
    district_name: district,
    district_code: code,
  };
}

function zhilianLocation(district = "朝阳区", code = "2006"): LocationCondition {
  return {
    platform: "zhilian",
    city_name: "北京",
    city_code: "530",
    district_name: district,
    district_code: code,
  };
}

describe("useLocationDraft", () => {
  let draft: ReturnType<typeof useLocationDraft>;

  beforeEach(() => {
    draft = useLocationDraft();
    draft.reset();
  });

  afterEach(() => {
    draft.reset();
  });

  it("keeps location drafts per platform", () => {
    draft.setLocations("boss", "上海", [bossLocation()]);
    draft.setLocations("zhilian", "北京", [zhilianLocation()]);
    expect(draft.allLocations("boss", ["上海"])).toHaveLength(1);
    expect(draft.allLocations("zhilian", ["北京"])).toHaveLength(1);
    expect(draft.allLocations("boss", ["北京"])).toHaveLength(0);
  });

  it("does not leak district codes across platform switch", () => {
    draft.setLocations("boss", "上海", [bossLocation()]);
    draft.setLocations("zhilian", "上海", [
      { ...zhilianLocation(), city_name: "上海", city_code: "538" },
    ]);
    const boss = draft.getLocations("boss", "上海");
    const zhilian = draft.getLocations("zhilian", "上海");
    expect(boss[0].district_code).toBe("310115");
    expect(zhilian[0].district_code).toBe("2006");
  });

  it("clears a single city without touching other cities", () => {
    draft.setLocations("boss", "上海", [bossLocation()]);
    draft.setLocations("boss", "北京", [{ ...bossLocation(), city_name: "北京", city_code: "101010100", district_name: "朝阳区", district_code: "1" }]);
    draft.clearLocations("boss", "上海");
    expect(draft.getLocations("boss", "上海")).toEqual([]);
    expect(draft.allLocations("boss", ["北京"])).toHaveLength(1);
  });

  it("keeps old drafts without locations as city-level", () => {
    expect(draft.allLocations("boss", ["上海"])).toEqual([]);
    expect(draft.getLocations("boss", "上海")).toEqual([]);
  });

  it("resets all platforms", () => {
    draft.setLocations("boss", "上海", [bossLocation()]);
    draft.setLocations("zhilian", "北京", [zhilianLocation()]);
    draft.reset();
    expect(draft.allLocations("boss", ["上海"])).toEqual([]);
    expect(draft.allLocations("zhilian", ["北京"])).toEqual([]);
  });
});
