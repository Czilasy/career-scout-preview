// 026 B078：restoreRunningTask 以"是否进过 04 页"为唯一闸门。
// - 已进 04 页（resultsPageSeen=true）＝上次流程已结束 → 即使后端残留
//   interrupted run 也不恢复 02/03 页、不弹"服务重启被中断"提示（FR-002/FR-003）。
// - 未进 04 页 → 走既有 interrupted 恢复续跑（FR-004，B068 行为不变）。
import { ref } from "vue";
import { apiRequest } from "../../api";
import { createPlatformState } from "../../discovery";
import { useDiscoveryExecution } from "../useDiscoveryExecution";

vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
  errorMessage: (error: unknown, fallback: string) => fallback,
}));
vi.mock("../../composables/useTheme", () => ({
  setThemePlatform: vi.fn(),
}));

const apiRequestMock = apiRequest as unknown as ReturnType<typeof vi.fn>;

function makeState(overrides: Record<string, unknown> = {}) {
  const base: Record<string, unknown> = {
    activeCategory: ref("matched"),
    activeStep: ref("upload"),
    activeTaskRestored: ref(false),
    advancedPanelsOpen: ref(false),
    analysisReady: ref(false),
    autoScreenArmed: ref(false),
    autoScreenFields: ref<Record<string, string[]>>({}),
    autoScreenProfile: ref(""),
    cancelBusy: ref(false),
    cityList: ref<string[]>([]),
    currentRoundStatus: ref(""),
    draftPlatform: ref<"boss" | "zhilian">("boss"),
    effectiveSearchCities: ref<string[]>(["上海"]),
    filterValues: ref<Record<string, Record<string, string[]>>>({
      boss: {}, zhilian: {},
    }),
    finishSaveBusy: ref(false),
    finishedPartial: ref(false),
    historyDetail: ref(null),
    historyMode: ref(false),
    historyRound: ref(null),
    historyScreenBusy: ref(false),
    interruptedRunId: ref(""),
    locationDraft: { allLocations: () => [] },
    nationalScopeConfirm: ref(null),
    oneClickOpen: ref(false),
    pausedRunId: ref(""),
    pipelineBusy: ref(false),
    pipelineResult: ref(null),
    pipelineResultRunId: ref(""),
    platformBeforeHistory: ref(null),
    platformState: createPlatformState("boss"),
    pollRetryCount: ref(0),
    pollTimer: ref<number | undefined>(undefined),
    profileConfirmed: ref(false),
    profileError: ref(""),
    profileFacts: ref<Record<string, unknown>>({}),
    profileSummary: ref(""),
    recrawlBusy: ref(false),
    recrawlPlatformGuide: ref(null),
    recrawlSnapshot: ref(null),
    recrawlTaskId: ref(""),
    restoredTaskHint: ref(""),
    resultEpoch: ref(0),
    resultLoaded: ref(false),
    resultPlatformFilter: ref("all"),
    resultRunIds: ref({ boss: "", zhilian: "" }),
    resultsPageSeen: ref(false),
    schemaRef: ref(null),
    scrapeBusy: ref(false),
    scrapeCompleted: ref(false),
    scrapeSnapshot: ref(null),
    scrapeTaskId: ref(""),
    screenBusy: ref(false),
    screenPanelOpen: ref(false),
    screenSnapshot: ref(null),
    screenTaskId: ref(""),
    searchPanelsOpen: ref(false),
    selectedKeywords: ref<string[]>([]),
    switchAccountId: ref(""),
    switchAccounts: ref([]),
  };
  return { ...base, ...overrides } as any;
}

function makeDeps(overrides: Record<string, unknown> = {}) {
  return {
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
    pollRecrawl: vi.fn(async () => {}),
    pollTask: vi.fn(async () => {}),
    requireProfileConfirmed: vi.fn(() => true),
    restoreLocationsFromContext: vi.fn(),
    returnToLatest: vi.fn(async () => {}),
    saveScrapedOnlySnapshot: vi.fn(async () => {}),
    setDraftPlatform: vi.fn(),
    setPipelineResult: vi.fn(),
    showLoginGuide: vi.fn(),
    validateProfileForScreen: vi.fn(() => true),
    roundFlow: { restoreRoundContext: vi.fn() },
    ...overrides,
  } as any;
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
