// 026 B078：restoreRunningTask 以"是否进过 04 页"为唯一闸门。
// - 已进 04 页（resultsPageSeen=true）＝上次流程已结束 → 即使后端残留
//   interrupted run 也不恢复 02/03 页、不弹"服务重启被中断"提示（FR-002/FR-003）。
// - AI failed/interrupted 只展示终态事实，不把错误快照当作占用中的恢复任务。
import { ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { ApiError, apiRequest } from "../../api";
import type { FrozenSearchScope } from "../../types";
import { useDiscoveryExecution } from "../useDiscoveryExecution";
import { useDiscoveryState } from "../useDiscoveryState";
import type { DiscoveryState } from "../useDiscoveryState";
import type { ExecutionNeeds } from "../discoveryDeps";

vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
  ApiError: class MockApiError extends Error {
    status: number;
    payload: Record<string, unknown>;

    constructor(status: number, payload: Record<string, unknown>) {
      super(String(payload.message || payload.error || "请求失败"));
      this.status = status;
      this.payload = payload;
    }
  },
  errorMessage: (error: unknown, fallback: string) => fallback,
}));
vi.mock("../../composables/useTheme", () => ({
  setThemePlatform: vi.fn(),
}));

const apiRequestMock = apiRequest as unknown as ReturnType<typeof vi.fn>;

// 031 B8 补遗：state fake = 真实状态工厂 + overrides（字段永齐全、类型真实，
// 消除 as any 兜底）；deps fake 按 ExecutionNeeds 全量类型化。
function makeState(overrides: Partial<DiscoveryState> = {}): DiscoveryState {
  const state = useDiscoveryState({ profileId: "test" }, () => {});
  return Object.assign(state, overrides);
}

function makeDeps(overrides: Partial<ExecutionNeeds> = {}): ExecutionNeeds {
  return Object.assign({
    clearWorkflowState: vi.fn(),
    emit: vi.fn(),
    enrichPausedSnapshot: vi.fn(async () => {}),
    enterScreenStep: vi.fn(),
    enterSearchStep: vi.fn(),
    isCompletedTaskStatus: vi.fn(() => false),
    isLoginErrorCode: vi.fn(() => false),
    loadCityCatalog: vi.fn(async () => {}),
    loadFilterLabels: vi.fn(async () => {}),
    loadLatestResult: vi.fn(async () => {}),
    notify: vi.fn(),
    persistFinishedState: vi.fn(),
    pollRecrawl: vi.fn(async () => {}),
    pollTask: vi.fn(async () => {}),
    props: { profileId: "test" },
    refreshScopePreview: vi.fn(async () => null),
    requireProfileConfirmed: vi.fn(() => true),
    restoreLocationsFromContext: vi.fn(),
    returnToLatest: vi.fn(async () => {}),
    roundFlow: {
      busyAction: "",
      roundContext: null,
      roundContexts: {},
      suppressProfileWatch: false,
      startRecrawl: vi.fn(async () => {}),
      clearRoundContext: vi.fn(),
      restoreRoundContext: vi.fn(() => false),
      registerRoundContext: vi.fn(),
    },
    saveScrapedOnlySnapshot: vi.fn(async () => "saved" as const),
    setDraftPlatform: vi.fn(),
    setPipelineResult: vi.fn(),
    showLoginGuide: vi.fn(async () => {}),
    validateProfileForScreen: vi.fn(() => true),
  }, overrides);
}

const interruptedScreenResponse = {
  has_task: true,
  task_id: "screen-t1",
  kind: "ai_screen",
  status: "interrupted",
  platform: "boss",
  scrape_task_id: "scrape-s1",
  scrape_completed: true,
  frozen_filters: {},
  profile_summary: "3 年 Python 后端",
  round_context: null,
};

