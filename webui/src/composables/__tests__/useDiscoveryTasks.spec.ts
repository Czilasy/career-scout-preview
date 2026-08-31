// 026 B078：maybeAutoStartNewRound 以"是否进过 04 页/已结束保存"为完成判据，
// 不再依赖后端历史轮状态推断（FR-001/FR-005）。
// - 已结束事实（resultsPageSeen/finishedPartial）→ 直接开始新一轮（01 页）。
// - 未结束（本地未完成快照 / 有活动任务 / 历史轮未完成）→ 不触发、恢复现场。
import { ref } from "vue";
import { apiRequest } from "../../api";
import { useDiscoveryTasks } from "../useDiscoveryTasks";
import { useDiscoveryState } from "../useDiscoveryState";
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
