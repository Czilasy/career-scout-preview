// 026 B078：maybeAutoStartNewRound 以"是否进过 04 页/已结束保存"为完成判据，
// 不再依赖后端历史轮状态推断（FR-001/FR-005）。
// - 已结束事实（resultsPageSeen/finishedPartial）→ 直接开始新一轮（01 页）。
// - 未结束（本地未完成快照 / 有活动任务 / 历史轮未完成）→ 不触发、恢复现场。
import { computed, ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { apiRequest } from "../../api";
import { useDiscoveryTasks } from "../useDiscoveryTasks";
import { useDiscoverySceneState } from "../useDiscoverySceneState";
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
  openScreenFinishChoice: vi.fn(() => false),
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

describe("useDiscoveryTasks 开新一轮现场归档（Spec041 返工）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
    apiRequestMock.mockResolvedValue({ has_task: false });
    sessionStorage.clear();
  });

  it("任务状态查询一律带当前画像（后端口径：跨画像按不存在处理）", async () => {
    const state = makeState();
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    apiRequestMock.mockResolvedValue({ status: "completed", progress: {}, logs: [] });
    await tasks.pollTask("run-1", "screen");
    expect(apiRequestMock).toHaveBeenCalledWith("/api/task-state/run-1?profile_id=test");

    apiRequestMock.mockClear();
    await tasks.enrichPausedSnapshot(
      "run-2",
      { status: "paused", progress: {}, logs: [] },
      "screen",
    );
    expect(apiRequestMock.mock.calls.some(
      ([url]) => String(url) === "/api/task-state/run-2?profile_id=test",
    )).toBe(true);
  });

  it("旧轮现场归档为该轮历史现场，轮次身份换新、新轮回默认", async () => {
    const state = makeState();
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);
    const scene = useDiscoverySceneState();

    const epoch = scene.ensureRoundEpoch("test");
    const identity = { profileId: "test", runEpoch: epoch, platform: "boss" as const };
    scene.saveCurrent(identity, { selectedJobKey: "boss:job-9", listScrollTop: 120 });
    state.pipelineResultRunId.value = "run-final";
    state.platformState.setResultPlatform("boss");

    await tasks.resetWorkflow();

    // 旧轮现场落到结果 run id 下：历史轮浏览按 run id 取，能接回最后看到的现场。
    expect(scene.getHistory("run-final", identity)).toMatchObject({
      selectedJobKey: "boss:job-9",
      listScrollTop: 120,
    });
    // 新轮换了身份，且是干净默认现场。
    expect(scene.roundEpoch.value).not.toBe(epoch);
    expect(scene.getCurrent({ ...identity, runEpoch: scene.roundEpoch.value })).toMatchObject({
      selectedJobKey: null,
      listScrollTop: 0,
    });
    expect(state.pipelineResultRunId.value).toBe("");
  });
});

describe("抓取暂停占用与轮询保护", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("暂停受理后任务仍在运行：保持“正在暂停”显示，按钮占用不释放", async () => {
    const state = makeState();
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);
    state.scrapeActionBusy.value = "pause-scrape";
    state.scrapeSnapshot.value = {
      status: "running", progress: { message: "抓取中" }, logs: [],
    };

    apiRequestMock.mockResolvedValue({
      status: "running", progress: { message: "抓取中" }, logs: [],
    });
    await tasks.pollTask("scrape-1", "scrape");

    expect(state.scrapeSnapshot.value?.status).toBe("pausing");
    expect(state.scrapeActionBusy.value).toBe("pause-scrape");
    if (state.pollTimer.value) {
      window.clearTimeout(state.pollTimer.value);
      state.pollTimer.value = undefined;
    }
  });

  it("任务真正暂停后释放暂停占用并落 paused 快照", async () => {
    const state = makeState();
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);
    state.scrapeActionBusy.value = "pause-scrape";
    state.scrapeSnapshot.value = { status: "pausing", progress: {}, logs: [] };

    apiRequestMock.mockResolvedValue({
      status: "paused", progress: {}, logs: [], error: "",
    });
    await tasks.pollTask("scrape-1", "scrape");

    expect(state.scrapeActionBusy.value).toBe("");
    expect(state.scrapeSnapshot.value?.status).toBe("paused");
  });
});

