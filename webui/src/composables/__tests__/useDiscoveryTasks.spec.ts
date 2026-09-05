// 026 B078：maybeAutoStartNewRound 以"是否进过 04 页/已结束保存"为完成判据，
// 不再依赖后端历史轮状态推断（FR-001/FR-005）。
// - 已结束事实（resultsPageSeen/finishedPartial）→ 直接开始新一轮（01 页）。
// - 未结束（本地未完成快照 / 有活动任务 / 历史轮未完成）→ 不触发、恢复现场。
import { computed, ref } from "vue";
import { apiRequest } from "../../api";
import { useDiscoveryTasks } from "../useDiscoveryTasks";
import { useDiscoverySearch } from "../useDiscoverySearch";
import { useDiscoveryState } from "../useDiscoveryState";
import { taskProgressFromSnapshot } from "../useDiscoveryState";
import type { DiscoveryState } from "../useDiscoveryState";
import type { RoundFlowLike, TasksNeeds } from "../discoveryDeps";

vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
  errorMessage: (error: unknown, fallback: string) => fallback,
  settingsApi: { get: vi.fn(), save: vi.fn() },
  userFacingMessage: (error: unknown, fallback: string) => fallback,
}));

const apiRequestMock = apiRequest as unknown as ReturnType<typeof vi.fn>;

const roundFlowFake: RoundFlowLike = {
  busyAction: "",
  roundContext: null,
  roundContexts: {},
  suppressProfileWatch: false,
  startRecrawl: vi.fn(async () => {}),
  clearRoundContext: vi.fn(),
  restoreRoundContext: vi.fn(() => false),
  registerRoundContext: vi.fn(),
};

// 031 B8 补遗：state fake = 真实状态工厂 + overrides（字段永齐全、类型真实，
// 消除 as any 兜底）；deps fake 按 TasksNeeds 全量类型化。
function makeState(overrides: Partial<DiscoveryState> = {}): DiscoveryState {
  const state = useDiscoveryState({ profileId: "test" }, () => {});
  return Object.assign(state, overrides);
}

function makeDeps(overrides: Partial<TasksNeeds> = {}): TasksNeeds {
  return Object.assign({
    cancelScrape: vi.fn(async () => {}),
    clearFinishedState: vi.fn(),
    clearLatestResult: vi.fn(async () => true),
    clearWorkflowState: vi.fn(),
    continueAiScreen: vi.fn(async () => {}),
    enterScreenStep: vi.fn(),
    fetchMergedLatestResult: vi.fn(async () => null),
    finishPausedTask: vi.fn(async () => {}),
    isLoginErrorCode: vi.fn(() => false),
    jobId: vi.fn(() => ""),
    loadLatestResult: vi.fn(async () => {}),
    notify: vi.fn(),
    restoreRunningTask: vi.fn(async () => {}),
    roundFlow: roundFlowFake,
    setPipelineResult: vi.fn(),
    showLoginGuide: vi.fn(async () => {}),
    startAiScreen: vi.fn(async () => {}),
  }, overrides);
}

describe("useDiscoveryTasks.maybeAutoStartNewRound（026 B078）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
    apiRequestMock.mockResolvedValue({ has_task: false });
  });

  it("T004a: 已进 04 页（已结束）→ 直接开始新一轮（resetWorkflow），不查历史轮", async () => {
    const state = makeState({ resultsPageSeen: ref(true) });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.fetchMergedLatestResult).not.toHaveBeenCalled();
    expect(state.activeStep.value).toBe("upload");
    expect(state.resultsPageSeen.value).toBe(false); // resetWorkflow 清除已结束标记
  });

  it("T004b: 结束保存（finishedPartial=true）同样触发新一轮", async () => {
    const state = makeState({ finishedPartial: ref(true) });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.fetchMergedLatestResult).not.toHaveBeenCalled();
    expect(state.activeStep.value).toBe("upload");
  });

  it("T004c: 本地有未完成快照 → 不触发（恢复现场，B068 不变）", async () => {
    const state = makeState({ unfinishedWorkflowRestored: ref(true) });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.fetchMergedLatestResult).not.toHaveBeenCalled();
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
  });

  it("T004d: 无已结束事实但最新历史轮已完成 → 自动新一轮", async () => {
    const state = makeState();
    const deps = makeDeps({
      fetchMergedLatestResult: vi.fn(async () => ({
        merged: { ok: true, jobs: [] },
        newer: { platform: "boss" as const, data: { status: "succeeded" } },
        platformStatuses: { boss: "succeeded", zhilian: "succeeded" },
      })),
    });
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(state.activeStep.value).toBe("upload");
  });

  it("T004e: 最新历史轮未完成 → 恢复现场，不重置", async () => {
    const state = makeState();
    const deps = makeDeps({
      fetchMergedLatestResult: vi.fn(async () => ({
        merged: { ok: true, jobs: [] },
        newer: { platform: "boss" as const, data: { status: "paused" } },
        platformStatuses: { boss: "paused", zhilian: "succeeded" },
      })),
    });
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.loadLatestResult).toHaveBeenCalled();
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
  });
});

