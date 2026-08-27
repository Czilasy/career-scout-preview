// 026 B078：maybeAutoStartNewRound 以"是否进过 04 页/已结束保存"为完成判据，
// 不再依赖后端历史轮状态推断（FR-001/FR-005）。
// - 已结束事实（resultsPageSeen/finishedPartial）→ 直接开始新一轮（01 页）。
// - 未结束（本地未完成快照 / 有活动任务 / 历史轮未完成）→ 不触发、恢复现场。
import { ref } from "vue";
import { apiRequest } from "../../api";
import { useDiscoveryTasks } from "../useDiscoveryTasks";

vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
  errorMessage: (error: unknown, fallback: string) => fallback,
  settingsApi: { get: vi.fn(), save: vi.fn() },
  userFacingMessage: (error: unknown, fallback: string) => fallback,
}));

const apiRequestMock = apiRequest as unknown as ReturnType<typeof vi.fn>;

function makeState(overrides: Record<string, unknown> = {}) {
  const base: Record<string, unknown> = {
    activeCategory: ref("matched"),
    activeStep: ref("upload"),
    activeTaskRestored: ref(false),
    advancedSettings: ref({ pages: 2 }),
    aiConsent: ref(false),
    analysisReady: ref(false),
    appliedResumePlatforms: ref(new Set()),
    autoScreenArmed: ref(false),
    autoScreenFields: ref({}),
    autoScreenProfile: ref(""),
    cityText: ref(""),
    customCity: ref(""),
    customKeyword: ref(""),
    currentRoundStatus: ref(""),
    draftPlatform: ref("boss"),
    executionSelection: ref("balanced"),
    filterValues: ref({ boss: {}, zhilian: {} }),
    finishedPartial: ref(false),
    groups: ref({ uncertain: [] }),
    historyBackToLatest: vi.fn(),
    historyMode: ref(false),
    historyRound: ref(null),
    interruptedRunId: ref(""),
    isScrapedOnly: ref(false),
    keywords: ref([]),
    locationDraft: { reset: vi.fn(), allLocations: () => [] },
    oneClickOpen: ref(false),
    pausedRunId: ref(""),
    pausingScreen: ref(false),
    pipelineResult: ref(null),
    pipelineResultRunId: ref(""),
    pollRetryCount: ref(0),
    pollTimer: ref<number | undefined>(undefined),
    profileError: ref(""),
    profileFacts: ref({}),
    profileSummary: ref(""),
    recrawlBusy: ref(false),
    recrawlPlatformGuide: ref(null),
    recrawlRetryCount: ref(0),
    recrawlSnapshot: ref(null),
    recrawlTaskId: ref(""),
    rejectedIds: ref(new Set()),
    restoredTaskHint: ref(""),
    resultLoaded: ref(false),
    resultPlatformFilter: ref("all"),
    resultRunIds: ref({ boss: "", zhilian: "" }),
    resultsPageSeen: ref(false),
    resumeAnalysis: ref(null),
    scopePreview: ref(null),
    scopePreviewBusy: ref(false),
    scrapeBusy: ref(false),
    scrapeCompleted: ref(false),
    scrapeSnapshot: ref(null),
    scrapeTaskId: ref(""),
    screenBusy: ref(false),
    screenPanelOpen: ref(true),
    screenSnapshot: ref(null),
    screenTaskId: ref(""),
    selectedFile: ref(null),
    selectedKeywords: ref([]),
    uncertainByPlatform: ref({ boss: 0, zhilian: 0 }),
    unfinishedWorkflowRestored: ref(false),
  };
  return { ...base, ...overrides } as any;
}

function makeDeps(overrides: Record<string, unknown> = {}) {
  return {
    clearLatestResult: vi.fn(async () => true),
    clearWorkflowState: vi.fn(),
    fetchMergedLatestResult: vi.fn(async () => null),
    loadLatestResult: vi.fn(async () => {}),
    notify: vi.fn(),
    roundFlow: { clearRoundContext: vi.fn() },
    ...overrides,
  } as any;
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
        newer: { platform: "boss", data: { status: "succeeded" } },
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
        newer: { platform: "boss", data: { status: "paused" } },
        platformStatuses: { boss: "paused", zhilian: "succeeded" },
      })),
    });
    const tasks = useDiscoveryTasks(state, deps);

    await tasks.maybeAutoStartNewRound();

    expect(deps.loadLatestResult).toHaveBeenCalled();
    expect(deps.clearLatestResult).not.toHaveBeenCalled();
  });
});
