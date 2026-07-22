import { buildFilterSummary, isTerminalScreeningStatus } from "../screening";

describe("screening helpers", () => {
  it("summarizes active filters without hiding execution limits", () => {
    const options = {
      city: [{ label: "不限", value: "" }, { label: "上海", value: "101020100" }],
      salary: [{ label: "不限", value: "" }, { label: "20-50K", value: "406" }],
    };
    expect(buildFilterSummary(
      { city: "101020100", salary: "406" },
      options,
      "Python",
      2,
      6,
    )).toBe("关键词：Python · 上海 · 20-50K · 2 页 · 核验 6 条详情");
  });

  it("recognizes every persisted terminal status", () => {
    expect(isTerminalScreeningStatus("succeeded")).toBe(true);
    expect(isTerminalScreeningStatus("partial")).toBe(true);
    expect(isTerminalScreeningStatus("failed")).toBe(true);
    expect(isTerminalScreeningStatus("interrupted")).toBe(true);
    expect(isTerminalScreeningStatus("running")).toBe(false);
  });
});