describe("useDiscoveryExecution.restoreRunningTask（026 B078）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("恢复任务时按当前画像查询，不读其它画像的任务", async () => {
    apiRequestMock.mockResolvedValue({ has_task: false });
    const state = makeState();
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(apiRequestMock).toHaveBeenCalledWith("/api/latest-running-task?profile_id=test");
  });

  it("恢复简历分析任务时把 task_id 交给分析流程接回结果", async () => {
    apiRequestMock.mockResolvedValue({
      has_task: true,
      task_id: "resume-analysis-1",
      kind: "resume_analysis",
      status: "running",
      platform: "boss",
    });
    const state = makeState();
    const restore = vi.fn();
    state.resumeAnalysisRestore.value = restore;
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(restore).toHaveBeenCalledWith("analyzing", "", "resume-analysis-1");
    expect(state.activeStep.value).toBe("upload");
    expect(state.activeTaskRestored.value).toBe(true);
  });

  it("切换画像后，旧画像晚到的恢复响应不能写回新画像", async () => {
    let resolveResponse: (value: Record<string, unknown>) => void = () => {};
    apiRequestMock.mockReturnValue(new Promise((resolve) => {
      resolveResponse = resolve;
    }));
    const state = makeState();
    state.activeStep.value = "search";
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    const restoring = execution.restoreRunningTask();
    state.resetForProfileSwitch();
    expect(state.workflowEpoch.value).toBe(1);
    resolveResponse({
      has_task: true,
      task_id: "old-profile-task",
      kind: "scrape",
      status: "running",
      platform: "boss",
    });
    await restoring;

    expect(state.activeStep.value).toBe("upload");
    expect(state.scrapeTaskId.value).toBe("");
    expect(state.scrapeBusy.value).toBe(false);
    expect(deps.pollTask).not.toHaveBeenCalled();
  });

  it("T001: 已进 04 页（已结束）+ 后端残留 interrupted → 不恢复、不弹提示", async () => {
    apiRequestMock.mockResolvedValue(interruptedScreenResponse);
    const state = makeState({ resultsPageSeen: ref(true) });
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(state.interruptedRunId.value).toBe("");
    expect(state.screenTaskId.value).toBe("");
    expect(state.activeStep.value).toBe("upload");
    expect(deps.enterScreenStep).not.toHaveBeenCalled();
    expect(state.restoredTaskHint.value).toBe("");
  });

  it.each(["failed", "interrupted"] as const)(
    "未进 04 页 + AI 筛选 %s → 只保留终态快照，不占用任务槽",
    async (status) => {
      apiRequestMock.mockResolvedValue({
        ...interruptedScreenResponse,
        task_id: `screen-${status}`,
        status,
        error: status === "failed" ? "AI 服务失败" : "服务重启导致 AI 筛选中断",
      });
      const state = makeState({
        activeStep: ref("screen"),
        resultsPageSeen: ref(false),
        screenBusy: ref(true),
        screenTaskId: ref("stale-screen"),
        interruptedRunId: ref("stale-interrupted"),
      });
      const deps = makeDeps();
      const execution = useDiscoveryExecution(state, deps);

      await execution.restoreRunningTask();

      expect(state.screenSnapshot.value?.status).toBe(status);
      expect(state.screenSnapshot.value?.error).toBeTruthy();
      expect(state.screenBusy.value).toBe(false);
      expect(state.screenTaskId.value).toBe(`screen-${status}`);
      expect(state.interruptedRunId.value).toBe("");
      expect(state.pausedRunId.value).toBe("");
      // 该标记表示错误恢复页已成功接回，不能让挂载后的自动新一轮检查
      // 把错误事实清回空白页；真正的任务占用由 busy/paused 标记决定。
      expect(state.activeTaskRestored.value).toBe(true);
      expect(state.pipelineBusy.value).toBe(false);
      expect(state.scopeLocked.value).toBe(false);
      expect(deps.pollTask).not.toHaveBeenCalled();
      expect(deps.roundFlow.restoreRoundContext).not.toHaveBeenCalled();
      expect(deps.enterScreenStep).not.toHaveBeenCalled();
    },
  );

  it("paused 分支同样受「已结束」闸门约束：已进 04 页则不恢复暂停任务", async () => {
    apiRequestMock.mockResolvedValue({
      has_task: true,
      task_id: "screen-p1",
      kind: "ai_screen",
      status: "paused",
      platform: "boss",
      scrape_task_id: "scrape-s1",
      scrape_completed: true,
      frozen_filters: {},
      profile_summary: "3 年 Python 后端",
      round_context: null,
    });
    const state = makeState({ resultsPageSeen: ref(true) });
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(state.pausedRunId.value).toBe("");
    expect(deps.enterScreenStep).not.toHaveBeenCalled();
    expect(state.restoredTaskHint.value).toBe("");
  });

  it("已进 04 页 + 残留 completed scrape(一键 auto_screen) → 不接续 AI 筛选、不设活动任务", async () => {
    apiRequestMock.mockResolvedValue({
      has_task: true,
      task_id: "scrape-c1",
      kind: "scrape",
      status: "completed",
      platform: "boss",
      auto_screen: true,
      scrape_task_id: "scrape-c1",
      frozen_filters: {},
      profile_summary: "3 年 Python 后端",
    });
    const state = makeState({ resultsPageSeen: ref(true) });
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(state.activeTaskRestored.value).toBe(false);
    expect(state.scrapeTaskId.value).toBe("");
    expect(deps.enterScreenStep).not.toHaveBeenCalled();
    expect(state.activeStep.value).toBe("upload");
    expect(state.restoredTaskHint.value).toBe("");
  });

  it("已进 04 页 + 残留 completed scrape(非一键) → 不恢复 02 页、不加载旧结果", async () => {
    apiRequestMock.mockResolvedValue({
      has_task: true,
      task_id: "scrape-c2",
      kind: "scrape",
      status: "completed",
      platform: "boss",
      auto_screen: false,
      scrape_task_id: "scrape-c2",
      frozen_filters: {},
      profile_summary: "3 年 Python 后端",
    });
    const state = makeState({ resultsPageSeen: ref(true) });
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(state.activeTaskRestored.value).toBe(false);
    expect(state.activeStep.value).toBe("upload");
    expect(deps.loadLatestResult).not.toHaveBeenCalled();
  });

  it("恢复重抓 interrupted 只保留错误快照，不占用公共任务槽", async () => {
    apiRequestMock.mockResolvedValue({
      has_task: true,
      task_id: "recrawl-interrupted",
      kind: "recrawl",
      status: "interrupted",
      platform: "boss",
      progress: { current: 2 },
      logs: [],
      error: "服务重启导致重抓中断",
    });
    const state = makeState({
      pausedRunId: ref("stale-paused"),
      interruptedRunId: ref("stale-interrupted"),
      recrawlBusy: ref(true),
    });
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(state.recrawlSnapshot.value?.status).toBe("interrupted");
    expect(state.recrawlBusy.value).toBe(false);
    expect(state.pausedRunId.value).toBe("");
    expect(state.interruptedRunId.value).toBe("");
    expect(state.pipelineBusy.value).toBe(false);
  });
});

