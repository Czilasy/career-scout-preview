import { computed, ref } from "vue";
import { hasLiveTaskState, hasUnfinishedRound, useDiscoveryState } from "../useDiscoveryState";
import { useDiscoveryResults } from "../useDiscoveryResults";
import type { ResultsNeeds, RoundFlowLike } from "../discoveryDeps";
import { apiRequest } from "../../api";
import { setThemePlatform } from "../useTheme";
import { useDiscoverySceneState } from "../useDiscoverySceneState";

vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
  errorMessage: (_error: unknown, fallback: string) => fallback,
  settingsApi: { get: vi.fn(), save: vi.fn() },
  userFacingMessage: (_error: unknown, fallback: string) => fallback,
  ApiError: class ApiError extends Error {},
}));

const apiRequestMock = apiRequest as unknown as ReturnType<typeof vi.fn>;

const roundFlow: RoundFlowLike = {
  busyAction: "",
  roundContext: null,
  roundContexts: {},
  suppressProfileWatch: false,
  startRecrawl: vi.fn(async () => {}),
  clearRoundContext: vi.fn(),
  restoreRoundContext: vi.fn(() => false),
  registerRoundContext: vi.fn(),
  openScreenFinishChoice: vi.fn(() => false),
};

function makeDeps(): ResultsNeeds {
  return {
    emit: vi.fn(),
    notify: vi.fn(),
    pollRecrawl: vi.fn(async () => {}),
    pollTask: vi.fn(async () => {}),
    props: { profileId: "platform-result-test" },
    roundFlow,
    setDraftPlatform: vi.fn(),
  };
}

function result(platform: "boss" | "zhilian", runId: string) {
  return {
    ok: true,
    has_result: true,
    source_run_id: runId,
    platform,
    status: "scraped_only",
    started_at: 2_000,
    finished_at: 3_000,
    result: {
      ok: true,
      jobs: [{
        job_id: `${platform}-job`,
        platform,
        title: `${platform} 岗位`,
        verdict: "",
      }],
      dropped: [],
      total_scraped: 1,
      total_kept: 0,
      total_matched: 0,
      total_dropped: 0,
    },
  };
}