describe("useDiscoveryTasks.maybeAutoStartNewRound（026 B078）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
    apiRequestMock.mockResolvedValue({ has_task: false });
  });

  it("Spec041 返工: 已进 04 页且结果在现场 → 原地接回结果页，不清空、不开新一轮", async () => {
    const state = makeState({ resultsPageSeen: ref(true) });
    state.activeStep.value = "results";
    state.resultLoaded.value = true;
    state.pipelineResult.value = { ok: true, jobs: [], dropped: [] };
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.fetchMergedLatestResult).not.toHaveBeenCalled();
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
    expect(state.activeStep.value).toBe("results");
    expect(state.resultsPageSeen.value).toBe(true);
  });

  it("Spec041 返工: 已进 04 页但现场没有结果 → 先问后端；仍无结果才退回新一轮", async () => {
    const state = makeState({ resultsPageSeen: ref(true) });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.fetchMergedLatestResult).toHaveBeenCalled();
    expect(deps.clearLatestResult).toHaveBeenCalled();
    expect(state.activeStep.value).toBe("upload");
    expect(state.resultsPageSeen.value).toBe(false);
  });

  it("Spec041 返工: 完成事实在、后端有最新结果 → 从最新轮补齐并落在结果页", async () => {
    const state = makeState({
      resultsPageSeen: ref(true),
      resultsBootstrapPending: ref(true),
    });
    const deps = makeDeps({
      fetchMergedLatestResult: vi.fn(async () => ({
        merged: { ok: true, jobs: [{ job_id: "j" }], dropped: [] },
        newer: {
          platform: "zhilian" as const,
          data: { status: "succeeded", source_run_id: "z-run" },
        },
        platformStatuses: { zhilian: "succeeded" },
      })),
      loadLatestResult: vi.fn(async () => {
        state.pipelineResult.value = { ok: true, jobs: [{ job_id: "j" }], dropped: [] };
        state.resultLoaded.value = true;
      }),
    });
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.loadLatestResult).toHaveBeenCalled();
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
    expect(state.activeStep.value).toBe("results");
    expect(state.resultsBootstrapPending.value).toBe(false);
  });

  it("T004b: 结束保存（finishedPartial=true）无结果可恢复时退回新一轮", async () => {
    const state = makeState({ finishedPartial: ref(true) });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.fetchMergedLatestResult).toHaveBeenCalled();
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

  it.each(["completed_with_pending", "partial"])(
    "待确认状态 %s → 恢复结果，不自动开始新一轮",
    async (status) => {
      const state = makeState();
      const deps = makeDeps({
        fetchMergedLatestResult: vi.fn(async () => ({
          merged: { ok: true, jobs: [] },
          newer: { platform: "zhilian" as const, data: { status } },
          platformStatuses: { zhilian: status },
        })),
      });
      const tasks = useDiscoveryTasks(state, deps);

      await tasks.maybeAutoStartNewRound();

      expect(deps.loadLatestResult).toHaveBeenCalled();
      expect(deps.clearLatestResult).not.toHaveBeenCalled();
    },
  );
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

  it("T003c: 抓取中断标记不再阻塞新一轮探测", async () => {
    const state = makeState({ interruptedRunId: ref("run-2") });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.fetchMergedLatestResult).toHaveBeenCalled();
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
    expect(deps.loadLatestResult).not.toHaveBeenCalled();
  });

  it("T003d: 新一轮开始后，旧轮晚到的完成响应不再回写结果", async () => {
    const state = makeState({
      screenTaskId: ref("old-screen"),
      screenBusy: ref(true),
      screenSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    });
    let resolveTaskState!: (snapshot: unknown) => void;
    const taskState = new Promise((resolve) => { resolveTaskState = resolve; });
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.includes("/api/task-state/old-screen")) return taskState;
      return { ok: true, has_task: false };
    });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);
    const pollPromise = tasks.pollTask("old-screen", "screen");

    await Promise.resolve();
    const resetPromise = tasks.resetWorkflow();
    resolveTaskState({
      status: "completed", progress: {}, logs: [],
      result: { ok: true, jobs: [{ job_id: "old", title: "旧轮结果" }], dropped: [] },
    });
    await Promise.all([pollPromise, resetPromise]);

    expect(state.activeStep.value).toBe("upload");
    expect(state.pipelineResult.value).toBeNull();
    expect(deps.setPipelineResult).not.toHaveBeenCalled();
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
      if (url.startsWith("/api/result-history")) return { ok: true, items: [] };
      return { ok: true };
    });

    await tasks.pollTask("run-1", "screen");

    expect(state.taskCompletedToast.value.visible).toBe(true);
    expect(state.activeStep.value).not.toBe("results");
    expect(apiRequestMock).toHaveBeenCalledWith("/api/result-history?profile_id=test");
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

  it("完成响应内联兜底时沿用外层任务平台", async () => {
    const state = makeState({ screenTaskId: ref("run-inline-zhilian") });
    const deps = makeDeps({ fetchMergedLatestResult: vi.fn(async () => null) });
    const tasks = useDiscoveryTasks(state, deps);
    apiRequestMock.mockResolvedValue({
      status: "completed",
      platform: "zhilian",
      progress: {},
      logs: [],
      result: { jobs: [{ job_id: "j1", title: "智联岗位" }], dropped: [] },
    });

    await tasks.pollTask("run-inline-zhilian", "screen");

    expect(deps.setPipelineResult).toHaveBeenCalledWith(expect.objectContaining({
      platform: "zhilian",
      jobs: [{ job_id: "j1", title: "智联岗位" }],
    }));
    expect(state.activeStep.value).toBe("results");
  });
});