// 035 US1（真机问题①，FR-010）：新一轮开始 / 恢复到活的抓取任务时，
// screen 侧旧一轮展示状态（screenSnapshot 等 5 项）必须同步清空——03 页不残留旧轮内容。
describe("useDiscoveryExecution screen 侧清空（035 FR-010）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  function seedStaleScreenSide(state: DiscoveryState) {
    state.screenTaskId.value = "screen-old";
    state.screenSnapshot.value = {
      status: "completed",
      progress: { message: "旧一轮 AI 筛选完成" },
      logs: [],
      total: 42,
      kept_count: 20,
      dropped_count: 22,
    };
    state.recrawlTaskId.value = "recrawl-old";
    state.recrawlSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.currentRoundStatus.value = "screened";
  }

  it("T005-①: startScrape 开新一轮时清空 screen 侧展示状态", async () => {
    const state = makeState();
    seedStaleScreenSide(state);
    state.selectedKeywords.value = ["Python"];
    apiRequestMock.mockResolvedValue({ task_id: "scrape-new-1" });
    const deps = makeDeps({
      refreshScopePreview: vi.fn(async () => ({ scope_digest: "digest-1" } as unknown as FrozenSearchScope)),
    });
    const execution = useDiscoveryExecution(state, deps);

    await execution.startScrape();

    expect(state.scrapeBusy.value).toBe(true);
    expect(state.screenTaskId.value).toBe("");
    expect(state.screenSnapshot.value).toBeNull();
    expect(state.recrawlTaskId.value).toBe("");
    expect(state.recrawlSnapshot.value).toBeNull();
    expect(state.currentRoundStatus.value).toBe("");
  });

  it("账号池读取失败时仍交给服务端 FR-019 门禁裁决", async () => {
    const state = makeState();
    state.selectedKeywords.value = ["Python"];
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url === "/api/browser-accounts") throw new Error("network");
      if (url === "/api/execute-search") return { task_id: "scrape-new-2" };
      throw new Error(`unexpected request: ${url}`);
    });
    const deps = makeDeps({
      refreshScopePreview: vi.fn(async () => (
        { scope_digest: "digest-2" } as unknown as FrozenSearchScope
      )),
    });

    await useDiscoveryExecution(state, deps).startScrape();

    expect(apiRequestMock).toHaveBeenCalledWith(
      "/api/execute-search", expect.objectContaining({ method: "POST" }),
    );
  });

  it("T005-②: restoreRunningTask 检测到活的抓取任务时清空 screen 侧残留（含 sessionStorage 整包恢复带入）", async () => {
    apiRequestMock.mockResolvedValue({
      ok: true, has_task: true, task_id: "scrape-live-1", kind: "scrape",
      status: "running", platform: "boss", progress: { message: "正在抓取" }, logs: [],
    });
    const state = makeState();
    seedStaleScreenSide(state);
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(state.scrapeBusy.value).toBe(true);
    expect(state.activeStep.value).toBe("search");
    expect(state.screenTaskId.value).toBe("");
    expect(state.screenSnapshot.value).toBeNull();
    expect(state.recrawlTaskId.value).toBe("");
    expect(state.recrawlSnapshot.value).toBeNull();
    expect(state.currentRoundStatus.value).toBe("");
  });

  it("恢复到活的抓取暂停任务（paused）时同样清空 screen 侧残留", async () => {
    apiRequestMock.mockResolvedValue({
      ok: true, has_task: true, task_id: "scrape-pause-1", kind: "scrape",
      status: "paused", platform: "boss", progress: { message: "已暂停" }, logs: [],
      pause_info: { error_code: "x", error_reason: "手动暂停" },
    });
    const state = makeState();
    seedStaleScreenSide(state);
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(state.pausedRunId.value).toBe("scrape-pause-1");
    expect(state.screenSnapshot.value).toBeNull();
    expect(state.currentRoundStatus.value).toBe("");
  });

  it("恢复到活的筛选任务时不清空 screen 侧（那是任务本体，不能误删）", async () => {
    apiRequestMock.mockResolvedValue({
      ok: true, has_task: true, task_id: "screen-live-1", kind: "ai_screen",
      status: "running", platform: "boss", scrape_task_id: "scrape-live-0",
      scrape_completed: true, progress: { message: "AI 筛选中" }, logs: [],
    });
    const state = makeState();
    state.screenTaskId.value = "screen-live-1";
    state.screenSnapshot.value = {
      status: "running", progress: { message: "AI 筛选中" }, logs: [],
    };
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(state.screenSnapshot.value?.status).toBe("running");
    expect(state.screenTaskId.value).toBe("screen-live-1");
  });
});

