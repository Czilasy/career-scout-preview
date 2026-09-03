// 026 B078：restoreRunningTask 以"是否进过 04 页"为唯一闸门。
// - 已进 04 页（resultsPageSeen=true）＝上次流程已结束 → 即使后端残留
//   interrupted run 也不恢复 02/03 页、不弹"服务重启被中断"提示（FR-002/FR-003）。
// - 未进 04 页 → 走既有 interrupted 恢复续跑（FR-004，B068 行为不变）。
import { ref } from "vue";
import { apiRequest } from "../../api";
import type { FrozenSearchScope } from "../../types";
import { useDiscoveryExecution } from "../useDiscoveryExecution";
import { useDiscoveryState } from "../useDiscoveryState";
import type { DiscoveryState } from "../useDiscoveryState";
import type { ExecutionNeeds } from "../discoveryDeps";

vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
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

  it("T002: 未进 04 页 + interrupted → 走既有恢复（设 interruptedRunId、进 03 页、弹提示）", async () => {
    apiRequestMock.mockResolvedValue(interruptedScreenResponse);
    const state = makeState({ resultsPageSeen: ref(false) });
    const deps = makeDeps();
    const execution = useDiscoveryExecution(state, deps);

    await execution.restoreRunningTask();

    expect(state.interruptedRunId.value).toBe("screen-t1");
    expect(state.screenTaskId.value).toBe("screen-t1");
    expect(deps.enterScreenStep).toHaveBeenCalled();
    expect(state.restoredTaskHint.value).toContain("服务重启被中断");
  });

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