describe("useDiscoveryResults latest platform identity", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
    document.documentElement.setAttribute("data-platform", "boss");
    setThemePlatform("boss");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads only the newest single-platform round instead of merging a stale platform round", async () => {
    const boss = { ...result("boss", "boss-old"), started_at: 1_000 };
    const zhilian = result("zhilian", "zhilian-latest");
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.includes("platform=boss")) return boss;
      if (url.includes("platform=zhilian")) return zhilian;
      return zhilian;
    });

    const state = useDiscoveryState({ profileId: "platform-result-test" }, () => {});
    const results = useDiscoveryResults(state, makeDeps());

    await results.loadLatestResult();

    const latestCalls = apiRequestMock.mock.calls.filter(([url]) =>
      String(url).includes("/api/latest-pipeline-result"));
    expect(latestCalls).toEqual([
      ["/api/latest-pipeline-result?profile_id=platform-result-test"],
    ]);
    expect(state.pipelineResult.value?.jobs?.map((job) => job.platform)).toEqual(["zhilian"]);
    expect(state.pipelineResult.value?.total_scraped).toBe(1);
    expect(state.platformState.result).toBe("zhilian");
    expect(document.documentElement.getAttribute("data-platform")).toBe("zhilian");
  });

  it("loads current Flow results without falling back to another platform or round", async () => {
    const currentFlowId = ref("flow-current");
    apiRequestMock.mockResolvedValue({
      ok: true,
      results: {
        flow_id: "flow-current",
        profile_id: "platform-result-test",
        selection: "all",
        status: "running",
        tracks: [
          {
            platform: "boss",
            status: "done",
            stage: "complete",
            result_run_id: "boss-result",
            ai_screened: true,
            screened_count: 1,
            jobs: [{ job_id: "boss-job", platform: "boss", title: "BOSS 岗位", verdict: "match" }],
            dropped: [],
          },
          {
            platform: "zhilian",
            status: "failed",
            stage: "ai",
            scrape_run_id: "zhilian-scrape",
            ai_screened: false,
            screened_count: 0,
            unfinished_ai_screening: true,
            message: "未完成 AI 筛选",
            jobs: [{ job_id: "zhilian-job", platform: "zhilian", title: "智联岗位", verdict: "" }],
            dropped: [],
          },
        ],
        jobs: [],
        screened_count: 1,
      },
    });

    const state = useDiscoveryState({ profileId: "platform-result-test" }, () => {});
    const results = useDiscoveryResults(state, makeDeps(), currentFlowId);

    await results.loadLatestResult();

    expect(apiRequestMock).toHaveBeenCalledWith(
      "/api/flows/flow-current/results?profile_id=platform-result-test",
    );
    expect(apiRequestMock.mock.calls.some(([url]) => String(url).includes("latest-pipeline-result"))).toBe(false);
    expect(state.pipelineResult.value?.jobs?.map((job) => job.platform)).toEqual(["boss", "zhilian"]);
    expect(state.pipelineResult.value?.total_scraped).toBe(2);
    expect(state.pipelineResult.value?.total_matched).toBe(1);
    expect(state.resultRunIds.value).toEqual({ boss: "boss-result", zhilian: "" });
    expect(state.pipelineResult.value?.jobs?.[0]?._result_run_id).toBe("boss-result");
    expect(state.pipelineResult.value?.jobs?.[1]?._result_run_id).toBe("zhilian-scrape");
    expect(state.screenSnapshot.value?.status).toBe("failed");
    expect(state.screenSnapshot.value?.error).toBe("未完成 AI 筛选");
  });

  it("does not rewrite the job platform supplied by the Flow results API", async () => {
    const currentFlowId = ref("flow-platform-identity");
    const state = useDiscoveryState({ profileId: "flow-platform-identity-test" }, () => {});
    const results = useDiscoveryResults(
      state,
      makeDeps(),
      currentFlowId,
      async () => ({
        flow_id: "flow-platform-identity",
        selection: "all",
        status: "done",
        tracks: [
          {
            platform: "boss",
            status: "done",
            result_run_id: "boss-result",
            jobs: [{ job_id: "boss-job", platform: "zhilian", title: "BOSS 岗位" }],
            dropped: [{ job_id: "boss-dropped", platform: "zhilian", title: "BOSS 已筛除" }],
          },
          {
            platform: "zhilian",
            status: "done",
            result_run_id: "zhilian-result",
            jobs: [{ job_id: "zhilian-job", platform: "boss", title: "智联岗位" }],
            dropped: [],
          },
        ],
      }),
      ref<"all" | "boss" | "zhilian">("all"),
    );

    await results.loadLatestResult();

    expect(state.pipelineResult.value?.jobs?.map((job) => [job.title, job.platform])).toEqual([
      ["BOSS 岗位", "zhilian"],
      ["智联岗位", "boss"],
    ]);
    expect(state.pipelineResult.value?.dropped?.map((job) => [job.title, job.platform])).toEqual([
      ["BOSS 已筛除", "zhilian"],
    ]);
  });

  it("does not let a delayed all-platform Flow overwrite the selected single-platform Flow", async () => {
    const currentFlowId = ref("flow-all");
    const currentFlowSelection = ref<"all" | "boss" | "zhilian">("all");
    let releaseAll!: (payload: Record<string, unknown>) => void;
    const allResponse = new Promise<Record<string, unknown>>((resolve) => { releaseAll = resolve; });
    const fetchFlowResults = vi.fn(async (flowId: string) => {
      if (flowId === "flow-all") return allResponse;
      return {
        flow_id: "flow-boss",
        selection: "boss",
        status: "done",
        tracks: [{
          platform: "boss",
          status: "done",
          result_run_id: "boss-new-result",
          jobs: [{ job_id: "boss-new", platform: "boss", title: "当前 BOSS 结果" }],
          dropped: [],
        }],
      };
    });
    const state = useDiscoveryState({ profileId: "flow-result-race" }, () => {});
    const results = useDiscoveryResults(
      state,
      makeDeps(),
      currentFlowId,
      fetchFlowResults,
      currentFlowSelection,
    );

    const allLoad = results.loadLatestResult();
    currentFlowId.value = "flow-boss";
    currentFlowSelection.value = "boss";
    await results.loadLatestResult();
    expect(state.pipelineResult.value?.jobs?.map((job) => job.title)).toEqual(["当前 BOSS 结果"]);

    releaseAll({
      flow_id: "flow-all",
      selection: "all",
      status: "done",
      tracks: [{
        platform: "zhilian",
        status: "done",
        result_run_id: "stale-all-result",
        jobs: [{ job_id: "stale-all", platform: "zhilian", title: "过期 All 结果" }],
        dropped: [],
      }],
    });
    await allLoad;

    expect(state.pipelineResult.value?.jobs?.map((job) => job.title)).toEqual(["当前 BOSS 结果"]);
    // 迟到的流程响应可以整条丢掉，但它顺手写进 state 的结果来源必须一起作废：
    // 「重抓待确认」按 resultRunIds 定位 source_run_id，写脏了就会打到上一流程的轮次。
    expect(state.resultRunIds.value).toEqual({ boss: "boss-new-result", zhilian: "" });
    expect(fetchFlowResults).toHaveBeenCalledWith("flow-all");
    expect(fetchFlowResults).toHaveBeenCalledWith("flow-boss");
  });

  it("ignores an all-platform response that arrives after mode switch even before the single-platform response", async () => {
    const currentFlowId = ref("flow-all-before-single");
    const currentFlowSelection = ref<"all" | "boss" | "zhilian">("all");
    let releaseAll!: (payload: Record<string, unknown>) => void;
    let releaseBoss!: (payload: Record<string, unknown>) => void;
    const allResponse = new Promise<Record<string, unknown>>((resolve) => { releaseAll = resolve; });
    const bossResponse = new Promise<Record<string, unknown>>((resolve) => { releaseBoss = resolve; });
    const fetchFlowResults = vi.fn((flowId: string) => flowId === "flow-all-before-single" ? allResponse : bossResponse);
    const state = useDiscoveryState({ profileId: "flow-result-race-2" }, () => {});
    const results = useDiscoveryResults(
      state,
      makeDeps(),
      currentFlowId,
      fetchFlowResults,
      currentFlowSelection,
    );

    const allLoad = results.loadLatestResult();
    currentFlowId.value = "flow-boss-after-all";
    currentFlowSelection.value = "boss";
    const bossLoad = results.loadLatestResult();
    releaseAll({
      flow_id: "flow-all-before-single",
      selection: "all",
      status: "done",
      tracks: [{ platform: "zhilian", status: "done", jobs: [{ job_id: "stale", platform: "zhilian", title: "不应先显示" }], dropped: [] }],
    });
    await allLoad;
    expect(state.pipelineResult.value).toBeNull();
    releaseBoss({
      flow_id: "flow-boss-after-all",
      selection: "boss",
      status: "done",
      tracks: [{ platform: "boss", status: "done", jobs: [{ job_id: "boss-final", platform: "boss", title: "最终 BOSS 结果" }], dropped: [] }],
    });
    await bossLoad;
    expect(state.pipelineResult.value?.jobs?.map((job) => job.title)).toEqual(["最终 BOSS 结果"]);
  });

  it("ignores a Flow response whose response flow_id differs from the requested Flow", async () => {
    const currentFlowId = ref("flow-requested");
    const currentFlowSelection = ref<"all" | "boss" | "zhilian">("all");
    const state = useDiscoveryState({ profileId: "flow-response-id-check" }, () => {});
    const results = useDiscoveryResults(
      state,
      makeDeps(),
      currentFlowId,
      async () => ({
        flow_id: "flow-other",
        selection: "all",
        status: "done",
        tracks: [{ platform: "boss", status: "done", jobs: [{ job_id: "wrong-flow", platform: "boss", title: "错误 Flow" }], dropped: [] }],
      }),
      currentFlowSelection,
    );

    await results.loadLatestResult();

    expect(state.pipelineResult.value).toBeNull();
  });

  it("does not let a delayed global result overwrite Flow results after returning to All", async () => {
    const currentFlowId = ref("flow-all-global-race");
    const currentFlowSelection = ref<"all" | "boss" | "zhilian">("all");
    let releaseGlobal!: (payload: Record<string, unknown>) => void;
    const pendingGlobal = new Promise<Record<string, unknown>>((resolve) => { releaseGlobal = resolve; });
    const fetchFlowResults = vi.fn(async (flowId: string) => ({
      flow_id: flowId,
      selection: "all",
      status: "done",
      tracks: [{
        platform: "boss",
        status: "done",
        result_run_id: "flow-all-result",
        jobs: [{ job_id: "flow-all-job", platform: "boss", title: "当前 All Flow 结果" }],
        dropped: [],
      }],
    }));
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.includes("/api/latest-pipeline-result")) return pendingGlobal;
      return {};
    });
    const state = useDiscoveryState({ profileId: "global-flow-race" }, () => {});
    const results = useDiscoveryResults(
      state,
      makeDeps(),
      currentFlowId,
      fetchFlowResults,
      currentFlowSelection,
    );

    await results.loadLatestResult();
    currentFlowId.value = "";
    currentFlowSelection.value = "boss";
    const globalLoad = results.loadLatestResult();
    currentFlowId.value = "flow-all-global-race";
    currentFlowSelection.value = "all";
    await results.loadLatestResult();
    expect(state.pipelineResult.value?.jobs?.map((job) => job.title)).toEqual(["当前 All Flow 结果"]);

    releaseGlobal({
      ok: true,
      has_result: true,
      source_run_id: "late-global-result",
      platform: "boss",
      status: "done",
      result: {
        jobs: [{ job_id: "late-global-job", platform: "boss", title: "迟到全局结果" }],
        dropped: [],
        total_scraped: 1,
        total_kept: 1,
        total_matched: 1,
        total_dropped: 0,
      },
    });
    await globalLoad;

    expect(state.pipelineResult.value?.jobs?.map((job) => job.title)).toEqual(["当前 All Flow 结果"]);
    expect(fetchFlowResults).toHaveBeenCalledWith("flow-all-global-race");
  });

  it("uses the most recently completed Flow track for metadata and only its task ids for counts", async () => {
    const currentFlowId = ref("flow-track-order");
    const taskStateIds: string[] = [];
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.includes("/task-state/")) {
        taskStateIds.push(url.split("/task-state/")[1].split("?")[0]);
        return { status: "done", total: 1, success_count: 1, fail_count: 0, unstarted_count: 0 };
      }
      return {
        ok: true,
        results: {
          flow_id: "flow-track-order", status: "done",
          tracks: [
            {
              platform: "boss", status: "done", stage: "complete",
              result_run_id: "result-b", screen_run_id: "screen-b", scrape_run_id: "scrape-b",
              finished_at: 200,
              jobs: [{ job_id: "b-job", platform: "boss", title: "较早 BOSS 岗位" }], dropped: [],
            },
            {
              platform: "zhilian", status: "done", stage: "complete",
              result_run_id: "result-z", screen_run_id: "screen-z", scrape_run_id: "scrape-z",
              finished_at: 300,
              jobs: [{ job_id: "z-job", platform: "zhilian", title: "较新智联岗位" }], dropped: [],
            },
          ],
        },
      };
    });

    const state = useDiscoveryState({ profileId: "flow-track-order" }, () => {});
    const results = useDiscoveryResults(state, makeDeps(), currentFlowId);
    await results.loadLatestResult();

    expect(state.pipelineResult.value?.platform).toBe("zhilian");
    expect(state.pipelineResult.value?.jobs?.map((job) => job.title)).toContain("较新智联岗位");
    expect(taskStateIds).toEqual(expect.arrayContaining(["scrape-z", "screen-z"]));
    expect(taskStateIds).not.toContain("result-z");
  });

  // 合并轮次（一个流程两条任务线）的计数只认全部任务线的汇总：拿任何一条单平台
  // 线的 /api/task-state 覆盖合并结果，第 3 步面板就会与第 4 步真实岗位数不一致。
  it("sums restored counts across every Flow track instead of one platform's snapshot", async () => {
    const currentFlowId = ref("flow-merged-counts");
    const lineCounts: Record<string, { total: number; success: number; fail: number }> = {
      "scrape-b": { total: 10, success: 8, fail: 2 },
      "screen-b": { total: 10, success: 8, fail: 2 },
      "scrape-z": { total: 4, success: 3, fail: 1 },
      "screen-z": { total: 4, success: 3, fail: 1 },
    };
    const requested: string[] = [];
    const job = (platform: "boss" | "zhilian", index: number, verdict: string) => ({
      job_id: `${platform}-${index}`, platform_job_id: `${platform}-${index}`, platform,
      title: `${platform} 岗位 ${index}`, verdict,
    });
    apiRequestMock.mockImplementation(async (url: string) => {
      const match = /\/api\/task-state\/([^?]+)/.exec(String(url));
      if (match) {
        const id = decodeURIComponent(match[1]);
        requested.push(id);
        const counts = lineCounts[id];
        return counts ? {
          status: "completed", total: counts.total, success_count: counts.success,
          fail_count: counts.fail, unstarted_count: 0, source_total: counts.total,
        } : null;
      }
      return {
        ok: true,
        results: {
          flow_id: "flow-merged-counts", selection: "all", status: "done",
          tracks: [
            {
              platform: "boss", status: "done", stage: "complete",
              result_run_id: "result-b", screen_run_id: "screen-b", scrape_run_id: "scrape-b",
              finished_at: 100,
              jobs: Array.from({ length: 8 }, (_, i) => job("boss", i, "match")),
              dropped: Array.from({ length: 2 }, (_, i) => job("boss", 100 + i, "not_match")),
            },
            {
              platform: "zhilian", status: "done", stage: "complete",
              result_run_id: "result-z", screen_run_id: "screen-z", scrape_run_id: "scrape-z",
              finished_at: 200,
              jobs: Array.from({ length: 3 }, (_, i) => job("zhilian", i, "match")),
              dropped: [job("zhilian", 100, "not_match")],
            },
          ],
        },
      };
    });

    const state = useDiscoveryState({ profileId: "flow-merged-counts" }, () => {});
    const results = useDiscoveryResults(state, makeDeps(), currentFlowId);
    await results.loadLatestResult();

    expect([...requested].sort()).toEqual(["scrape-b", "scrape-z", "screen-b", "screen-z"]);
    expect(state.screenSnapshot.value?.total).toBe(14);
    expect(state.screenSnapshot.value?.success_count).toBe(11);
    expect(state.screenSnapshot.value?.fail_count).toBe(3);
    expect(state.scrapeSnapshot.value?.total).toBe(14);
    expect(state.scrapeSnapshot.value?.success_count).toBe(11);
    // 合并计数既不等于最新那条线，也不等于较早那条线。
    expect(state.screenSnapshot.value?.success_count).not.toBe(3);
    expect(state.screenSnapshot.value?.success_count).not.toBe(8);
  });

  // Flow 切换不动 workflowEpoch：计数写回必须自己认流程身份，否则上一个流程的
  // 任务快照会在切换之后落进新流程的面板。
  it("drops restored Flow counts that land after the Flow has switched", async () => {
    const currentFlowId = ref("flow-counts-before-switch");
    let releaseTaskState!: () => void;
    const gate = new Promise<void>((resolve) => { releaseTaskState = resolve; });
    apiRequestMock.mockImplementation(async (url: string) => {
      if (String(url).includes("/api/task-state/")) {
        await gate;
        return { status: "completed", total: 99, success_count: 99, fail_count: 0, unstarted_count: 0 };
      }
      return {
        ok: true,
        results: {
          flow_id: "flow-counts-before-switch", selection: "boss", status: "done",
          tracks: [{
            platform: "boss", status: "done", stage: "complete",
            result_run_id: "counts-result", screen_run_id: "counts-screen", scrape_run_id: "counts-scrape",
            jobs: [{ job_id: "b1", platform: "boss", title: "岗位", verdict: "match" }], dropped: [],
          }],
        },
      };
    });
    const state = useDiscoveryState({ profileId: "flow-counts-switch" }, () => {});
    const results = useDiscoveryResults(state, makeDeps(), currentFlowId);

    const load = results.loadLatestResult();
    await new Promise((resolve) => { setTimeout(resolve, 0); });
    currentFlowId.value = "flow-counts-after-switch";
    releaseTaskState();
    await load;

    // 结果来源在切换前仍然新鲜 → 照常提交；迟到的计数写回必须作废。
    expect(state.resultRunIds.value).toEqual({ boss: "counts-result", zhilian: "" });
    expect(state.screenSnapshot.value?.success_count).not.toBe(99);
    expect(state.screenSnapshot.value?.total).not.toBe(99);
  });

  it("prefers a result track over a newer scrape-only track for metadata", async () => {
    const currentFlowId = ref("flow-mixed-track-priority");
    apiRequestMock.mockResolvedValue({
      ok: true,
      results: {
        flow_id: "flow-mixed-track-priority", selection: "all", status: "done",
        tracks: [
          {
            platform: "boss", status: "done", result_run_id: "result-b", screen_run_id: "screen-b",
            finished_at: 100, updated_at: 100,
            jobs: [{ job_id: "boss-job", platform: "boss", title: "BOSS 结果" }], dropped: [],
          },
          {
            platform: "zhilian", status: "done", result_run_id: null, screen_run_id: null,
            finished_at: 200, updated_at: 999,
            jobs: [{ job_id: "zhilian-job", platform: "zhilian", title: "智联仅抓取" }], dropped: [],
          },
        ],
      },
    });
    const state = useDiscoveryState({ profileId: "flow-mixed-track-priority" }, () => {});
    const results = useDiscoveryResults(state, makeDeps(), currentFlowId);

    await results.loadLatestResult();

    expect(state.pipelineResult.value?.platform).toBe("boss");
    expect(state.pipelineResult.value?.jobs?.map((job) => job.title)).toEqual(["BOSS 结果", "智联仅抓取"]);
  });

  it("aligns the draft platform switch to the newest result platform on first load", async () => {
    const boss = { ...result("boss", "boss-old"), started_at: 1_000 };
    const zhilian = result("zhilian", "zhilian-latest");
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.includes("platform=boss")) return boss;
      return zhilian;
    });

    const state = useDiscoveryState({ profileId: "align-test" }, () => {});
    const deps = makeDeps();
    const results = useDiscoveryResults(state, deps);

    await results.loadLatestResult();

    // 首屏对齐：滑块跟到结果平台，避免「颜色智联 / 滑块 BOSS」
    expect(deps.setDraftPlatform).toHaveBeenCalledWith("zhilian");
    expect(deps.setDraftPlatform).toHaveBeenCalledTimes(1);
  });

  it("does not keep rewriting the draft platform on later result loads", async () => {
    const boss = { ...result("boss", "boss-old"), started_at: 1_000 };
    const zhilian = result("zhilian", "zhilian-latest");
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.includes("platform=boss")) return boss;
      return zhilian;
    });

    const state = useDiscoveryState({ profileId: "align-once" }, () => {});
    const deps = makeDeps();
    const results = useDiscoveryResults(state, deps);

    await results.loadLatestResult();
    await results.loadLatestResult();

    expect(deps.setDraftPlatform).toHaveBeenCalledTimes(1);
  });

  it("结果补齐后清掉「正在恢复上次的结果」骨架标记", async () => {
    apiRequestMock.mockResolvedValue(result("boss", "resume-run"));
    const state = useDiscoveryState({ profileId: "bootstrap-pending" }, () => {});
    const results = useDiscoveryResults(state, makeDeps());
    state.resultsBootstrapPending.value = true;

    await results.loadLatestResult();

    expect(state.resultLoaded.value).toBe(true);
    expect(state.resultsBootstrapPending.value).toBe(false);
  });

  it("已有最新结果时，回到最新不被旧的简历分析成功态带回第二页", async () => {
    const latest = {
      ...result("boss", "latest-run"),
      status: "done",
      started_at: 4_000,
    };
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.includes("platform=zhilian")) return { ok: true, has_result: false };
      return latest;
    });

    const state = useDiscoveryState({ profileId: "return-latest-after-analysis" }, () => {});
    const results = useDiscoveryResults(state, makeDeps());
    state.activeStep.value = "results";
    state.resultLoaded.value = true;
    state.pipelineResult.value = {
      jobs: [{ job_id: "history-job", platform: "boss", title: "历史岗位", verdict: "match" }],
      dropped: [],
    };
    state.pipelineResultRunId.value = "history-run";
    state.historyRound.value = {
      runId: "history-run",
      platform: "boss",
      status: "done",
      jobCount: 1,
    };
    state.platformBeforeHistory.value = "boss";
    state.resumeAnalysisPhase.value = "succeeded";
    state.resumeAnalysisLandOnReturn.value = () => {
      state.activeStep.value = "search";
    };

    await results.returnToLatest();

    expect(state.activeStep.value).toBe("results");
    expect(state.resultLoaded.value).toBe(true);
    expect(state.pipelineResult.value?.jobs?.[0]?.title).toBe("boss 岗位");
  });

  it("简历分析仍在进行时，暂存的旧结果不覆盖分析中的第一页", async () => {
    const latest = {
      ...result("boss", "old-run"),
      status: "done",
      started_at: 3_000,
    };
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.includes("platform=zhilian")) return { ok: true, has_result: false };
      return latest;
    });

    const state = useDiscoveryState({ profileId: "return-latest-while-analysis" }, () => {});
    const results = useDiscoveryResults(state, makeDeps());
    state.activeStep.value = "results";
    state.resultLoaded.value = true;
    state.pipelineResult.value = {
      jobs: [{ job_id: "history-job", platform: "boss", title: "历史岗位", verdict: "match" }],
      dropped: [],
    };
    state.pipelineResultRunId.value = "history-run";
    state.historyRound.value = {
      runId: "history-run",
      platform: "boss",
      status: "done",
      jobCount: 1,
    };
    state.platformBeforeHistory.value = "boss";
    state.resumeAnalysisPhase.value = "analyzing";
    state.resumeAnalysisLandOnReturn.value = () => {
      state.activeStep.value = "upload";
    };

    await results.returnToLatest();

    expect(state.activeStep.value).toBe("upload");
    expect(state.resultLoaded.value).toBe(false);
    expect(state.pipelineResult.value).toBeNull();
  });

  // 判活口径只有一份，落在树干 useDiscoveryState.hasLiveTaskState（含 flowActive）。
  // 结果层此前自己另写一份且不看 flowActive：全部平台在跑、领先平台已出结果时，
  // 首屏草稿对齐会清掉本轮抓取/筛选现场，并把「上次抓取已完成」式假终态快照
  // 写进仍在运行的进度面板。
  it("keeps the running Flow untouched when the leading platform result arrives", async () => {
    apiRequestMock.mockResolvedValue({
      ok: true,
      results: {
        flow_id: "flow-live",
        selection: "all",
        status: "running",
        tracks: [
          {
            platform: "boss", status: "done", stage: "complete", result_run_id: "boss-live-result",
            jobs: [{ job_id: "b1", platform: "boss", title: "BOSS 岗位", verdict: "match" }], dropped: [],
          },
          { platform: "zhilian", status: "running", jobs: [], dropped: [] },
        ],
      },
    });
    const state = useDiscoveryState({ profileId: "flow-live-draft" }, () => {});
    state.setFlowActive(true);
    state.setFlowLiveWorker(true);
    // Flow 运行期间 02/03 画面由 Flow 投影持有；结果层只剩上一轮的旧快照。
    // 判活必须只认树干那一份口径（含 flowActive），否则这里会被本轮结果顶掉。
    const seededScrape = { status: "completed", progress: { message: "上一轮抓取画面" }, logs: [], total: 3 };
    const seededScreen = { status: "completed", progress: { message: "上一轮筛选画面" }, logs: [], total: 3 };
    state.scrapeSnapshot.value = seededScrape as never;
    state.screenSnapshot.value = seededScreen as never;
    const deps = makeDeps();
    const results = useDiscoveryResults(state, deps, ref("flow-live"), undefined, ref<"all" | "boss" | "zhilian">("all"));

    await results.loadLatestResult({ preservePresentation: true });

    expect(deps.setDraftPlatform).not.toHaveBeenCalled();
    expect(JSON.stringify(state.scrapeSnapshot.value)).toContain("上一轮抓取画面");
    expect(JSON.stringify(state.screenSnapshot.value)).toContain("上一轮筛选画面");
    expect(state.pipelineResult.value?.jobs?.map((job) => job.title)).toEqual(["BOSS 岗位"]);
  });

  it("本轮结果没能载入时给出用户可见提示，且不残留错误态", async () => {
    apiRequestMock.mockRejectedValue(new Error("Request failed with status code 503"));
    const currentFlowId = ref("flow-unreachable");
    const state = useDiscoveryState({ profileId: "flow-result-failure" }, () => {});
    const deps = makeDeps();
    const results = useDiscoveryResults(state, deps, currentFlowId);

    await results.loadLatestResult();

    const notify = deps.notify as ReturnType<typeof vi.fn>;
    expect(notify).toHaveBeenCalledTimes(1);
    const [message, tone] = notify.mock.calls[0];
    expect(message).toContain("没能载入");
    expect(tone).toBe("warning");
    expect(String(message)).not.toMatch(/503|flow|results|tracks|profile_id/i);
    expect(state.pipelineResult.value).toBeNull();
    expect(state.resultLoaded.value).toBe(false);

    // 同一流程的自动重试不重复播报；换到另一个流程仍会提示一次。
    await results.loadLatestResult();
    expect(notify).toHaveBeenCalledTimes(1);
    currentFlowId.value = "flow-unreachable-2";
    await results.loadLatestResult();
    expect(notify).toHaveBeenCalledTimes(2);
  });

  // 刷新恢复的计数只认「两条路径都存在的来源」：旧接口顶层没有 screen_run_id，
  // 拿它当第二参会让筛选面板退回「已完成 0」（039 禁止入口缺补丁）。
  it("restores the screen panel counts from the real task snapshot after a refresh", async () => {
    apiRequestMock.mockImplementation(async (url: string) => {
      if (String(url).includes("/api/task-state/scrape-run-1")) {
        return { status: "completed", total: 16, success_count: 14, fail_count: 2, unstarted_count: 0 };
      }
      if (String(url).includes("/api/task-state/")) {
        return { status: "completed", total: 12, success_count: 9, fail_count: 1, unstarted_count: 2, pending_count: 3 };
      }
      return {
        ok: true, has_result: true, source_run_id: "screen-run-1", platform: "boss", status: "done",
        scrape_task_id: "scrape-run-1", started_at: 1, finished_at: 2,
        result: {
          jobs: [{ job_id: "j1", platform: "boss", title: "岗位", verdict: "match" }],
          dropped: [], total_scraped: 16, total_kept: 1, total_matched: 1, total_dropped: 15,
        },
      };
    });
    const state = useDiscoveryState({ profileId: "restore-round-counts" }, () => {});
    const results = useDiscoveryResults(state, makeDeps());

    await results.loadLatestResult();

    expect(state.scrapeSnapshot.value?.success_count).toBe(14);
    expect(state.screenSnapshot.value?.success_count).toBe(9);
    expect(state.screenSnapshot.value?.unstarted_count).toBe(2);
    expect(state.screenSnapshot.value?.pending_count).toBe(3);
  });

  // 用户主动点开历史轮是一次显式导航：手动持有期不得把它拦在原地，
  // 否则历史数据已装载却停在旧页，而「回到最新」入口只在结果页渲染，用户被困。
  it("lands on the results page when the user opens a history round after clicking an earlier step", () => {
    const state = useDiscoveryState({ profileId: "history-after-manual-hold" }, () => {});
    state.analysisReady.value = true;
    state.scrapeCompleted.value = true;
    state.resultLoaded.value = true;
    const results = useDiscoveryResults(state, makeDeps());
    expect(state.navigateStep("results", { source: "user" })).toBe("results");
    expect(state.navigateStep("search", { source: "user" })).toBe("search");

    results.enterHistoryRound({
      ok: true,
      has_result: true,
      source_run_id: "history-round-1",
      platform: "boss",
      status: "done",
      result: { jobs: [{ job_id: "h", platform: "boss", title: "历史岗位", verdict: "match" }], dropped: [] },
    } as never);

    expect(state.activeStep.value).toBe("results");
    expect(state.historyRound.value?.runId).toBe("history-round-1");
  });

  it("binds the current result scene to the result platform instead of the draft platform", () => {
    sessionStorage.clear();
    const state = useDiscoveryState({ profileId: "scene-result-platform" }, () => {});
    const results = useDiscoveryResults(state, makeDeps());
    const scene = useDiscoverySceneState();
    state.pipelineResultRunId.value = "run-result";
    state.platformState.setDraftPlatform("boss");
    state.draftPlatform.value = "boss";
    state.platformState.setResultPlatform("zhilian");
    scene.saveCurrent(
      { profileId: "scene-result-platform", runEpoch: "run-result", platform: "boss" },
      { selectedJobKey: "boss:draft-scene" },
    );
    scene.saveCurrent(
      { profileId: "scene-result-platform", runEpoch: "run-result", platform: "zhilian" },
      { selectedJobKey: "zhilian:result-scene" },
    );

    results.enterHistoryRound({
      ok: true,
      has_result: true,
      source_run_id: "history-run",
      platform: "zhilian",
      status: "done",
      result: { jobs: [], dropped: [] },
    });
    scene.archiveCurrentForNewRound("run-result");

    expect(scene.getHistory("run-result", {
      profileId: "scene-result-platform",
      runEpoch: "run-result",
      platform: "zhilian",
    }).selectedJobKey).toBe("zhilian:result-scene");
  });
});