describe("单独抓取入口的画像边界", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("空画像时单独抓取仍可创建抓取任务", async () => {
    const state = makeState();
    state.selectedKeywords.value = ["Python"];
    state.cityText.value = "上海";
    const validateProfile = vi.fn(() => false);
    const requireProfileConfirmed = vi.fn(() => false);
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url === "/api/browser-accounts") {
        return { accounts: [{ pool: { selected: true } }] };
      }
      if (url === "/api/execute-search") return { task_id: "scrape-empty-profile" };
      throw new Error(`unexpected request: ${url}`);
    });
    const deps = makeDeps({
      refreshScopePreview: vi.fn(async () => ({
        scope_digest: "digest-empty-profile",
      } as unknown as FrozenSearchScope)),
      requireProfileConfirmed,
      validateProfileForScreen: validateProfile,
    });
    const execution = useDiscoveryExecution(state, deps);

    execution.handleStartScrapeClick();
    await flushPromises();

    expect(validateProfile).not.toHaveBeenCalled();
    expect(requireProfileConfirmed).not.toHaveBeenCalled();
    expect(apiRequestMock).toHaveBeenCalledWith(
      "/api/execute-search", expect.objectContaining({ method: "POST" }),
    );
  });
});

describe("抓取任务恢复边界", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("继续抓取只提交统一任务 ID，不携带请求 body", async () => {
    const state = makeState({
      scrapeTaskId: ref("paused-scrape"),
      pausedRunId: ref("paused-scrape"),
      scrapeSnapshot: ref({ status: "paused", progress: {}, logs: [] }),
    });
    apiRequestMock.mockResolvedValue({ task_id: "resumed-scrape" });
    const execution = useDiscoveryExecution(state, makeDeps());

    await execution.continueScrape();

    expect(apiRequestMock).toHaveBeenCalledWith(
      "/api/task/continue/paused-scrape",
      { method: "POST" },
    );
  });

  it("暂停请求失败且状态未知时仍保持抓取任务占用", async () => {
    const state = makeState({
      scrapeTaskId: ref("scrape-pausing"),
      scrapeSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    });
    const deps = makeDeps({
      pollTask: vi.fn(async () => {}),
    });
    apiRequestMock.mockRejectedValueOnce(new Error("暂停请求失败"));
    const execution = useDiscoveryExecution(state, deps);

    await execution.pauseScrape();

    expect(state.scrapeBusy.value).toBe(true);
    expect(state.scrapeSnapshot.value?.status).toBe("pausing");
    expect(state.pipelineBusy.value).toBe(true);
    expect(state.scrapeActionBusy.value).toBe("");
  });

  it("暂停请求受理后任务仍在运行保持暂停占用（按钮变灰，不可重复点）", async () => {
    const state = makeState({
      scrapeTaskId: ref("scrape-hold"),
      scrapeSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    });
    const deps = makeDeps({
      // 任务仍在等当前组合结束：轮询不会把快照推进到 paused
      pollTask: vi.fn(async () => {}),
    });
    apiRequestMock.mockResolvedValueOnce({ ok: true });
    const execution = useDiscoveryExecution(state, deps);

    await execution.pauseScrape();

    expect(state.scrapeActionBusy.value).toBe("pause-scrape");
    expect(state.scrapeSnapshot.value?.status).toBe("pausing");
  });

  it("续跑收到明确失败响应时清除本地恢复标记并解除新任务锁", async () => {
    const state = makeState({
      scrapeTaskId: ref("paused-scrape"),
      pausedRunId: ref("paused-scrape"),
      interruptedRunId: ref("stale-interrupted"),
      scrapeSnapshot: ref({ status: "paused", progress: { current: 2 }, logs: [] }),
    });
    apiRequestMock
      .mockRejectedValueOnce(new ApiError(409, {
        error: "checkpoint_read_failed",
        error_code: "checkpoint_read_failed",
        error_reason: "暂停断点读取失败，任务已结束，请重试",
        status: "failed",
      }))
      .mockResolvedValueOnce({
        status: "failed",
        progress: { current: 2 },
        logs: ["断点读取失败"],
        error: "暂停断点读取失败，任务已结束，请重试",
      });
    const execution = useDiscoveryExecution(state, makeDeps());

    await execution.continueScrape();

    expect(state.scrapeBusy.value).toBe(false);
    expect(state.scrapeSnapshot.value?.status).toBe("failed");
    expect(state.pausedRunId.value).toBe("");
    expect(state.interruptedRunId.value).toBe("");
    expect(state.pipelineBusy.value).toBe(false);
  });

  it.each(["failed", "interrupted"] as const)(
    "恢复抓取 %s 只保留错误快照并清除旧恢复标记",
    async (status) => {
      const state = makeState({
        pausedRunId: ref("stale-paused"),
        interruptedRunId: ref("stale-interrupted"),
      });
      apiRequestMock.mockResolvedValue({
        has_task: true,
        task_id: `scrape-${status}`,
        kind: "scrape",
        status,
        platform: "boss",
        progress: { message: "列表抓取失败" },
        logs: ["抓取日志"],
        error: "列表抓取失败",
      });
      const execution = useDiscoveryExecution(state, makeDeps());

      await execution.restoreRunningTask();

      expect(state.scrapeSnapshot.value?.status).toBe(status);
      expect(state.pausedRunId.value).toBe("");
      expect(state.interruptedRunId.value).toBe("");
      expect(state.pipelineBusy.value).toBe(false);
    },
  );

  it("恢复暂停抓取时保留抓取任务 ID，继续和取消都指向同一轮", async () => {
    apiRequestMock.mockResolvedValue({
      has_task: true,
      task_id: "paused-scrape-current",
      kind: "scrape",
      status: "paused",
      platform: "zhilian",
      progress: { overall_percent: 42 },
      logs: [],
      error: "需要登录",
      scraped_count: 8,
      source_total: 8,
      pause_info: { error_code: "source_login_required", error_reason: "需要登录" },
    });
    const state = makeState();
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(state.scrapeTaskId.value).toBe("paused-scrape-current");
    expect(state.pausedRunId.value).toBe("paused-scrape-current");
    expect(state.scrapeSnapshot.value?.status).toBe("paused");
  });
});

