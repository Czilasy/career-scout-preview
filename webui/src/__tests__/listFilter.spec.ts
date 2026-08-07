import { describe, expect, it } from "vitest";
import {
  FILTER_GROUPS,
  SORT_OPTIONS,
  countActiveFilters,
  emptyFilterState,
  filterJobs,
  parseSalaryRange,
  salaryBandOf,
  salaryValue,
  sortJobs,
} from "../listFilter";
import type { JobItem } from "../types";

function job(partial: Partial<JobItem> = {}): JobItem {
  return {
    job_id: "job-1",
    title: "Python 后端",
    platform: "boss" as const,
    ...partial,
  };
}

describe("parseSalaryRange（合同 §3）", () => {
  it("unit exclusion comes before digit extraction", () => {
    expect(parseSalaryRange("150-200元/天")).toBeNull();
    expect(parseSalaryRange("200-300元/日")).toBeNull();
    expect(parseSalaryRange("80元/小时")).toBeNull();
    expect(parseSalaryRange("日薪 300")).toBeNull();
    expect(parseSalaryRange("按次计酬")).toBeNull();
  });

  it("rejects empty, 面议 and non-numeric text", () => {
    expect(parseSalaryRange("")).toBeNull();
    expect(parseSalaryRange("面议")).toBeNull();
    expect(parseSalaryRange("薪资面议")).toBeNull();
    expect(parseSalaryRange(undefined)).toBeNull();
  });

  it("parses standard K ranges (first digit is lower bound, max is upper bound)", () => {
    expect(parseSalaryRange("4-6K")).toEqual([4, 6]);
    expect(parseSalaryRange("5-8K")).toEqual([5, 8]);
    expect(parseSalaryRange("10-15K")).toEqual([10, 15]);
    expect(parseSalaryRange("25-35K")).toEqual([25, 35]);
  });

  it("ignores bonus suffixes for the band; sort value uses the lower bound", () => {
    expect(parseSalaryRange("10-15K·13薪")).toEqual([10, 15]);
    expect(parseSalaryRange("20K以上")).toEqual([20, 20]);
    // 排序数值 = 区间下限（范围薪资取最低）
    expect(salaryValue("10-15K·13薪")).toBe(10);
  });

  it("first digit below 3 is not a monthly K lower bound", () => {
    expect(parseSalaryRange("1-2K")).toBeNull();
  });
});

describe("salaryBandOf（合同 §3 档位判定，以区间下限为准）", () => {
  it("maps lower bounds to single bands (no overlap)", () => {
    expect(salaryBandOf(parseSalaryRange("4-6K"))).toBe("lt5");
    expect(salaryBandOf(parseSalaryRange("5-8K"))).toBe("5-10");
    expect(salaryBandOf(parseSalaryRange("10-15K"))).toBe("10-15");
    expect(salaryBandOf(parseSalaryRange("25-35K"))).toBe("15up");
    expect(salaryBandOf(parseSalaryRange("15-20K"))).toBe("15up");
    expect(salaryBandOf(parseSalaryRange("20K以上"))).toBe("15up");
  });

  it("returns null for unparseable salaries", () => {
    expect(salaryBandOf(null)).toBeNull();
    expect(salaryBandOf(parseSalaryRange("面议"))).toBeNull();
  });
});

describe("countActiveFilters（合同 §2 徽标）", () => {
  it("counts every selected option across groups; sentinel is never stored", () => {
    const state = emptyFilterState();
    expect(countActiveFilters(state)).toBe(0);
    state.salary = ["5-10"];
    state.experience = ["应届生"];
    state.welfare = ["双休"];
    expect(countActiveFilters(state)).toBe(3);
  });
});

