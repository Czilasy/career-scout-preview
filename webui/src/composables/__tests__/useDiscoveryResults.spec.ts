import { useDiscoveryState } from "../useDiscoveryState";
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