describe("浏览器清理失败的公共动作反馈", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("抓取取消收到 HTTP 200 cleanup failure 时不显示纯成功", async () => {
    apiRequestMock.mockResolvedValue({
      ok: false,
      error: "browser_cleanup_failed",
      cleanup_error: "browser_cleanup_failed",
      cleanup: { ok: false, error_code: "source_cdp_unavailable" },
    });
    const state = makeState({
      scrapeTaskId: ref("boss-scrape-cleanup-failed"),
      scrapeBusy: ref(true),
    });
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.cancelScrape();

    expect(state.scrapeSnapshot.value?.error).toContain("清理失败");
    expect(deps.notify).toHaveBeenCalledWith(expect.stringContaining("清理失败"), "error");
    expect(deps.notify).not.toHaveBeenCalledWith("已停止抓取", "warning");
  });

  it.each(["boss", "zhilian"] as const)(
    "%s 结束保存保留结果事实并显式提示浏览器清理失败",
    async (platform) => {
      apiRequestMock.mockResolvedValue({
        ok: true,
        cleanup_error: "browser_cleanup_failed",
        cleanup: { ok: false, error_code: "source_cdp_unavailable" },
        platform,
        status: "completed_with_pending",
        result: { total_scraped: 1, jobs: [] },
      });
      const state = makeState({
        screenTaskId: ref(`${platform}-screen-cleanup-failed`),
        screenSnapshot: ref({ status: "paused", platform }),
      });
      const deps = makeDeps();
      const execution = useDiscoveryExecution(state, deps);

      await execution.finishPausedTask(state.screenTaskId.value);

      expect(state.finishedPartial.value).toBe(true);
      expect(state.screenSnapshot.value?.status).toBe("completed_with_pending");
      expect(state.screenSnapshot.value?.error).toContain("结果已保存，但浏览器清理失败");
      expect(deps.notify).toHaveBeenCalledWith(
        "结果已保存，但浏览器清理失败",
        "error",
      );
      expect(deps.notify).not.toHaveBeenCalledWith(
        "任务已结束，已完成结果已保存",
        "success",
      );
    },
  );

  it.each(["boss", "zhilian"] as const)(
    "%s 暂停任务取消使用同一清理失败反馈",
    async (platform) => {
      apiRequestMock.mockResolvedValue({
        ok: false,
        error: "browser_cleanup_failed",
        cleanup: { ok: false, error_code: "source_cdp_unavailable" },
        platform,
      });
      const state = makeState({
        screenTaskId: ref(`${platform}-paused-cleanup-failed`),
        screenSnapshot: ref({ status: "paused", platform }),
      });
      const deps = makeDeps();
      const execution = useDiscoveryExecution(state, deps);

      await execution.cancelPausedTask(state.screenTaskId.value);

      expect(state.screenSnapshot.value?.status).toBe("cancelled");
      expect(state.screenSnapshot.value?.error).toContain("清理失败");
      expect(deps.notify).toHaveBeenCalledWith(expect.stringContaining("清理失败"), "error");
      expect(deps.notify).not.toHaveBeenCalledWith("已取消任务，已有结果保留", "warning");
    },
  );
});