describe("useDiscoveryTasks.pollTask 错误态不阻塞新任务", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it.each(["failed", "interrupted"] as const)(
    "抓取 %s 保留错误快照但清除旧恢复标记",
    async (status) => {
      const taskId = `scrape-${status}`;
      const state = makeState({
        scrapeTaskId: ref(taskId),
        pausedRunId: ref("stale-paused"),
        interruptedRunId: ref("stale-interrupted"),
      });
      apiRequestMock.mockResolvedValue({
        status,
        progress: { message: "抓取失败" },
        logs: [],
        error: "抓取失败",
      });
      const tasks = useDiscoveryTasks(state, makeDeps());

      await tasks.pollTask(taskId, "scrape");

      expect(state.scrapeSnapshot.value?.status).toBe(status);
      expect(state.pausedRunId.value).toBe("");
      expect(state.interruptedRunId.value).toBe("");
      expect(state.pipelineBusy.value).toBe(false);
    },
  );

  it.each(["failed", "interrupted"] as const)(
    "AI 筛选 %s 保留错误快照但清除旧恢复标记",
    async (status) => {
      const taskId = `screen-${status}`;
      const state = makeState({
        screenTaskId: ref(taskId),
        screenBusy: ref(true),
        pausedRunId: ref("stale-paused"),
        interruptedRunId: ref("stale-interrupted"),
      });
      apiRequestMock.mockResolvedValue({
        status,
        progress: { message: "AI 筛选失败" },
        logs: [],
        error: "AI 筛选失败",
      });
      const tasks = useDiscoveryTasks(state, makeDeps());

      await tasks.pollTask(taskId, "screen");

      expect(state.screenSnapshot.value?.status).toBe(status);
      expect(state.screenBusy.value).toBe(false);
      expect(state.pausedRunId.value).toBe("");
      expect(state.interruptedRunId.value).toBe("");
      expect(state.pipelineBusy.value).toBe(false);
    },
  );

  it("新一轮开始后，旧重抓轮询的完成响应不再回写", async () => {
    const state = makeState({
      recrawlBusy: ref(true),
      activeStep: ref("screen"),
      recrawlSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    });
    let resolveTaskState!: (snapshot: unknown) => void;
    const taskState = new Promise((resolve) => { resolveTaskState = resolve; });
    apiRequestMock.mockReturnValue(taskState);
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);
    const pollPromise = tasks.pollRecrawl("old-recrawl");

    await Promise.resolve();
    state.workflowEpoch.value += 1;
    resolveTaskState({
      status: "completed",
      progress: {},
      logs: [],
      result: { updates: { old: { verdict: "match" } } },
    });
    await pollPromise;

    expect(state.recrawlSnapshot.value?.status).toBe("running");
    expect(state.recrawlBusy.value).toBe(true);
    expect(state.activeStep.value).toBe("screen");
    expect(deps.notify).not.toHaveBeenCalled();
  });
});

