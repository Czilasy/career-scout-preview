import { flushPromises } from "@vue/test-utils";
import { ref } from "vue";
import { apiRequest } from "../../api";
import { useScreenRoundFlow } from "../useScreenRoundFlow";
import type { Platform, RoundContext } from "../../types";

vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
  errorMessage: (error: unknown, fallback: string) => fallback,
}));

const apiRequestMock = apiRequest as unknown as ReturnType<typeof vi.fn>;

function makeDeps() {
  const refs = {
    draftPlatform: ref<Platform>("boss"),
    filterValues: ref<Record<Platform, Record<string, string[]>>>({ boss: {}, zhilian: {} }),
    keywords: ref<Array<{ word: string; recommended: boolean }>>([]),
    selectedKeywords: ref<string[]>([]),
    cityText: ref(""),
    profileSummary: ref(""),
    profileFacts: ref<Record<string, unknown>>({}),
    profileConfirmed: ref(false),
    scrapeTaskId: ref("scrape-1"),
    screenTaskId: ref("screen-1"),
    pausedRunId: ref(""),
    interruptedRunId: ref(""),
    screenBusy: ref(false),
    pausingScreen: ref(false),
    screenSnapshot: ref<any>(null),
    recrawlBusy: ref(false),
    recrawlTaskId: ref("recrawl-1"),
    recrawlSnapshot: ref<any>(null),
    finishedPartial: ref(false),
    restoredTaskHint: ref(""),
    activeStep: ref("screen"),
    resultLoaded: ref(false),
    resultPlatformFilter: ref<"all" | Platform>("all"),
    currentRoundStatus: ref(""),
    uncertainCount: ref(0),
  };
  const api = {
    startAiScreen: vi.fn(async () => {}),
    continueAiScreen: vi.fn(async () => {}),
    recrawlUncertain: vi.fn(async () => {}),
    continueRecrawl: vi.fn(async () => {}),
    finishPausedTask: vi.fn(async () => {}),
    resetWorkflow: vi.fn(async () => {}),
    loadLatestResult: vi.fn(async () => {}),
    notify: vi.fn(),
  };
  return { refs, api };
}

function roundContext(overrides: Partial<RoundContext> = {}): RoundContext {
  return {
    platform: "boss",
    keywords: ["Python"],
    cities: ["上海"],
    screening_fields: { salary: ["20-30K"] },
    profile_summary: "3年Python后端",
    profile_facts: { years: 3 },
    scrape_task_id: "scrape-1",
    screen_run_id: "screen-1",
    status: "paused",
    resumable: true,
    has_frozen_filters: true,
    ...overrides,
  };
}