function flowResult(jobs: Array<Record<string, unknown>>, sourceRunId: string) {
  return {
    ok: true,
    results: {
      flow_id: "flow-merge",
      profile_id: "merge-test",
      selection: "all",
      status: "running",
      tracks: [
        {
          platform: "boss",
          status: "done",
          stage: "complete",
          result_run_id: sourceRunId,
          ai_screened: true,
          screened_count: jobs.length,
          jobs,
          dropped: [],
        },
      ],
      jobs: [],
      screened_count: jobs.length,
    },
  };
}

describe("useDiscoveryResults Flow merge presentation", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("preservePresentation merges second Flow results without changing scene identity", async () => {
    apiRequestMock.mockImplementation(async (url: string) => {
      if (String(url).includes("latest-pipeline-result")) {
        return {
          ok: true, has_result: true, source_run_id: "boss-first", platform: "boss", status: "screened",
          started_at: 1, finished_at: 2,
          result: {
            ok: true,
            jobs: [{ job_id: "first", platform: "boss", title: "原岗位", verdict: "match" }],
            dropped: [], total_scraped: 1, total_kept: 1, total_matched: 1, total_dropped: 0,
          },
        };
      }
      return flowResult([{ job_id: "second", platform: "boss", title: "新岗位", verdict: "uncertain" }], "boss-second");
    });

    const state = useDiscoveryState({ profileId: "merge-test" }, () => {});
    state.pipelineResult.value = {
      platform: "boss",
      jobs: [{ job_id: "first", platform: "boss", title: "原岗位", verdict: "match" }],
      dropped: [], total_scraped: 1, total_kept: 1, total_matched: 1, total_dropped: 0,
    } as never;
    state.activeCategory.value = "uncertain";
    state.resultPlatformFilter.value = "boss";
    state.resultLoaded.value = true;
    const scene = useDiscoverySceneState();
    const epoch = scene.ensureRoundEpoch("merge-test");
    const sceneIdentity = { profileId: "merge-test", runEpoch: epoch, platform: "boss" as const };
    scene.saveCurrent(sceneIdentity, {
      selectedJobKey: "boss:first",
      sortKey: "salary_desc",
      listFilterDraft: { salary: ["5-10"], experience: [], degree: [], welfare: [] },
      listScrollTop: 144,
      jdScrollTop: 233,
      detailOpen: true,
      cardOpenStates: { "boss:first": true },
    });
    const results = useDiscoveryResults(state, {
      emit: vi.fn(), notify: vi.fn(), pollRecrawl: vi.fn(async () => {}), pollTask: vi.fn(async () => {}),
      props: { profileId: "merge-test" }, roundFlow: {} as never, setDraftPlatform: vi.fn(),
    }, ref("flow-merge"));

    await results.loadLatestResult({ preservePresentation: true });

    const mergedResult = state.pipelineResult.value as unknown as { jobs?: Array<{ job_id?: string }> } | null;
    const jobIds = mergedResult?.jobs?.map((job) => job.job_id);
    expect(jobIds).toContain("first");
    expect(jobIds).toContain("second");
    expect(state.activeCategory.value).toBe("uncertain");
    expect(state.resultPlatformFilter.value).toBe("boss");
    expect(state.resultEpoch.value).toBe(0);
    expect(scene.getCurrent(sceneIdentity)).toMatchObject({
      selectedJobKey: "boss:first",
      sortKey: "salary_desc",
      listFilterDraft: { salary: ["5-10"] },
      listScrollTop: 144,
      jdScrollTop: 233,
      detailOpen: true,
      cardOpenStates: { "boss:first": true },
    });
  });

  it("keeps the neutral all-platform theme while Flow results are merged", async () => {
    setThemePlatform("all");
    apiRequestMock.mockResolvedValue(flowResult([
      { job_id: "boss-result", platform: "boss", title: "BOSS 岗位", verdict: "match" },
    ], "boss-result"));
    const state = useDiscoveryState({ profileId: "merge-theme" }, () => {});
    const results = useDiscoveryResults(state, {
      emit: vi.fn(), notify: vi.fn(), pollRecrawl: vi.fn(async () => {}), pollTask: vi.fn(async () => {}),
      props: { profileId: "merge-theme" }, roundFlow: {} as never, setDraftPlatform: vi.fn(),
    }, ref("flow-merge-theme"));

    await results.loadLatestResult({ preservePresentation: true });

    expect(document.documentElement.getAttribute("data-platform")).toBe("all");
  });

  it("keeps a single-platform Flow result on its brand theme", async () => {
    setThemePlatform("all");
    apiRequestMock.mockResolvedValue({
      ok: true,
      results: {
        flow_id: "flow-single-theme",
        selection: "boss",
        status: "done",
        tracks: [{
          platform: "boss", status: "done", stage: "complete", result_run_id: "boss-result",
          jobs: [{ job_id: "boss-theme-job", platform: "boss", title: "BOSS 岗位" }], dropped: [],
        }],
      },
    });
    const state = useDiscoveryState({ profileId: "single-theme" }, () => {});
    const results = useDiscoveryResults(state, makeDeps(), ref("flow-single-theme"));

    await results.loadLatestResult({ preservePresentation: true });

    expect(document.documentElement.getAttribute("data-platform")).toBe("boss");
  });

  it("deduplicates by platform job identity while retaining equal ids from both platforms", async () => {
    const currentFlowId = ref("flow-platform-job-identity");
    apiRequestMock.mockResolvedValue({
      ok: true,
      results: {
        flow_id: "flow-platform-job-identity",
        status: "running",
        tracks: [
          {
            platform: "boss",
            status: "done",
            result_run_id: "boss-result",
            jobs: [
              { job_id: "shared", platform_job_id: "boss-shared", platform: "boss", title: "BOSS 原岗位" },
              { job_id: "shared", platform_job_id: "boss-shared", platform: "boss", title: "BOSS 重复岗位" },
            ],
            dropped: [],
          },
          {
            platform: "zhilian",
            status: "done",
            result_run_id: "zhilian-result",
            jobs: [
              { job_id: "shared", platform_job_id: "zhilian-shared", platform: "zhilian", title: "智联岗位" },
              { job_id: "shared", platform_job_id: "zhilian-shared", platform: "zhilian", title: "智联重复岗位" },
            ],
            dropped: [],
          },
        ],
      },
    });
    const state = useDiscoveryState({ profileId: "platform-job-identity" }, () => {});
    state.pipelineResult.value = {
      platform: "boss",
      jobs: [{ job_id: "shared", platform_job_id: "boss-shared", platform: "boss", title: "BOSS 旧岗位" }],
      dropped: [],
    } as never;
    const results = useDiscoveryResults(state, makeDeps(), currentFlowId);

    await results.loadLatestResult({ preservePresentation: true });

    const jobs = ((state.pipelineResult.value as unknown as {
      jobs?: Array<{ platform?: string; platform_job_id?: string }>;
    } | null)?.jobs || []);
    expect(jobs).toHaveLength(2);
    expect(jobs.map((job) => `${job.platform}:${job.platform_job_id}`)).toEqual([
      "boss:boss-shared",
      "zhilian:zhilian-shared",
    ]);
  });
});

