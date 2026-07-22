import { buildSearchScriptParams, partitionPipelineResult } from "../discovery";

describe("discovery helpers", () => {
  it("keeps priority, consider and uncertain results in separate decision zones", () => {
    const groups = partitionPipelineResult({
      jobs: [
        { job_id: "p", title: "优先投递", verdict: "priority" },
        { job_id: "c", title: "可以考虑", verdict: "consider" },
        { job_id: "legacy", title: "旧匹配结果", verdict: "match" },
        { job_id: "n", title: "不匹配", verdict: "not_match" },
        { job_id: "u", title: "待确认", verdict: "uncertain" },
      ],
      dropped: [{ job_id: "d", title: "粗筛剔除" }],
    });

    expect(groups.priority.map((job) => job.job_id)).toEqual(["p"]);
    expect(groups.considered.map((job) => job.job_id)).toEqual(["c", "legacy"]);
    expect(groups.notRecommended.map((job) => job.job_id)).toEqual(["n"]);
    expect(groups.uncertain.map((job) => job.job_id)).toEqual(["u"]);
    expect(groups.dropped.map((job) => job.job_id)).toEqual(["d"]);
  });

  it("builds broad-search parameters from only keywords and cities", () => {
    expect(buildSearchScriptParams(
      ["Python 后端", " Python 后端 ", "FastAPI"],
      ["上海", " 杭州 ", ""],
    )).toEqual({
      keyword: "Python 后端,FastAPI",
      city: ["上海", "杭州"],
      filters: {},
    });
  });
});