describe("useScreenRoundFlow", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("restores the full round context and confirms the profile", () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    const ok = flow.restoreRoundContext(roundContext());
    expect(ok).toBe(true);
    expect(refs.keywords.value).toEqual([{ word: "Python", recommended: false }]);
    expect(refs.selectedKeywords.value).toEqual(["Python"]);
    expect(refs.cityText.value).toBe("上海");
    expect(refs.filterValues.value.boss.salary).toEqual(["20-30K"]);
    expect(refs.profileSummary.value).toBe("3年Python后端");
    expect(refs.profileConfirmed.value).toBe(true);
    expect(refs.scrapeTaskId.value).toBe("scrape-1");
  });

  it("restore writes to the context platform slot only", () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.restoreRoundContext(roundContext({ platform: "zhilian" }));
    expect(refs.filterValues.value.zhilian.salary).toEqual(["20-30K"]);
    expect(refs.filterValues.value.boss.salary).toBeUndefined();
  });

  it("2993: missing frozen filters on an existing round blocks restore", () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    const ok = flow.restoreRoundContext(
      roundContext({ screening_fields: {}, has_frozen_filters: true }),
    );
    expect(ok).toBe(false);
    expect(api.notify).toHaveBeenCalledWith(
      "本轮筛选条件未能恢复，无法继续 AI 筛选", "warning",
    );
  });

  it("pauseScreen calls the pause endpoint and polls until paused", async () => {
    vi.useFakeTimers();
    try {
      const { refs, api } = makeDeps();
      const flow = useScreenRoundFlow({ refs, api });
      apiRequestMock
        .mockResolvedValueOnce({ ok: true, run_id: "screen-1", status: "pausing" })
        .mockResolvedValueOnce({ status: "paused" });
      const promise = flow.pauseScreen();
      await vi.advanceTimersByTimeAsync(300);
      await flushPromises();
      await promise;
      expect(apiRequestMock).toHaveBeenCalledWith(
        "/api/task/pause/screen-1", { method: "POST" },
      );
      expect(refs.screenBusy.value).toBe(false);
      expect(refs.screenSnapshot.value?.status).toBe("paused");
      expect(api.loadLatestResult).toHaveBeenCalled();
      expect(flow.busyAction.value).toBe("");
    } finally {
      vi.useRealTimers();
    }
  });

  it("pauseScreen does not claim success while the worker is still running", async () => {
    vi.useFakeTimers();
    try {
      const { refs, api } = makeDeps();
      const flow = useScreenRoundFlow({ refs, api });
      apiRequestMock
        .mockResolvedValueOnce({ ok: true, run_id: "screen-1", status: "pausing" })
        .mockResolvedValue({ status: "running", progress: { message: "AI 筛选中" }, logs: [] });
      const promise = flow.pauseScreen();
      await vi.advanceTimersByTimeAsync(300 * 16);
      await flushPromises();
      await promise;
      expect(refs.screenBusy.value).toBe(true);
      expect(refs.pausingScreen.value).toBe(true);
      expect(refs.screenSnapshot.value?.status).toBe("pausing");
      expect(api.notify).not.toHaveBeenCalledWith("任务已暂停，结果已保留", "success");
    } finally {
      vi.useRealTimers();
    }
  });

  it("continueScreen restores context and calls the continue action", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.restoreRoundContext(roundContext());
    await flow.continueScreen();
    expect(api.continueAiScreen).toHaveBeenCalled();
    expect(refs.profileConfirmed.value).toBe(true);
  });

  it("continueScreen falls back to the restored run id when no context exists", async () => {
    const { refs, api } = makeDeps();
    refs.interruptedRunId.value = "interrupted-run";
    const flow = useScreenRoundFlow({ refs, api });
    await flow.continueScreen();
    expect(api.continueAiScreen).toHaveBeenCalledWith(undefined);
  });

  it("continueScreen continues directly when only one platform is resumable", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.registerRoundContext("boss", roundContext());
    await flow.continueScreen();
    expect(flow.continueGuide.value).toBeNull();
    expect(api.continueAiScreen).toHaveBeenCalledWith("boss");
  });

  it("continueScreen shows a platform guide when both platforms are resumable", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.registerRoundContext("boss", roundContext());
    flow.registerRoundContext("zhilian", roundContext({ platform: "zhilian", screen_run_id: "screen-z" }));
    await flow.continueScreen();
    expect(flow.continueGuide.value).toEqual({ boss: true, zhilian: true });
    expect(api.continueAiScreen).not.toHaveBeenCalled();
  });

  it("chooseContinuePlatform continues the selected platform", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.registerRoundContext("boss", roundContext());
    flow.registerRoundContext("zhilian", roundContext({ platform: "zhilian", screen_run_id: "screen-z" }));
    await flow.continueScreen();
    expect(flow.continueGuide.value).not.toBeNull();
    await flow.chooseContinuePlatform("zhilian");
    expect(flow.continueGuide.value).toBeNull();
    expect(api.continueAiScreen).toHaveBeenCalledWith("zhilian");
  });

  it("clearRoundContext clears stale contexts and guide", () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.registerRoundContext("boss", roundContext());
    flow.continueGuide.value = { boss: true, zhilian: false };
    flow.clearRoundContext();
    expect(flow.roundContexts.value).toEqual({});
    expect(flow.continueGuide.value).toBeNull();
  });

  it("mixed platforms: older resumable side keeps the continue action when newer side completed", () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.registerRoundContext("boss", roundContext());
    flow.registerRoundContext(
      "zhilian",
      roundContext({ platform: "zhilian", screen_run_id: "screen-z", status: "succeeded", resumable: false }),
    );
    refs.screenSnapshot.value = { status: "completed" };
    expect(flow.screenAction.value.kind).toBe("continue");
    expect(flow.continueTargetList.value).toEqual(["boss"]);
  });

  it("confirmNewRound asks when an older platform is still resumable", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.registerRoundContext("boss", roundContext());
    flow.registerRoundContext(
      "zhilian",
      roundContext({ platform: "zhilian", screen_run_id: "screen-z", status: "succeeded", resumable: false }),
    );
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(false);
    confirmMock.mockClear();
    await flow.confirmNewRound();
    expect(confirmMock).toHaveBeenCalled();
    expect(api.resetWorkflow).not.toHaveBeenCalled();
    confirmMock.mockRestore();
  });

  it("continueScreen falls back to a resumable platform outside the current filter", async () => {
    const { refs, api } = makeDeps();
    refs.resultPlatformFilter.value = "zhilian";
    const flow = useScreenRoundFlow({ refs, api });
    flow.registerRoundContext("boss", roundContext());
    flow.registerRoundContext(
      "zhilian",
      roundContext({ platform: "zhilian", screen_run_id: "screen-z", status: "succeeded", resumable: false }),
    );
    await flow.continueScreen();
    expect(api.continueAiScreen).toHaveBeenCalledWith("boss");
  });

  it("continueScreen notifies when the chosen platform cannot restore frozen conditions", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.registerRoundContext(
      "boss",
      roundContext({ screening_fields: {}, has_frozen_filters: true }),
    );
    await flow.continueScreen("boss");
    expect(api.continueAiScreen).not.toHaveBeenCalled();
    expect(api.notify).toHaveBeenCalledWith(
      "本轮筛选条件未能恢复，无法继续 AI 筛选", "warning",
    );
  });

  it("continueScreen refuses when frozen conditions were not restored", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.restoreRoundContext(roundContext({ screening_fields: {}, has_frozen_filters: true }));
    await flow.continueScreen();
    expect(api.continueAiScreen).not.toHaveBeenCalled();
  });

  it("closed completed context does not warn about missing frozen filters", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.registerRoundContext("boss", roundContext({
      screening_fields: {}, has_frozen_filters: true, status: "interrupted", resumable: false,
    }));
    await flow.continueScreen("boss");
    expect(api.notify).not.toHaveBeenCalledWith(
      "本轮筛选条件未能恢复，无法继续 AI 筛选", "warning",
    );
  });

  it("startScreen navigates to 03 and starts AI screening", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    await flow.startScreen();
    expect(refs.activeStep.value).toBe("screen");
    expect(api.startAiScreen).toHaveBeenCalled();
  });

  it("startRecrawl with a platform navigates to 03 after starting", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    await flow.startRecrawl("boss");
    expect(api.recrawlUncertain).toHaveBeenCalledWith("boss");
    expect(refs.activeStep.value).toBe("screen");
  });

  it("continueRecrawl keeps busyAction while the request is pending", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    let observed = "";
    api.continueRecrawl.mockImplementation(async () => {
      observed = flow.busyAction.value;
    });
    await flow.continueRecrawl();
    expect(observed).toBe("continue-recrawl");
    expect(flow.busyAction.value).toBe("");
  });

  it("confirmNewRound resets immediately when nothing is resumable", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    await flow.confirmNewRound();
    expect(api.resetWorkflow).toHaveBeenCalled();
  });

  it("confirmNewRound asks before resetting a resumable round", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.restoreRoundContext(roundContext());
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(false);
    await flow.confirmNewRound();
    expect(confirmMock).toHaveBeenCalled();
    expect(api.resetWorkflow).not.toHaveBeenCalled();
    confirmMock.mockReturnValue(true);
    await flow.confirmNewRound();
    expect(api.resetWorkflow).toHaveBeenCalled();
  });

  it("derives pause/continue actions from snapshot status", () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    refs.screenSnapshot.value = { status: "running" };
    expect(flow.screenAction.value.kind).toBe("pause");
    refs.screenSnapshot.value = { status: "paused" };
    expect(flow.screenAction.value.kind).toBe("continue");
  });

  it("scraped_only status derives the start action even with a completed snapshot", () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    refs.screenSnapshot.value = { status: "completed" };
    refs.currentRoundStatus.value = "scraped_only";
    expect(flow.screenStatus.value).toBe("scraped_only");
    expect(flow.screenAction.value.kind).toBe("start");
  });

  it("closed saved round maps screenStatus to partial and keeps recrawl on results", () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    refs.uncertainCount.value = 1;
    flow.restoreRoundContext(roundContext({ status: "interrupted", resumable: false }));
    expect(flow.screenStatus.value).toBe("partial");
    expect(flow.screenAction.value.kind).toBe("recrawl");
  });

  it("confirmNewRound resets immediately after a closed saved round", async () => {
    const { refs, api } = makeDeps();
    const flow = useScreenRoundFlow({ refs, api });
    flow.restoreRoundContext(roundContext({ status: "interrupted", resumable: false }));
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(false);
    confirmMock.mockClear();
    await flow.confirmNewRound();
    expect(confirmMock).not.toHaveBeenCalled();
    expect(api.resetWorkflow).toHaveBeenCalled();
    confirmMock.mockRestore();
  });
});