describe("useDiscoveryTasks.abandonRound", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("cancels the current task, clears the round, and returns to upload", async () => {
    const state = makeState({
      activeStep: ref("screen"),
      scrapeTaskId: ref("scrape-abandon-1"),
      scrapeSnapshot: ref({ status: "paused", progress: { current: 3 }, logs: [] }),
      pausedRunId: ref("scrape-abandon-1"),
      selectedKeywords: ref(["Python"]),
    });
    const deps = makeDeps();
    apiRequestMock.mockResolvedValue({ ok: true, status: "cancelled" });
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.abandonRound();

    expect(apiRequestMock).toHaveBeenCalledWith(
      "/api/task/cancel/scrape-abandon-1", { method: "POST" },
    );
    expect(deps.clearLatestResult).toHaveBeenCalled();
    expect(state.activeStep.value).toBe("upload");
    expect(state.scrapeTaskId.value).toBe("");
    expect(state.scrapeSnapshot.value).toBeNull();
    // 用户拍板：第 2 页输入（关键词/城市/画像）随"放弃本轮/开新一轮"保留，
    // 方便换个平台直接复用；只有用户手动改才变。
    expect(state.selectedKeywords.value).toEqual(["Python"]);
    expect(state.cancelBusy.value).toBe(false);
    expect(deps.notify).toHaveBeenCalledWith("已放弃本轮，已回到第一步", "info");
  });

  it("keeps the current round when cancellation cannot be confirmed", async () => {
    const state = makeState({
      activeStep: ref("search"),
      scrapeTaskId: ref("scrape-abandon-2"),
      scrapeSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    });
    const deps = makeDeps();
    apiRequestMock.mockRejectedValue(new Error("cancel unavailable"));
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.abandonRound();

    expect(state.activeStep.value).toBe("search");
    expect(state.scrapeTaskId.value).toBe("scrape-abandon-2");
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
    expect(deps.notify).not.toHaveBeenCalledWith("已放弃本轮，已回到第一步", "info");
    expect(state.cancelBusy.value).toBe(false);
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

  it.each(["failed", "interrupted"] as const)(
    "重抓 %s 保留错误快照但清除旧恢复标记",
    async (status) => {
      const state = makeState({
        recrawlBusy: ref(true),
        recrawlTaskId: ref(`recrawl-${status}`),
        pausedRunId: ref("stale-paused"),
        interruptedRunId: ref("stale-interrupted"),
      });
      const deps = makeDeps();
      const tasks = useDiscoveryTasks(state, deps);
      apiRequestMock.mockResolvedValue({
        status,
        progress: { message: "重抓失败" },
        logs: [],
        error: "重抓失败",
      });

      await tasks.pollRecrawl(`recrawl-${status}`);

      expect(state.recrawlSnapshot.value?.status).toBe(status);
      expect(state.recrawlBusy.value).toBe(false);
      expect(state.pausedRunId.value).toBe("");
      expect(state.interruptedRunId.value).toBe("");
      expect(state.pipelineBusy.value).toBe(false);
    },
  );
});

describe("useDiscoveryTasks.saveScrapedOnlySnapshot（039 数字永久展示）", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("定格“已抓取，未筛选”轮时，03 面板拿到真实 0（已完成 0 / N、未开始 N）", async () => {
    apiRequestMock.mockResolvedValue({
      saved: true,
      run_id: "run-1",
      result: {
        ok: true, jobs: [], dropped: [], total_scraped: 40,
        total_kept: 0, total_matched: 0, total_dropped: 0, profile_summary: "",
      },
    });
    const state = makeState({
      scrapeTaskId: ref("scrape-1"),
      scrapeSnapshot: ref({
        status: "done", stage: "done", progress: {}, logs: [],
        scraped_count: 40, source_total: 40,
      }),
    });
    const deps = makeDeps();
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.saveScrapedOnlySnapshot();

    const screen = state.screenSnapshot.value;
    expect(screen?.total).toBe(40);
    expect(screen?.success_count).toBe(0);
    expect(screen?.fail_count).toBe(0);
    expect(screen?.unstarted_count).toBe(40);
    expect(screen?.scraped_count).toBe(40);
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
      props: { profileId: "test" },
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
    await flushPromises();

    expect(deps.cancelActiveTasksForNewRound).toHaveBeenCalled();
    expect(apiRequestMock).toHaveBeenCalledWith("/api/analyze-resume", expect.anything());
  });

  it("分析完成时用户正在看历史：只更新当前分析，不打断历史画面", async () => {
    const state = makeUploadReadyState({ activeStep: ref("results") });
    const historyResult = { jobs: [{ job_id: "history-job", title: "历史岗位" }], dropped: [] };
    state.historyRound.value = { runId: "history-run", platform: "boss", status: "done", jobCount: 1 };
    state.pipelineResult.value = historyResult;
    const deps = makeSearchDeps();
    const search = useDiscoverySearch(state, deps);
    apiRequestMock.mockResolvedValue({ ok: true, fields: {}, labels: {} });

    await search.analyzeResume();
    await flushPromises();

    expect(state.historyRound.value?.runId).toBe("history-run");
    expect(state.pipelineResult.value).toMatchObject({ jobs: [{ job_id: "history-job" }] });
    expect(state.activeStep.value).toBe("results");
    expect(deps.enterSearchStep).not.toHaveBeenCalled();
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