describe("filterJobs（合同 §2 组内 OR、组间 AND）", () => {
  const jobs: JobItem[] = [
    job({ job_id: "a", salary: "25-35K", experience: "3-5年", degree: "本科", extra: { welfare_list: ["五险一金", "双休"] } }),
    job({ job_id: "b", salary: "6-8K", experience: "应届生", degree: "大专", extra: { welfare_list: ["五险一金"] } }),
    job({ job_id: "c", salary: "面议", experience: "经验不限", degree: "", extra: {} }),
    job({ job_id: "d", salary: "12-18K", experience: "5-10年", degree: "硕士", extra: { welfare_list: ["双休"] } }),
    job({ job_id: "e", salary: "150-200元/天", experience: "", degree: "本科", extra: {} }),
  ];

  it("returns the same array reference when nothing is selected", () => {
    expect(filterJobs(jobs, emptyFilterState())).toBe(jobs);
  });

  it("filters by salary band using lower bound (no overlap between bands)", () => {
    const r1 = filterJobs(jobs, { ...emptyFilterState(), salary: ["15up"] });
    expect(r1.map((item) => item.job_id)).toEqual(["a"]);
    // 12-18K 下限 12 → 只落 10-15 档；面议/元/天 不落任何档
    const r2 = filterJobs(jobs, { ...emptyFilterState(), salary: ["10-15"] });
    expect(r2.map((item) => item.job_id)).toEqual(["d"]);
    const r3 = filterJobs(jobs, { ...emptyFilterState(), salary: ["lt5"] });
    expect(r3.map((item) => item.job_id)).toEqual([]);
    expect(r2).not.toContain(jobs[2]);
    expect(r2).not.toContain(jobs[4]);
  });

  it("filters by experience with exact band strings; missing experience passes nothing", () => {
    const r = filterJobs(jobs, { ...emptyFilterState(), experience: ["5-10年"] });
    expect(r.map((item) => item.job_id)).toEqual(["d"]);
    const fresh = filterJobs(jobs, { ...emptyFilterState(), experience: ["应届生"] });
    expect(fresh.map((item) => item.job_id)).toEqual(["b"]);
    // 「经验不限」不匹配任何具体档位；experience 为空同样不匹配
    const any = filterJobs(jobs, { ...emptyFilterState(), experience: ["10年以上"] });
    expect(any).toHaveLength(0);
  });

  it("filters by degree with prefix-tolerant contains match", () => {
    const bachelor = filterJobs(jobs, { ...emptyFilterState(), degree: ["本科"] });
    expect(bachelor.map((item) => item.job_id)).toEqual(["a", "e"]);
    // 统招本科 前缀 → 包含匹配
    const prefix = filterJobs([job({ job_id: "f", degree: "统招本科" })], { ...emptyFilterState(), degree: ["本科"] });
    expect(prefix.map((item) => item.job_id)).toEqual(["f"]);
    // degree 为空：不匹配任何具体档位
    const master = filterJobs(jobs, { ...emptyFilterState(), degree: ["硕士"] });
    expect(master.map((item) => item.job_id)).toEqual(["d"]);
  });

  it("filters by welfare from extra.welfare_list exactly; missing key never matches", () => {
    const r = filterJobs(jobs, { ...emptyFilterState(), welfare: ["双休"] });
    expect(r.map((item) => item.job_id)).toEqual(["a", "d"]);
    // 旧结果/智联岗位无 welfare_list 键 → 不满足
    const legacy = filterJobs([job({ job_id: "g", extra: undefined })], { ...emptyFilterState(), welfare: ["五险一金"] });
    expect(legacy).toHaveLength(0);
    const empty = filterJobs([job({ job_id: "h", extra: { welfare_list: [] } })], { ...emptyFilterState(), welfare: ["五险一金"] });
    expect(empty).toHaveLength(0);
  });

  it("combines groups with AND semantics; missing jd/fields are not fabricated", () => {
    const state = {
      ...emptyFilterState(),
      salary: ["15up" as const],
      experience: ["3-5年"],
      degree: ["本科"],
    };
    expect(filterJobs(jobs, state).map((item) => item.job_id)).toEqual(["a"]);
  });

  it("FILTER_GROUPS matches the contract: 4 groups with sentinel markers and welfare note", () => {
    expect(FILTER_GROUPS.map((group) => group.key)).toEqual(["salary", "experience", "degree", "welfare"]);
    expect(FILTER_GROUPS.find((group) => group.key === "salary")?.sentinel).toBeUndefined();
    expect(FILTER_GROUPS.find((group) => group.key === "experience")?.sentinel).toBe("不限");
    expect(FILTER_GROUPS.find((group) => group.key === "degree")?.sentinel).toBe("不限");
    expect(FILTER_GROUPS.find((group) => group.key === "welfare")?.note).toContain("智联岗位暂不支持福利筛选");
    const expOptions = FILTER_GROUPS.find((group) => group.key === "experience")!.options.map((o) => o.value);
    expect(expOptions).toEqual(["应届生", "1-3年", "3-5年", "5-10年", "10年以上"]);
  });
});