describe("039 结束保存后的面板计数", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("结束并保存结果后面板保留该轮真实计数与失败留痕，不回退成 0 完成", async () => {
    apiRequestMock.mockResolvedValue({
      ok: true,
      platform: "boss",
      status: "completed_with_pending",
      result: { total_scraped: 3, jobs: [] },
    });
    const state = makeState({
      screenTaskId: ref("screen-039-finish"),
      screenSnapshot: ref({
        status: "paused", total: 16, success_count: 14, fail_count: 2,
        unstarted_count: 0, pending_count: 0,
        combo_issues: [{
          combo_key: "A|上海", code: "source_timeout",
          code_text: "抓取超时", reason: "第 9 页无响应", ts: "t1",
        }],
      }),
      scrapeSnapshot: ref({
        status: "done", total: 2, success_count: 2,
        fail_count: 0, unstarted_count: 0,
      }),
    });
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.finishPausedTask("screen-039-finish");

    expect(state.screenSnapshot.value?.status).toBe("completed_with_pending");
    expect(state.screenSnapshot.value?.total).toBe(16);
    expect(state.screenSnapshot.value?.success_count).toBe(14);
    expect(state.screenSnapshot.value?.fail_count).toBe(2);
    expect(state.screenSnapshot.value?.unstarted_count).toBe(0);
    expect(state.screenSnapshot.value?.combo_issues?.[0]?.combo_key).toBe("A|上海");
    expect(state.scrapeSnapshot.value?.success_count).toBe(2);
  });
});

describe("抓取终止后的结果收口", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("终止抓取后保存当前任务快照并立即进入结果页", async () => {
    apiRequestMock.mockResolvedValue({ ok: true });
    const state = makeState({
      activeStep: ref("search"),
      scrapeTaskId: ref("scrape-current-round"),
      scrapeBusy: ref(true),
      scrapeSnapshot: ref({
        status: "running",
        scraped_count: 12,
        source_total: 12,
        progress: { overall_percent: 48 },
        logs: ["抓取中"],
      }),
    });
    const saveScrapedOnlySnapshot = vi.fn(async () => "saved" as const);
    const deps = makeDeps({ saveScrapedOnlySnapshot });
    const execution = useDiscoveryExecution(state, deps);

    await execution.cancelScrape();

    expect(saveScrapedOnlySnapshot).toHaveBeenCalledWith(true);
    expect(state.scrapeSnapshot.value?.status).toBe("cancelled");
    expect(state.activeStep.value).toBe("results");
  });
});