// 035：未结束任务保护（B086）与后台跑完历史冒泡（B087）
describe("useDiscoveryTasks.maybeAutoStartNewRound（035 未结束任务保护）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
    apiRequestMock.mockResolvedValue({ has_task: false });
  });

  it("T003a: 未结束任务存在（运行中快照）→ 恢复现场，不 reset、不取消", async () => {
    const state = makeState({
      screenSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    // 直接返回、不触发任何新一轮逻辑（不查历史轮、不 reset、不取消）
    expect(deps.fetchMergedLatestResult).not.toHaveBeenCalled();
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
    expect(deps.loadLatestResult).not.toHaveBeenCalled();
  });

  it("T003b: 未结束任务存在（pausedRunId）→ 恢复现场，不 reset、不取消", async () => {
    const state = makeState({ pausedRunId: ref("run-1") });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.fetchMergedLatestResult).not.toHaveBeenCalled();
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
    expect(deps.loadLatestResult).not.toHaveBeenCalled();
  });

  it("T003c: 未结束任务存在（interruptedRunId）→ 恢复现场，不 reset、不取消", async () => {
    const state = makeState({ interruptedRunId: ref("run-2") });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.fetchMergedLatestResult).not.toHaveBeenCalled();
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
    expect(deps.loadLatestResult).not.toHaveBeenCalled();
  });
});

describe("useDiscoveryTasks.pollTask（035 后台跑完历史冒泡）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("T014a: 历史模式下任务跑完 → 顶部冒泡提示 + 刷新历史列表，不切走历史视图", async () => {
    const state = makeState({
      historyMode: computed(() => true),
      screenTaskId: ref("run-1"),
    });
    const deps = makeDeps({ fetchMergedLatestResult: vi.fn(async () => null) });
    const tasks = useDiscoveryTasks(state, deps);
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/task-state/")) {
        return { status: "completed", progress: {}, logs: [], result: null };
      }
      if (url === "/api/result-history") return { ok: true, items: [] };
      return { ok: true };
    });

    await tasks.pollTask("run-1", "screen");

    expect(state.taskCompletedToast.value.visible).toBe(true);
    expect(state.activeStep.value).not.toBe("results");
    expect(apiRequestMock).toHaveBeenCalledWith("/api/result-history");
  });

  it("T014b: 非历史模式任务跑完 → 不冒泡、切到结果页", async () => {
    const state = makeState({ screenTaskId: ref("run-1") });
    const deps = makeDeps({ fetchMergedLatestResult: vi.fn(async () => null) });
    const tasks = useDiscoveryTasks(state, deps);
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/task-state/")) {
        return { status: "completed", progress: {}, logs: [], result: null };
      }
      return { ok: true };
    });

    await tasks.pollTask("run-1", "screen");

    expect(state.taskCompletedToast.value.visible).toBe(false);
    expect(state.activeStep.value).toBe("results");
  });
});

describe("useDiscoveryTasks.pollRecrawl（033 V2 完整性优先）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("终态 status=completed 但 integrity=unverifiable → 不自动进入结果页", async () => {
    const state = makeState({
      recrawlBusy: ref(true),
      activeStep: ref("screen"),
    });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);
    apiRequestMock.mockResolvedValue({
      status: "completed",
      progress: {},
      logs: [],
      integrity: {
        conclusion: "unverifiable", label: "无法确认", evidence_complete: false,
        primary_code: "unit_evidence_missing", primary_reason: "证据不足",
      },
    });

    await tasks.pollRecrawl("recrawl-1");

    expect(state.recrawlBusy.value).toBe(false);
    expect(state.activeStep.value).toBe("screen");
    expect(deps.notify).toHaveBeenCalledWith("证据不足", "warning");
  });
});