// SPEC 046 第五轮：判活只有一份口径，落点也必须同源，并跟随流程投影取**最深**的进行中
// 阶段。真实现场——流程还在跑（流程活动线为真）、本地三个任务快照仍是上一轮终态，用户
// 在历史轮点「回到最新」：落点读不到活任务，就会把进行中这一轮的结果当最新拉回来并落进
// 04；落点只会 02，用户照样看不到 03 正在跑的筛选进度。
describe("useDiscoveryResults.returnToLatest 与树干判活同源（SPEC 046 第五轮）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  /** 造「流程在跑 + 正在看历史轮」的现场（每个 state 实例独立，无需跨用例清场）。 */
  function makeFlowLiveHistoryScene(profileId: string) {
    const state = useDiscoveryState({ profileId }, () => {});
    state.pausedRunId.value = "";
    state.interruptedRunId.value = "";
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = null;
    state.recrawlSnapshot.value = null;
    const results = useDiscoveryResults(state, makeDeps());
    state.activeStep.value = "results";
    state.resultLoaded.value = true;
    state.pipelineResult.value = {
      jobs: [{ job_id: "history-job", platform: "boss", title: "历史岗位", verdict: "match" }],
      dropped: [],
    };
    state.pipelineResultRunId.value = "history-run";
    state.historyRound.value = { runId: "history-run", platform: "boss", status: "done", jobCount: 1 };
    state.platformBeforeHistory.value = "boss";
    // 流程还在跑：活体投影与活动线在同一 tick 里一起到位（协调器口径）。
    state.setFlowActive(true);
    state.setFlowLiveWorker(true);
    return { state, results };
  }

  function latestResultCalls(): number {
    return apiRequestMock.mock.calls.filter(([url]) =>
      String(url).includes("latest-pipeline-result")).length;
  }

  it("只有流程活动线时回最新：不请求最新结果，落回任务真实进度页", async () => {
    const { state, results } = makeFlowLiveHistoryScene("return-latest-flow-live");

    const returned = await results.returnToLatest();

    expect(hasLiveTaskState(state)).toBe(true);
    expect(returned).toBe("search");
    expect(state.activeStep.value).toBe("search");
    expect(state.enabledSteps.value).toContain("search");
    expect(latestResultCalls()).toBe(0);
  });

  it("投影已开到 03（抓取已完成、AI 筛选在跑）：回最新落 03，不落 02", async () => {
    const { state, results } = makeFlowLiveHistoryScene("return-latest-flow-screen");
    state.setFlowReachableSteps(new Set(["search", "screen"]));

    const returned = await results.returnToLatest();

    expect(returned).toBe("screen");
    expect(state.activeStep.value).toBe("screen");
    expect(state.enabledSteps.value).toContain("screen");
    expect(latestResultCalls()).toBe(0);
  });
});

describe("useDiscoveryResults 判活谓词纯度（SPEC 046 v2「状态词表」）", () => {
  // 结果层此前在树干判活之外 OR 上 interruptedRunId——把中断当成有人在干活，
  // 中断轮的「已看过结果页」因此永不置位、结果加载被闸门挡死（04 显示 0/0/0/0）。
  // 词表：中断没有活体 worker，不属于问题 A；「本轮未结束」由 B 谓词回答。
  it("A 判活不再 OR interruptedRunId：中断不算活体，「本轮未结束」由 B 谓词回答", () => {
    apiRequestMock.mockReset();
    const state = useDiscoveryState({ profileId: "vocab-results-live" }, () => {});
    const results = useDiscoveryResults(state, makeDeps());

    state.interruptedRunId.value = "screen-interrupted";
    expect(results.hasLiveTaskState()).toBe(false);

    state.interruptedRunId.value = "";
    state.pausedRunId.value = "screen-paused";
    expect(results.hasLiveTaskState()).toBe(false);
    expect(hasUnfinishedRound(state)).toBe(true);

    state.pausedRunId.value = "";
    state.setFlowLiveWorker(true);
    expect(results.hasLiveTaskState()).toBe(true);
    state.setFlowLiveWorker(false);
  });
});