describe("sortJobs（合同 §4 稳定排序、面议沉底）", () => {
  const jobs: JobItem[] = [
    job({ job_id: "a", salary: "25-35K" }),
    job({ job_id: "b", salary: "6-8K" }),
    job({ job_id: "c", salary: "面议" }),
    job({ job_id: "d", salary: "12-18K" }),
  ];

  it("default keeps original order", () => {
    expect(sortJobs(jobs, "default").map((item) => item.job_id)).toEqual(["a", "b", "c", "d"]);
  });

  it("disabled sort keys (published_desc / match_desc) keep original order", () => {
    expect(sortJobs(jobs, "published_desc").map((item) => item.job_id)).toEqual(["a", "b", "c", "d"]);
    expect(sortJobs(jobs, "match_desc").map((item) => item.job_id)).toEqual(["a", "b", "c", "d"]);
  });

  it("does not mutate the input array", () => {
    const snapshot = jobs.map((item) => item.job_id).join(",");
    sortJobs(jobs, "salary_desc");
    expect(jobs.map((item) => item.job_id).join(",")).toBe(snapshot);
  });

  it("sorts by upper bound desc with unparseable salaries pinned at the end", () => {
    expect(sortJobs(jobs, "salary_desc").map((item) => item.job_id)).toEqual(["a", "d", "b", "c"]);
  });

  it("sorts asc with unparseable salaries pinned at the end too", () => {
    expect(sortJobs(jobs, "salary_asc").map((item) => item.job_id)).toEqual(["b", "d", "a", "c"]);
  });

  it("is stable for equal salaries", () => {
    const equal = [
      job({ job_id: "x1", salary: "10-15K" }),
      job({ job_id: "x2", salary: "10-15K" }),
      job({ job_id: "x3", salary: "10-15K" }),
    ];
    // 排序键相同 → 保持原始相对顺序
    expect(sortJobs(equal, "salary_desc").map((item) => item.job_id)).toEqual(["x1", "x2", "x3"]);
    expect(sortJobs(equal, "salary_asc").map((item) => item.job_id)).toEqual(["x1", "x2", "x3"]);
  });

  it("sorts salary asc by the range lower bound (user-verified regression: 4-5K/3-5K/5-6K/4-6K)", () => {
    const ranges = [
      job({ job_id: "r1", salary: "4-5K" }),
      job({ job_id: "r2", salary: "3-5K" }),
      job({ job_id: "r3", salary: "5-6K" }),
      job({ job_id: "r4", salary: "4-6K" }),
    ];
    // 按区间下限：3 → 4 → 4 → 5；下限相同（r1/r4 均 4）保持原始相对顺序
    expect(sortJobs(ranges, "salary_asc").map((item) => item.job_id)).toEqual(["r2", "r1", "r4", "r3"]);
  });

  it("SORT_OPTIONS exposes five entries with disabled published/match options", () => {
    expect(SORT_OPTIONS.map((option) => option.key)).toEqual([
      "default", "salary_desc", "salary_asc", "published_desc", "match_desc",
    ]);
    expect(SORT_OPTIONS.find((option) => option.key === "published_desc")).toMatchObject({
      disabled: true, note: "数据补齐后开放",
    });
    expect(SORT_OPTIONS.find((option) => option.key === "match_desc")).toMatchObject({
      disabled: true, note: "数据补齐后开放",
    });
  });
});