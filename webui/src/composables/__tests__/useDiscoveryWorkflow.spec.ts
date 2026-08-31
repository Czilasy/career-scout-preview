// 026 B078：useDiscoveryWorkflow 以"是否进过 04 页"为流程结束唯一判据。
// - persistWorkflowState 持久化 resultsPageSeen 准确反映进没进 04 页；
//   已结束时快照不残留未完成语义（FR-001）。
// - 进 04 页（markResultsPageSeen）与结束保存（persistFinishedState）的
//   已结束事实独立持久化（localStorage），restoreWorkflowState 据此恢复。
import { ref } from "vue";
import { useDiscoveryState } from "../useDiscoveryState";
import type { DiscoveryState } from "../useDiscoveryState";
import { useDiscoveryWorkflow } from "../useDiscoveryWorkflow";
import type { WorkflowNeeds } from "../discoveryDeps";

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
});
