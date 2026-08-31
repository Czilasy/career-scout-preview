// 026 B078：restoreRunningTask 以"是否进过 04 页"为唯一闸门。
// - 已进 04 页（resultsPageSeen=true）＝上次流程已结束 → 即使后端残留
//   interrupted run 也不恢复 02/03 页、不弹"服务重启被中断"提示（FR-002/FR-003）。
// - 未进 04 页 → 走既有 interrupted 恢复续跑（FR-004，B068 行为不变）。
import { ref } from "vue";
import { apiRequest } from "../../api";
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
