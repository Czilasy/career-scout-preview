// 026 B078：useDiscoveryWorkflow 以"是否进过 04 页"为流程结束唯一判据。
// - persistWorkflowState 持久化 resultsPageSeen 准确反映进没进 04 页；
//   已结束时快照不残留未完成语义（FR-001）。
// - 进 04 页（markResultsPageSeen）与结束保存（persistFinishedState）的
//   已结束事实独立持久化（localStorage），restoreWorkflowState 据此恢复。
import { nextTick, ref } from "vue";
import { useDiscoveryState } from "../useDiscoveryState";
import type { DiscoveryState } from "../useDiscoveryState";
import { useDiscoveryWorkflow } from "../useDiscoveryWorkflow";
import type { WorkflowNeeds } from "../discoveryDeps";
import { useDiscoverySceneState } from "../useDiscoverySceneState";

const WORKFLOW_KEY = "career-scout-workflow:test";
const FINISHED_KEY = "career-scout-workflow:test:finished";

// 031 B8 补遗：state fake = 真实状态工厂 + overrides（字段永齐全、类型真实，
// 消除 as any 兜底）；workflowStateRestored 置 true 对齐 persist 闸门的测试
// 前置（真实默认 false，persistWorkflowState 会被闸门跳过）。
function makeState(overrides: Partial<DiscoveryState> = {}): DiscoveryState {
  const state = useDiscoveryState({ profileId: "test" }, () => {});
  state.workflowStateRestored.value = true;
  return Object.assign(state, overrides);
}

function makeDeps(overrides: Partial<WorkflowNeeds> = {}): WorkflowNeeds {
  return Object.assign({ emit: vi.fn() }, overrides);
}