// 035 US2（真机问题②，FR-011）：入口 5（启动/刷新自动开新一轮）的
// scrape-only running 守卫——抓取运行中恢复现场，不 reset、不取消、不查历史轮。
describe("useDiscoveryTasks.maybeAutoStartNewRound（035 scrape-only 守卫）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
    apiRequestMock.mockResolvedValue({ has_task: false });
  });

  it("T011: 抓取运行中（scrape-only，screen 侧全空）→ 恢复现场，不触发新一轮", async () => {
    const state = makeState({
      scrapeSnapshot: ref({ status: "running", progress: {}, logs: [] }),
      screenSnapshot: ref(null),
    });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.fetchMergedLatestResult).not.toHaveBeenCalled();
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
    expect(deps.loadLatestResult).not.toHaveBeenCalled();
  });
});

// 035 US2：入口 2（01 页「上传并分析」）守卫的跳回落点按任务类型分派——
// 抓取活 → 02 search；筛选活 → 03 screen（不再一律跳 03）。
describe("useDiscoverySearch.analyzeResume（035 入口守卫落点）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  function makeSearchDeps() {
    return {
      cancelActiveTasksForNewRound: vi.fn(async () => true),
      clearLatestResult: vi.fn(async () => true),
      enterSearchStep: vi.fn(),
      notify: vi.fn(),
      openOneClickDialog: vi.fn(),
      restoreRunningTask: vi.fn(async () => {}),
      startScrape: vi.fn(async () => {}),
    };
  }

  function makeUploadReadyState(overrides: Partial<DiscoveryState> = {}): DiscoveryState {
    return makeState({
      selectedFile: ref(new File(["resume"], "resume.txt", { type: "text/plain" })),
      aiConsent: ref(true),
      activeStep: ref("upload"),
      ...overrides,
    });
  }

  it("抓取运行中 → 跳回 02 search，不开新一轮、不取消、不请求分析接口", async () => {
    const state = makeUploadReadyState({
      scrapeSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    });
    const deps = makeSearchDeps();
    const search = useDiscoverySearch(state, deps);

    await search.analyzeResume();

    expect(state.activeStep.value).toBe("search");
    expect(deps.cancelActiveTasksForNewRound).not.toHaveBeenCalled();
    expect(apiRequestMock).not.toHaveBeenCalled();
  });

  it("筛选运行中 → 跳回 03 screen", async () => {
    const state = makeUploadReadyState({
      screenSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    });
    const deps = makeSearchDeps();
    const search = useDiscoverySearch(state, deps);

    await search.analyzeResume();

    expect(state.activeStep.value).toBe("screen");
    expect(deps.cancelActiveTasksForNewRound).not.toHaveBeenCalled();
  });

  it("无活任务 → 正常走分析流程（不误挡）", async () => {
    const state = makeUploadReadyState();
    const deps = makeSearchDeps();
    const search = useDiscoverySearch(state, deps);
    apiRequestMock.mockResolvedValue({ ok: true, fields: {}, labels: {} });

    await search.analyzeResume();

    expect(deps.cancelActiveTasksForNewRound).toHaveBeenCalled();
    expect(apiRequestMock).toHaveBeenCalledWith("/api/analyze-resume", expect.anything());
  });
});

// ---------------------------------------------------------------------------
// 036 B088：taskProgressFromSnapshot 进度数字提取（供胶囊 running 态）。
// ---------------------------------------------------------------------------
describe("taskProgressFromSnapshot（036 胶囊进度提取）", () => {
  it("null 快照 → done 0", () => {
    expect(taskProgressFromSnapshot(null)).toEqual({ done: 0 });
  });

  it("progress.current + progress.total → done/total", () => {
    expect(taskProgressFromSnapshot({ status: "running", progress: { current: 12, total: 50 }, logs: [] }))
      .toEqual({ done: 12, total: 50 });
  });

  it("total 缺省（未知总量）→ 省略分母", () => {
    expect(taskProgressFromSnapshot({ status: "running", progress: { current: 5 }, logs: [] }))
      .toEqual({ done: 5 });
  });

  it("total 为 0 → 省略分母（不显示假分母）", () => {
    expect(taskProgressFromSnapshot({ status: "running", progress: { current: 5, total: 0 }, logs: [] }))
      .toEqual({ done: 5 });
  });

  it("缺 progress 时回退 success_count/source_total", () => {
    expect(taskProgressFromSnapshot({ status: "running", progress: {}, logs: [], success_count: 3, source_total: 20 }))
      .toEqual({ done: 3, total: 20 });
  });

  it("done 非数字 → 0", () => {
    expect(taskProgressFromSnapshot({ status: "running", progress: {}, logs: [], success_count: undefined }))
      .toEqual({ done: 0 });
  });
});