describe("useDiscoveryWorkflow（026 B078）", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it("T003a: 未结束时持久化快照携带准确的 resultsPageSeen（false）", () => {
    const state = makeState({
      analysisReady: ref(true),
      scrapeTaskId: ref("s1"),
      activeStep: ref("search"),
      resultsPageSeen: ref(false),
    });
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.persistWorkflowState();

    const saved = JSON.parse(
      sessionStorage.getItem(WORKFLOW_KEY) as string,
    );
    expect(saved.unfinished).toBe(true);
    expect(saved.resultsPageSeen).toBe(false);
    expect(saved.activeStep).toBe("search");
  });

  it("现场先写入、工作流后写入时仍保留完整现场结构", () => {
    const state = makeState({
      analysisReady: ref(true),
      scrapeTaskId: ref("s1"),
      activeStep: ref("search"),
    });
    const workflow = useDiscoveryWorkflow(state, makeDeps());
    const sceneStore = useDiscoverySceneState();
    const identity = { profileId: "test", runEpoch: "run-1", platform: "boss" as const };
    sceneStore.saveCurrent(identity, { visibleCount: 60, selectedJobKey: "boss:job-60" });
    sceneStore.persistToSession("test");
    workflow.persistWorkflowState();

    const saved = JSON.parse(sessionStorage.getItem(WORKFLOW_KEY) as string);
    expect(saved.pageScene.version).toBe(2);
    expect(saved.pageScene.current["test::run-1::boss"].visibleCount).toBe(60);
  });

  it("清理已结束工作流时保留页面现场存档", () => {
    const state = makeState({ resultsPageSeen: ref(true), scrapeTaskId: ref("s1") });
    const workflow = useDiscoveryWorkflow(state, makeDeps());
    const sceneStore = useDiscoverySceneState();
    const identity = { profileId: "test", runEpoch: "run-1", platform: "boss" as const };
    sceneStore.saveCurrent(identity, { selectedJobKey: "boss:kept" });

    workflow.persistWorkflowState();

    const saved = JSON.parse(sessionStorage.getItem(WORKFLOW_KEY) as string);
    expect(saved.unfinished).toBeUndefined();
    expect(saved.pageScene.current["test::run-1::boss"].selectedJobKey).toBe("boss:kept");
  });

  it("分析完成后不等组件卸载也会保存现场，刷新仍能找回新结果", async () => {
    const state = makeState({
      activeStep: ref("upload"),
      analysisReady: ref(false),
      keywords: ref([]),
      selectedKeywords: ref([]),
      profileSummary: ref(""),
    });
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.restoreWorkflowState();
    state.analysisReady.value = true;
    state.activeStep.value = "search";
    state.keywords.value = [{ word: "新分析关键词", recommended: true }];
    state.selectedKeywords.value = ["新分析关键词"];
    state.profileSummary.value = "这是本次新分析得到的求职画像";
    await nextTick();

    const saved = JSON.parse(sessionStorage.getItem(WORKFLOW_KEY) as string);
    expect(saved.analysisReady).toBe(true);
    expect(saved.activeStep).toBe("search");
    expect(saved.keywords).toEqual([{ word: "新分析关键词", recommended: true }]);
    expect(saved.profileSummary).toBe("这是本次新分析得到的求职画像");
  });

  it("T003b: 已进 04 页（resultsPageSeen=true）→ 快照不残留未完成态", () => {
    const state = makeState({
      resultsPageSeen: ref(true),
      scrapeTaskId: ref("s1"),
    });
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.persistWorkflowState();

    expect(sessionStorage.getItem(WORKFLOW_KEY)).toBeNull();
  });

  it("T003c: 结束保存（finishedPartial=true）同样视为已结束，不写未完成快照", () => {
    const state = makeState({
      finishedPartial: ref(true),
      screenTaskId: ref("p1"),
    });
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.persistWorkflowState();

    expect(sessionStorage.getItem(WORKFLOW_KEY)).toBeNull();
  });

  it("T003d: markResultsPageSeen 持久化「已进 04 页」事实并清空未完成快照", () => {
    const state = makeState();
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.markResultsPageSeen();

    expect(state.resultsPageSeen.value).toBe(true);
    const finished = JSON.parse(localStorage.getItem(FINISHED_KEY) as string);
    expect(finished.resultsPageSeen).toBe(true);
    expect(sessionStorage.getItem(WORKFLOW_KEY)).toBeNull();
  });

  it("T003e: restoreWorkflowState 从持久化的已结束事实恢复 resultsPageSeen", () => {
    localStorage.setItem(
      FINISHED_KEY,
      JSON.stringify({ resultsPageSeen: true, finishedPartial: false }),
    );
    const state = makeState();
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.restoreWorkflowState();

    expect(state.resultsPageSeen.value).toBe(true);
  });

  it("T003f: 未结束（无已结束事实）时恢复为 resultsPageSeen=false", () => {
    const state = makeState();
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.restoreWorkflowState();

    expect(state.resultsPageSeen.value).toBe(false);
  });

  it("T003g: 已结束事实 + 残留未完成快照 → resultsPageSeen 不被快照覆盖", () => {
    localStorage.setItem(
      FINISHED_KEY,
      JSON.stringify({ resultsPageSeen: true, finishedPartial: false }),
    );
    sessionStorage.setItem(WORKFLOW_KEY, JSON.stringify({
      version: 1, unfinished: true, resultsPageSeen: false,
      activeStep: "screen", analysisReady: true, keywords: [], selectedKeywords: [],
      cityText: "", filterValues: { boss: {}, zhilian: {} }, profileSummary: "",
      profileFacts: {}, scrapeTaskId: "", screenTaskId: "", pausedRunId: "",
      interruptedRunId: "", recrawlTaskId: "", scrapeCompleted: false,
      scrapeSnapshot: null, screenSnapshot: null, recrawlSnapshot: null,
      pipelineResult: null, pipelineResultRunId: "", currentRoundStatus: "",
      resultLoaded: false,
    }));
    const state = makeState();
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.restoreWorkflowState();

    expect(state.resultsPageSeen.value).toBe(true);
  });

  it("T003h: 已结束 + 残留快照 → activeStep 不被恢复（不短暂回 02/03 页）", () => {
    localStorage.setItem(
      FINISHED_KEY,
      JSON.stringify({ resultsPageSeen: true, finishedPartial: false }),
    );
    sessionStorage.setItem(WORKFLOW_KEY, JSON.stringify({
      version: 1, unfinished: true, resultsPageSeen: false,
      activeStep: "screen", analysisReady: true, keywords: [], selectedKeywords: [],
      cityText: "", filterValues: { boss: {}, zhilian: {} }, profileSummary: "",
      profileFacts: {}, scrapeTaskId: "", screenTaskId: "", pausedRunId: "",
      interruptedRunId: "", recrawlTaskId: "", scrapeCompleted: false,
      scrapeSnapshot: null, screenSnapshot: null, recrawlSnapshot: null,
      pipelineResult: null, pipelineResultRunId: "", currentRoundStatus: "",
      resultLoaded: false,
    }));
    const state = makeState();
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.restoreWorkflowState();

    // 已结束流程不恢复残留快照的步骤，避免刷新瞬间短暂回到 02/03 页
    expect(state.activeStep.value).toBe("upload");
    expect(state.unfinishedWorkflowRestored.value).toBe(false);
    expect(state.resultsPageSeen.value).toBe(true);
  });

  it("Spec041 返工: 完成态结果页现场刷新后原地接回（不再被清成 01 空上传页）", () => {
    sessionStorage.setItem(WORKFLOW_KEY, JSON.stringify({
      version: 1, unfinished: true, resultsPageSeen: false,
      activeStep: "results", analysisReady: true,
      scrapeTaskId: "scrape-done", screenTaskId: "screen-done", scrapeCompleted: true,
      scrapeSnapshot: { status: "completed", progress: {}, logs: [] },
      screenSnapshot: { status: "completed", progress: {}, logs: [] },
      pipelineResult: {
        ok: true,
        jobs: [{ job_id: "old", title: "旧快照结果" }],
        dropped: [], total_kept: 1, total_dropped: 0,
      },
      pipelineResultRunId: "completed-run", currentRoundStatus: "screened",
      resultLoaded: true,
    }));
    const state = makeState();
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.restoreWorkflowState();

    // 真实验收失败项一：刷新后仍停在当前结果页，当前轮结果继续存在。
    expect(state.activeStep.value).toBe("results");
    expect(state.pipelineResult.value).not.toBeNull();
    expect(state.resultLoaded.value).toBe(true);
    expect(state.unfinishedWorkflowRestored.value).toBe(false);
    expect(state.resultsPageSeen.value).toBe(true);
    // 完成态现场不再被清掉，而是写成完成态快照供下一次刷新继续接回。
    const saved = JSON.parse(sessionStorage.getItem(WORKFLOW_KEY) as string);
    expect(saved.completed).toBe(true);
    expect(saved.unfinished).toBe(false);
    expect(saved.activeStep).toBe("results");
  });

  it("Spec041 返工: 完成态快照恢复平台身份、结果分类与筛选档（不先给一屏 BOSS）", () => {
    sessionStorage.setItem(WORKFLOW_KEY, JSON.stringify({
      version: 2, unfinished: false, completed: true,
      activeStep: "results", analysisReady: true,
      keywords: [], selectedKeywords: [], cityText: "",
      filterValues: { boss: {}, zhilian: {} }, profileSummary: "", profileFacts: {},
      scrapeTaskId: "scrape-z", screenTaskId: "screen-z", pausedRunId: "",
      interruptedRunId: "", recrawlTaskId: "", scrapeCompleted: true,
      scrapeSnapshot: { status: "completed", progress: {}, logs: [], platform: "zhilian" },
      screenSnapshot: { status: "completed", progress: {}, logs: [], platform: "zhilian" },
      recrawlSnapshot: null,
      pipelineResult: {
        ok: true, platform: "zhilian",
        jobs: [{ job_id: "z1", platform: "zhilian", verdict: "uncertain" }],
        dropped: [], total_kept: 1, total_dropped: 0,
      },
      pipelineResultRunId: "z-run", currentRoundStatus: "screened", resultLoaded: true,
      resultsPageSeen: true, activeCategory: "uncertain", resultPlatformFilter: "zhilian",
      platform: "zhilian", resultPlatform: "zhilian",
    }));
    const state = makeState();
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.restoreWorkflowState();

    expect(state.activeStep.value).toBe("results");
    expect(state.draftPlatform.value).toBe("zhilian");
    expect(state.platformState.result).toBe("zhilian");
    expect(state.activeCategory.value).toBe("uncertain");
    expect(state.resultPlatformFilter.value).toBe("zhilian");
    expect(state.resultsBootstrapPending.value).toBe(false);
  });

  it("Spec041 返工: 完成事实在、会话现场缺失 → 先恢复平台与结果页骨架等后端补齐", () => {
    localStorage.setItem(
      FINISHED_KEY,
      JSON.stringify({
        resultsPageSeen: true, finishedPartial: false,
        platform: "zhilian", runId: "z-run",
      }),
    );
    const state = makeState();
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.restoreWorkflowState();

    // 不闪 BOSS 空上传页：平台与结果页先同步接回，结果由启动流程补齐。
    expect(state.activeStep.value).toBe("results");
    expect(state.draftPlatform.value).toBe("zhilian");
    expect(state.resultsBootstrapPending.value).toBe(true);
    expect(state.resultsPageSeen.value).toBe(true);
  });

  it("Spec041: 恢复快照的 analysisReady 不触发面板自动展开（收起现场不被覆盖）", async () => {
    sessionStorage.setItem(WORKFLOW_KEY, JSON.stringify({
      version: 2, unfinished: true, activeStep: "search", analysisReady: true,
      keywords: [], selectedKeywords: [], cityText: "",
      filterValues: { boss: {}, zhilian: {} }, profileSummary: "",
      profileFacts: {}, scrapeTaskId: "s1", screenTaskId: "", pausedRunId: "",
      interruptedRunId: "", recrawlTaskId: "", scrapeCompleted: true,
      scrapeSnapshot: { status: "completed", progress: {}, logs: [] },
      screenSnapshot: null, recrawlSnapshot: null, pipelineResult: null,
      pipelineResultRunId: "", currentRoundStatus: "", resultLoaded: false,
    }));
    const state = makeState({
      activeStep: ref("upload"),
      analysisReady: ref(false),
      searchPanelsOpen: ref(false),
      advancedPanelsOpen: ref(false),
      screenPanelOpen: ref(false),
    });
    const workflow = useDiscoveryWorkflow(state, makeDeps());

    workflow.restoreWorkflowState();
    await nextTick();

    // 快照恢复出的 analysisReady 是"上次流程已就绪"，不是"本次分析刚完成"，
    // 不能借它强制展开面板——用户收起的卡片要保持收起（FR-001）。
    expect(state.analysisReady.value).toBe(true);
    expect(state.searchPanelsOpen.value).toBe(false);
    expect(state.advancedPanelsOpen.value).toBe(false);
    expect(state.screenPanelOpen.value).toBe(false);
  });
});
