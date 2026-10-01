import { nextTick, ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { vi } from "vitest";
import { useDiscoveryFlowPresentation } from "../useDiscoveryFlowPresentation";
import type { FlowPresentationDeps, FlowPresentationTrack } from "../useDiscoveryFlowPresentation";
import type { TaskSnapshot as ApiTaskSnapshot } from "../../types";
import { useDiscoveryState } from "../useDiscoveryState";
import { stageStatusLabel } from "../../discovery";

// 这一行对用户说的那一句话：头部徽章与卡体都由 TaskProgress 按唯一词表算这两参，
// 呈现层负责的是把状态定稿进 snapshot——于是断言读的就是卡真正会说的那句。
function theRowSays(item?: { snapshot: ApiTaskSnapshot } | null): string {
  return stageStatusLabel(item?.snapshot.status, item?.snapshot.integrity?.conclusion);
}

function track(overrides: Record<string, unknown> = {}): FlowPresentationTrack {
  return {
    id: "track-a",
    platform: "boss",
    scrape_run_id: "scrape-a",
    screen_run_id: null,
    result_run_id: null,
    status: "running",
    stage: "scrape",
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T10:00:01Z",
    ...overrides,
  } as unknown as FlowPresentationTrack;
}

function setup(tracks: FlowPresentationTrack[], deps: FlowPresentationDeps = {}, active = "search") {
  const flow = ref<{ id: string; tracks: FlowPresentationTrack[] } | null>({ id: "flow-1", tracks });
  const activeStep = ref(active);
  const presentation = useDiscoveryFlowPresentation({
    flow,
    navigateStep: (step) => { activeStep.value = step; },
    deps,
  });
  return { flow, activeStep, presentation };
}

it("fetches real task state for each visible run and preserves scrape after AI starts", async () => {
  const calls: string[] = [];
  const { presentation } = setup(
    [
      track(),
      track({ id: "track-b", platform: "zhilian", scrape_run_id: "scrape-b", screen_run_id: "screen-b" }),
    ],
    {
      fetchTaskState: async (runId) => {
        calls.push(runId);
        return { status: "running", progress: { overall_percent: 50, stage: runId.startsWith("screen") ? "screen" : "scrape" }, logs: [] };
      },
    },
  );
  await presentation.refresh();
  await flushPromises();
  await nextTick();
  expect(calls).toEqual(["scrape-a", "scrape-b", "screen-b"]);
  expect(presentation.scrapeItems.value.map((item) => item.snapshot.progress?.overall_percent)).toEqual([50, 50]);
  expect(presentation.screenItems.value).toHaveLength(1);
});

it("keeps stable first-observed order and appends a late platform", async () => {
  const stampTime = new Date("2026-01-01T10:00:02Z").getTime();
  const { flow, presentation } = setup(
    [
      track({ id: "track-z", platform: "zhilian", created_at: "2026-01-01T10:00:02Z" }),
      track({ id: "track-b", platform: "boss", created_at: "2026-01-01T10:00:01Z" }),
    ],
    { fetchTaskState: async () => ({ status: "running", progress: {}, logs: [] }) },
  );
  await presentation.refresh();
  expect(presentation.scrapeItems.value.map((item) => item.platform)).toEqual(["boss", "zhilian"]);
  flow.value = {
    id: "flow-1",
    tracks: flow.value!.tracks.concat(
      track({ id: "track-c", platform: "boss", scrape_run_id: "scrape-c", created_at: "2026-01-01T09:00:00Z" }),
    ),
  };
  await presentation.refresh();
  await flushPromises();
  expect(presentation.scrapeItems.value.map((item) => item.platform)).toEqual(["boss", "zhilian", "boss"]);
});

it("orders each stage by its task start snapshot and keeps scrape/screen memories separate", async () => {
  const { presentation } = setup([
    track({ id: "track-b", platform: "boss", scrape_run_id: "scrape-b", screen_run_id: "screen-b", created_at: "2026-01-01T10:00:00Z" }),
    track({ id: "track-z", platform: "zhilian", scrape_run_id: "scrape-z", screen_run_id: "screen-z", created_at: "2026-01-01T10:00:00Z" }),
  ], {
    fetchTaskState: async (runId) => ({
      status: "running", progress: {}, logs: [],
      started_at: runId === "scrape-z" ? 100 : runId === "scrape-b" ? 200 : runId === "screen-z" ? 400 : 300,
    }),
  });

  await presentation.refresh();

  expect(presentation.scrapeItems.value.map((item) => item.platform)).toEqual(["zhilian", "boss"]);
  expect(presentation.screenItems.value.map((item) => item.platform)).toEqual(["boss", "zhilian"]);
});

it("keeps a failed preflight track visible without inventing a run or percentage", async () => {
  const calls: string[] = [];
  const { presentation } = setup([
    track({ id: "track-failed", platform: "zhilian", scrape_run_id: null, status: "failed", stage: "scrape", reason: "平台不可用", error_code: "platform_unavailable" }),
  ], { fetchTaskState: async (runId) => { calls.push(runId); return null; } });

  await presentation.refresh();

  expect(presentation.scrapeItems.value).toHaveLength(1);
  expect(presentation.scrapeItems.value[0]).toMatchObject({ platform: "zhilian", runId: "", kind: "scrape" });
  expect(presentation.scrapeItems.value[0]?.snapshot.progress).toEqual({});
  expect(presentation.scrapeItems.value[0]?.snapshot).toMatchObject({
    error_code: "platform_unavailable",
    reason: "平台不可用",
  });
  expect(calls).toEqual([]);
});

it("unlocks results and keeps failed-track jobs visible without a result run id", async () => {
  const { presentation } = setup([
    track({
      id: "track-failed-with-jobs", platform: "zhilian", scrape_run_id: null, screen_run_id: null,
      result_run_id: null, status: "failed", stage: "complete", jobs: [
        { job_id: "partial-job", platform: "zhilian", title: "失败轨保留岗位" },
      ], reason: "AI 筛选失败",
    }),
  ]);

  await presentation.refresh();

  expect(presentation.unlockedSteps.value).toEqual(new Set(["search", "results"]));
  expect(presentation.screenItems.value).toHaveLength(1);
  expect((presentation.screenItems.value[0]?.snapshot as ApiTaskSnapshot & { reason?: string }).reason).toBe("AI 筛选失败");
});

it("hydrates failed-track jobs from the Flow results projection when current tracks omit jobs", async () => {
  const { presentation } = setup([
    track({ id: "track-failed-projected", platform: "zhilian", scrape_run_id: null, screen_run_id: null, result_run_id: null, status: "failed", stage: "complete", reason: "AI 筛选失败" }),
  ], {
    fetchFlowResults: async () => ({
      tracks: [{ platform: "zhilian", status: "failed", result_run_id: null, jobs: [{ job_id: "projected-job", title: "投影岗位" }], dropped: [] }],
    }),
  });

  await presentation.refresh();

  expect(presentation.unlockedSteps.value).toEqual(new Set(["search", "results"]));
  expect(presentation.screenItems.value[0]?.snapshot).toMatchObject({ status: "failed", reason: "AI 筛选失败" });
});

it("clears the previous Flow presentation when the current Flow is removed", async () => {
  const { flow, presentation } = setup([track()]);
  await presentation.refresh();
  expect(presentation.scrapeItems.value).toHaveLength(1);

  flow.value = null;
  await presentation.refresh();

  expect(presentation.scrapeItems.value).toEqual([]);
  expect(presentation.screenItems.value).toEqual([]);
  expect(presentation.unlockedSteps.value).toEqual(new Set(["search"]));
});

it("unlocks and auto-advances once, then honors manual hold", async () => {
  const { flow, activeStep, presentation } = setup([track()]);
  await presentation.refresh();
  await flushPromises();
  expect(activeStep.value).toBe("search");
  flow.value = { id: "flow-1", tracks: [track({ screen_run_id: "screen-a" })] };
  await presentation.refresh();
  await flushPromises();
  expect(activeStep.value).toBe("screen");
  presentation.setManualHold(true);
  activeStep.value = "search";
  flow.value = { id: "flow-1", tracks: [track({ screen_run_id: "screen-a", result_run_id: "result-a" })] };
  await presentation.refresh();
  await flushPromises();
  expect(activeStep.value).toBe("search");
  presentation.setManualHold(false);
  expect(activeStep.value).toBe("results");
});

it("keeps search unlocked when an existing Flow already has AI or results", async () => {
  const { presentation } = setup([
    track({ screen_run_id: "screen-a", result_run_id: "result-a", status: "succeeded", stage: "complete" }),
  ]);

  await presentation.refresh();

  expect(presentation.unlockedSteps.value).toEqual(new Set(["search", "screen", "results"]));
});

it("unlocks AI screening after a scrape-only Flow reaches a terminal state", async () => {
  const { presentation } = setup([
    track({ status: "done", stage: "complete", screen_run_id: null, result_run_id: null }),
  ]);

  await presentation.refresh();

  expect(presentation.unlockedSteps.value).toEqual(new Set(["search", "screen"]));
});

it("keeps a failed AI track visible and unlocks retry without inventing results", async () => {
  const { presentation } = setup([
    track({
      status: "failed",
      stage: "ai",
      scrape_run_id: null,
      screen_run_id: null,
      result_run_id: null,
      jobs: [],
      reason: "平台登录空间暂不可用",
      error_code: "source_cdp_unavailable",
    }),
  ]);

  await presentation.refresh();

  expect(presentation.unlockedSteps.value).toEqual(new Set(["search", "screen"]));
  expect(presentation.screenItems.value).toHaveLength(1);
  expect(presentation.screenItems.value[0]?.snapshot).toMatchObject({
    status: "failed",
    reason: "平台登录空间暂不可用",
    error_code: "source_cdp_unavailable",
  });
  expect(presentation.unlockedSteps.value).not.toContain("results");
});

it("keeps a paused AI track visible and unlocks retry without inventing results", async () => {
  const { presentation } = setup([
    track({
      status: "paused",
      stage: "ai",
      scrape_run_id: null,
      screen_run_id: null,
      result_run_id: null,
      jobs: [],
      reason: "AI 筛选暂停后未完成",
      error_code: "source_cdp_unavailable",
    }),
  ]);

  await presentation.refresh();

  expect(presentation.unlockedSteps.value).toEqual(new Set(["search", "screen"]));
  expect(presentation.screenItems.value).toHaveLength(1);
  expect(presentation.screenItems.value[0]?.snapshot).toMatchObject({
    status: "paused",
    reason: "AI 筛选暂停后未完成",
    error_code: "source_cdp_unavailable",
  });
  expect(presentation.unlockedSteps.value).not.toContain("results");
});

it("does not auto-advance or notify on first hydration", async () => {
  const { flow, activeStep, presentation } = setup(
    [track({ screen_run_id: "screen-a", result_run_id: "result-a" })],
    {
      fetchTaskState: async () => ({ status: "succeeded", progress: {}, logs: [] }),
    },
    "results",
  );
  await presentation.refresh();
  await flushPromises();
  expect(activeStep.value).toBe("results");
  expect(presentation.consumeNotice()).toBeNull();
});

it("refreshes results with preserved presentation only after first hydration", async () => {
  const calls: Array<boolean | undefined> = [];
  const { flow, presentation } = setup(
    [track({ result_run_id: null })],
    {
      refreshResults: (preserve) => {
        calls.push(preserve);
        return Promise.resolve();
      },
    },
  );
  await presentation.refresh();
  await flushPromises();
  expect(calls).toEqual([]);
  flow.value = { id: "flow-1", tracks: [track({ result_run_id: "result-a", status: "succeeded" })] };
  await presentation.refresh();
  await flushPromises();
  expect(calls).toEqual([true]);
  expect(presentation.consumeNotice()?.id).toBe("flow-1:boss:result-a");
  await presentation.refresh();
  await flushPromises();
  expect(presentation.consumeNotice()).toBeNull();
});

it("does not duplicate the same result signature after polling", async () => {
  const { flow, presentation } = setup(
    [track({ result_run_id: "result-a" })],
    {
      fetchTaskState: async () => ({ status: "succeeded", progress: {}, logs: [] }),
    },
  );
  await presentation.refresh();
  await flushPromises();
  flow.value = { id: "flow-1", tracks: [track({ result_run_id: "result-a", status: "succeeded" })] };
  await presentation.refresh();
  await flushPromises();
  expect(presentation.consumeNotice()).toBeNull();
  flow.value = { id: "flow-1", tracks: [track({ result_run_id: "result-a", status: "succeeded" })] };
  await presentation.refresh();
  await flushPromises();
  expect(presentation.consumeNotice()).toBeNull();
});

it("retries a result seen by a stale refresh and emits exactly once", async () => {
  let resolveStale!: () => void;
  let refreshCalls = 0;
  const staleRefreshResults = new Promise<void>((resolve) => { resolveStale = resolve; });
  const { flow, presentation } = setup(
    [track({ result_run_id: null })],
    {
      refreshResults: () => {
        refreshCalls += 1;
        return refreshCalls === 1 ? staleRefreshResults : Promise.resolve();
      },
    },
  );

  await presentation.refresh();
  flow.value = { id: "flow-1", tracks: [track({ result_run_id: "result-a", status: "succeeded" })] };
  const staleRefresh = presentation.refresh();
  for (let i = 0; i < 8 && refreshCalls === 0; i += 1) await Promise.resolve();
  expect(refreshCalls).toBe(1);

  await presentation.refresh();
  expect(refreshCalls).toBe(2);
  expect(presentation.consumeNotice()?.id).toBe("flow-1:boss:result-a");
  expect(presentation.consumeNotice()).toBeNull();

  resolveStale();
  await staleRefresh;
  expect(presentation.consumeNotice()).toBeNull();
});

it("retries a terminal result after the first workspace refresh fails", async () => {
  vi.useFakeTimers();
  try {
    let refreshCalls = 0;
    const { flow, presentation } = setup(
      [track({ result_run_id: null })],
      {
        refreshResults: () => {
          refreshCalls += 1;
          return refreshCalls === 1
            ? Promise.reject(new Error("workspace temporarily unavailable"))
            : Promise.resolve();
        },
      },
    );
    await presentation.refresh();
    flow.value = { id: "flow-1", tracks: [track({ result_run_id: "result-a", status: "succeeded" })] };

    await expect(presentation.refresh()).resolves.toBeUndefined();
    expect(refreshCalls).toBe(1);
    expect(presentation.consumeNotice()).toBeNull();

    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(refreshCalls).toBe(2);
    expect(presentation.consumeNotice()).toMatchObject({
      id: "flow-1:boss:result-a",
      platform: "boss",
    });
    expect(presentation.consumeNotice()).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

it("cancels a pending result retry when the Flow is cleared", async () => {
  vi.useFakeTimers();
  try {
    let refreshCalls = 0;
    const { flow, presentation } = setup(
      [track({ result_run_id: null })],
      {
        refreshResults: () => {
          refreshCalls += 1;
          return Promise.reject(new Error("workspace unavailable"));
        },
      },
    );
    await presentation.refresh();
    flow.value = { id: "flow-1", tracks: [track({ result_run_id: "result-a", status: "succeeded" })] };
    await presentation.refresh();
    expect(refreshCalls).toBe(1);

    flow.value = null;
    await presentation.refresh();
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(refreshCalls).toBe(1);
    expect(presentation.consumeNotice()).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

it("caps consecutive result refresh retries until an explicit refresh", async () => {
  vi.useFakeTimers();
  try {
    let refreshCalls = 0;
    const { flow, presentation } = setup(
      [track({ result_run_id: null })],
      {
        refreshResults: () => {
          refreshCalls += 1;
          return Promise.reject(new Error("workspace unavailable"));
        },
      },
    );
    await presentation.refresh();
    flow.value = { id: "flow-1", tracks: [track({ result_run_id: "result-a", status: "succeeded" })] };
    await presentation.refresh();
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await vi.advanceTimersByTimeAsync(1000);
      await flushPromises();
    }
    expect(refreshCalls).toBe(4);
    await vi.advanceTimersByTimeAsync(3000);
    expect(refreshCalls).toBe(4);

    await presentation.refresh();
    expect(refreshCalls).toBe(5);
  } finally {
    vi.useRealTimers();
  }
});

it("notifies once for a later second-platform result run", async () => {
  const { flow, presentation } = setup(
    [track({ result_run_id: "boss-result", status: "succeeded" })],
  );

  await presentation.refresh();
  flow.value = {
    id: "flow-1",
    tracks: [
      track({ result_run_id: "boss-result", status: "succeeded" }),
      track({
        id: "track-z", platform: "zhilian", scrape_run_id: "scrape-z",
        result_run_id: "zhilian-result", status: "succeeded", stage: "complete",
      }),
    ],
  };
  await presentation.refresh();
  await flushPromises();

  expect(presentation.consumeNotice()).toMatchObject({
    id: "flow-1:zhilian:zhilian-result",
    platform: "zhilian",
  });
  expect(presentation.consumeNotice()).toBeNull();
  await presentation.refresh();
  expect(presentation.consumeNotice()).toBeNull();
});

it("refreshes once when a failed projection adds jobs without a result id", async () => {
  let projectedJobs: Array<Record<string, unknown>> = [];
  const calls: Array<boolean | undefined> = [];
  const { flow, presentation } = setup(
    [track({ id: "track-z", platform: "zhilian", scrape_run_id: null, screen_run_id: null, result_run_id: null, status: "failed", stage: "screen" })],
    {
      fetchFlowResults: async () => ({ tracks: [{ id: "track-z", platform: "zhilian", status: "failed", stage: "screen", result_run_id: null, unfinished_ai_screening: projectedJobs.length > 0, message: projectedJobs.length > 0 ? "AI 筛选未完成" : "", jobs: projectedJobs }] }),
      refreshResults: (preserve) => { calls.push(preserve); return Promise.resolve(); },
    },
  );
  await presentation.refresh();
  projectedJobs = [{ job_id: "projected-job" }];
  flow.value = { id: "flow-1", tracks: [track({ id: "track-z", platform: "zhilian", scrape_run_id: null, screen_run_id: null, result_run_id: null, status: "failed", stage: "screen" })] };
  await presentation.refresh();
  await flushPromises();
  expect(calls).toEqual([true]);
  expect(presentation.consumeNotice()).toBeNull();
  flow.value = { id: "flow-1", tracks: [track({ id: "track-z", platform: "zhilian", scrape_run_id: null, screen_run_id: null, result_run_id: null, status: "failed", stage: "screen" })] };
  await presentation.refresh();
  await flushPromises();
  expect(calls).toEqual([true]);
});

it("drops stale task-state writes after a newer refresh clears the Flow", async () => {
  let resolveTask!: (snapshot: ApiTaskSnapshot) => void;
  const pendingTask = new Promise<ApiTaskSnapshot>((resolve) => { resolveTask = resolve; });
  const { flow, presentation } = setup([
    track({ screen_run_id: "screen-a", result_run_id: "result-a" }),
  ], { fetchTaskState: async () => pendingTask });

  const staleRefresh = presentation.refresh();
  await nextTick();
  flow.value = null;
  await presentation.refresh();
  expect(presentation.scrapeItems.value).toEqual([]);
  expect(presentation.screenItems.value).toEqual([]);
  expect(presentation.unlockedSteps.value).toEqual(new Set(["search"]));

  resolveTask({ status: "succeeded", progress: { message: "旧画像任务" }, logs: [] });
  await staleRefresh;
  expect(presentation.scrapeItems.value).toEqual([]);
  expect(presentation.screenItems.value).toEqual([]);
  expect(presentation.unlockedSteps.value).toEqual(new Set(["search"]));
});

it("keeps the newest same-Flow refresh after an older task-state response arrives", async () => {
  let resolveOld!: (snapshot: ApiTaskSnapshot) => void;
  const oldTask = new Promise<ApiTaskSnapshot>((resolve) => { resolveOld = resolve; });
  let calls = 0;
  const { presentation } = setup([track()], {
    fetchTaskState: async () => {
      calls += 1;
      return calls === 1
        ? oldTask
        : { status: "running", progress: { message: "最新任务状态" }, logs: [] };
    },
  });

  const staleRefresh = presentation.refresh();
  await nextTick();
  await presentation.refresh();
  expect(presentation.scrapeItems.value[0]?.snapshot.progress?.message).toBe("最新任务状态");

  resolveOld({ status: "running", progress: { message: "旧任务状态" }, logs: [] });
  await staleRefresh;
  expect(presentation.scrapeItems.value[0]?.snapshot.progress?.message).toBe("最新任务状态");
});

it("新轮重置清空旧 Flow 可达性与人工停留态", async () => {
  const { flow, presentation } = setup([
    track({ screen_run_id: "screen-a", result_run_id: "result-a" }),
  ]);
  await presentation.refresh();
  presentation.setManualHold(true);

  presentation.resetNavigation();
  flow.value = { id: "flow-1", tracks: [track()] };
  await presentation.refresh();

  expect(presentation.unlockedSteps.value).toEqual(new Set(["search"]));
  expect(presentation.manualHold.value).toBe(false);
});

it("Flow 只通过统一导航回调请求自动推进，不直接写页面 activeStep", async () => {
  const requested: string[] = [];
  const { flow, presentation } = setup([track()], {}, "upload");
  await presentation.refresh();
  presentation.setNavigationHandler((step) => requested.push(step));

  flow.value = { id: "flow-1", tracks: [track({ screen_run_id: "screen-a" })] };
  await presentation.refresh();
  await flushPromises();

  expect(requested).toEqual(["screen"]);
});

it("在请求自动推进前先投影可达集合，连续完成 screen/results 不丢步", async () => {
  const state = useDiscoveryState({ profileId: "atomic-projection" }, () => {});
  state.workflowStateRestored.value = true;
  const flow = ref<{ id: string; tracks: FlowPresentationTrack[] }>({
    id: "flow-atomic",
    tracks: [track()],
  });
  const presentation = useDiscoveryFlowPresentation({
    flow,
    navigateStep: (step) => state.navigateStep(step, { source: "flow" }),
    // Keep this test source-compatible with the pre-fix API: the missing
    // projection callback must fail at runtime, not be hidden by a type error.
    ...( {
      projectReachableSteps: (steps: Set<string> | null) => state.setFlowReachableSteps(steps),
    } as unknown as Record<string, unknown>),
    deps: {
      fetchTaskState: async () => ({ status: "running", progress: {}, logs: [] }),
    },
  });

  await presentation.refresh();
  expect(state.activeStep.value).toBe("upload");

  flow.value = { id: "flow-atomic", tracks: [track({ screen_run_id: "screen-atomic" })] };
  await presentation.refresh();
  expect(state.activeStep.value).toBe("screen");

  flow.value = { id: "flow-atomic", tracks: [track({ screen_run_id: "screen-atomic", result_run_id: "result-atomic" })] };
  await presentation.refresh();
  expect(state.activeStep.value).toBe("results");
});

// 刚提交「全部」流程时，两条轨道都还是 queued 且 scrape_run_id IS NULL
//（store_flow_claims 只有在没有 run 身份时才允许认领）——之前这种轨道被
// buildItems 直接过滤掉，02/03 进度面板整块不渲染：用户点了开始新一轮，
// 页面上没有任何「排队中/正在准备」的可读反馈。
it("keeps queued tracks with no run id in the progress presentation instead of a blank panel", async () => {
  const { presentation } = setup([
    track({ id: "t-boss", platform: "boss", scrape_run_id: null, screen_run_id: null, status: "queued", stage: "pending" }),
    track({ id: "t-zhilian", platform: "zhilian", scrape_run_id: null, screen_run_id: null, status: "queued", stage: "pending" }),
  ], { fetchTaskState: async () => null });

  await presentation.refresh();

  expect(presentation.scrapeItems.value.map((item) => item.platform)).toEqual(["boss", "zhilian"]);
  expect(presentation.scrapeItems.value.map((item) => item.status)).toEqual(["queued", "queued"]);
  expect(presentation.scrapeItems.value.map((item) => item.runId)).toEqual(["", ""]);
  expect(presentation.scrapeItems.value[0]?.snapshot).toMatchObject({ status: "queued" });
  // 排队阶段还没抓到任何岗位：不得凭空造出百分比。
  expect(presentation.scrapeItems.value[0]?.snapshot.progress).toEqual({});
  expect(presentation.screenItems.value.map((item) => item.status)).toEqual(["queued", "queued"]);
});

// 后端把可变 stage 只当线索、把 durable run id 当权威（webui/flow_task_coordinator.py
// 「durable IDs outrank the mutable worker stage」；flow_service.py 的
// mark_scrape_complete 先绑 screen_run_id 再写 stage='ai'；store_flow_state.py 取消时
// 用 COALESCE(stage,'scrape') 不推进阶段）。所以「stage 还是 scrape、screen_run_id 已
// 绑定、整条线被中断」是真实存在的一态：轨道状态必须归真正停摆的 AI 段。
it("hands the Track state to the AI stage once its run is bound even while the stage still reads scrape", async () => {
  const { presentation } = setup([
    track({
      id: "track-z", platform: "zhilian", status: "interrupted", stage: "scrape",
      scrape_run_id: "scrape-z", screen_run_id: "screen-z",
    }),
  ], {
    fetchTaskState: async (runId) => (runId === "scrape-z"
      ? { status: "succeeded", progress: { overall_percent: 100, current: 377, total: 377 }, logs: [], scraped_count: 377 }
      : { status: "interrupted", progress: {}, logs: [] }),
  });

  await presentation.refresh();

  // 抓取段已经自己跑完：这张卡只说本段那一句「已完成」，不替整条线背「已中断」；
  // 中断那句归真正停摆的 AI 段那张卡。判定看得见的位置就是两张卡各自定稿的快照。
  expect(presentation.scrapeItems.value[0]).toMatchObject({ kind: "scrape" });
  expect(presentation.scrapeItems.value[0]?.snapshot.status).toBe("succeeded");
  expect(theRowSays(presentation.scrapeItems.value[0])).toBe("已完成");
  expect(presentation.screenItems.value[0]).toMatchObject({ kind: "screen" });
  expect(presentation.screenItems.value[0]?.snapshot.status).toBe("interrupted");
  expect(theRowSays(presentation.screenItems.value[0])).toBe("已中断");
});

// 交接窗口的真实一态：webui/runners/pipeline_task.py 调 mark_scrape_complete 时不带
// screen_run_id（绑定发生在 begin_ai），于是 stage 已进 AI、screen_run_id 还是空、
// 整条线还在 running、AI 段一条证据都还没有。此前这种卡在候选过滤处被直接丢掉，
// 03 整块不渲染，而按钮仍按轨道态给暂停/停止——用户点的是一条没有任何现场的线。
it("collects the current AI stage card during the hand-off window before its run is bound", async () => {
  const { presentation } = setup([
    track({
      id: "track-z", platform: "zhilian", status: "running", stage: "ai",
      scrape_run_id: "scrape-z", screen_run_id: null,
    }),
  ], {
    fetchTaskState: async (runId) => (runId === "scrape-z"
      ? { status: "succeeded", progress: { overall_percent: 100, current: 7, total: 7 }, logs: [], scraped_count: 7 }
      : null),
  });

  await presentation.refresh();

  // 本段就是当前段、整条线还在活动态 → 这张卡必须收进 03。
  expect(presentation.screenItems.value).toHaveLength(1);
  expect(presentation.screenItems.value[0]).toMatchObject({
    kind: "screen", runId: "", status: "running",
  });
  // 这条线当前的段就是 AI 段：轨道状态落进这张卡自己定稿的快照。
  expect(presentation.screenItems.value[0]?.snapshot.status).toBe("running");
  // 抓取段自己已经跑完，照旧只说自己那一句「已完成」，不替 AI 段背状态。
  expect(presentation.scrapeItems.value[0]).toMatchObject({ kind: "scrape" });
  expect(presentation.scrapeItems.value[0]?.snapshot.status).toBe("succeeded");
});

// 交接已发生、这一段又不是当前段、还取不到自己的证据：这张卡对整条线的停摆
// 一无所知，绝不许把轨道问题态抄进快照（fallback 快照抄的就是轨道状态）。
// 「没有本段证据」有两种，中性口径只有一种配得上：这张卡有 scrape_run_id，
// 说明本段真的跑过，只是这一轮读不到状态——写成「等待开始」就把跑过的段说成
// 还没开始（组件测 ParallelPlatformProgress.spec 同一场景）。这里换成读不到状态
// 的兜底口径，卡片两头同说「状态更新中」，仍然不抄轨道问题态。
it("keeps a handed-off stage card that already started but reads no state off the neutral wording", async () => {
  const { presentation } = setup([
    track({
      id: "track-z", platform: "zhilian", status: "stopped", stage: "ai",
      scrape_run_id: "scrape-z", screen_run_id: "screen-z", reason: "用户已取消",
      error_code: "user_cancelled",
    }),
  ], {
    fetchTaskState: async (runId) => (runId === "screen-z"
      ? { status: "stopped", progress: {}, logs: [] }
      : null),
  });

  await presentation.refresh();

  const scrape = presentation.scrapeItems.value[0];
  expect(scrape).toMatchObject({ kind: "scrape", runId: "scrape-z" });
  const blind = scrape?.snapshot as ApiTaskSnapshot & { reason?: string; error_code?: string } | undefined;
  expect(scrape?.snapshot.status).toBe("unknown");
  expect(blind?.reason).toBeUndefined();
  expect(blind?.error).toBeUndefined();
  expect(blind?.error_code).toBeUndefined();
  expect(scrape?.snapshot.progress).toEqual({});
  expect(presentation.screenItems.value[0]).toMatchObject({ kind: "screen" });
  // 这条线自己停在抓取段之后的 AI 段：卡说的是这条线那段真正说的话（用户取消＝已停止）。
  expect(presentation.screenItems.value[0]?.snapshot.status).toBe("stopped");
});

// 另一种无证据：这一段连 run 身份都还没有（刚提交的一轮整条线在排队），
// 说「等待开始」才是事实。
it("keeps a stage card that never got a run on the neutral queued wording", async () => {
  const { presentation } = setup([
    track({
      id: "track-z", platform: "zhilian", status: "queued", stage: "pending",
      scrape_run_id: null, screen_run_id: null,
    }),
  ], { fetchTaskState: async () => null });

  await presentation.refresh();

  expect(presentation.screenItems.value[0]).toMatchObject({
    kind: "screen", runId: "",
  });
  expect(presentation.screenItems.value[0]?.snapshot.status).toBe("queued");
});

// 单条 run 的状态读取会真实抛错（webui/task_state_api.py 对 run 不存在或画像不符
// 返回 404，webui/src/api.ts 对非 2xx 抛 ApiError）。之前没有捕获：异常冒泡到
// useDiscoveryFlowCoordinator 的 .catch(() => {}) 被吞掉，items 只填了一半、
// screenItems 与解锁/可达性投影都不再执行，03/04 长期锁死且用户无提示。
it("degrades only the one card whose task-state read throws and still finishes the refresh", async () => {
  const stored = { status: "running", progress: { overall_percent: 30, current: 3, total: 10 }, logs: [] };
  const { presentation } = setup([
    track({ id: "track-b", platform: "boss", scrape_run_id: "scrape-b", screen_run_id: null, status: "running", stage: "scrape", "scrape-b:snapshot": stored }),
    track({ id: "track-z", platform: "zhilian", scrape_run_id: "scrape-z", screen_run_id: "screen-z", status: "running", stage: "ai" }),
  ], {
    fetchTaskState: async (runId) => {
      if (runId === "scrape-b") throw new Error("run_not_found");
      return { status: "running", progress: { overall_percent: 60 }, logs: [] };
    },
  });

  await expect(presentation.refresh()).resolves.toBeUndefined();

  expect(presentation.scrapeItems.value).toHaveLength(2);
  expect(presentation.scrapeItems.value.find((item) => item.platform === "boss")?.snapshot)
    .toMatchObject({ status: "running", progress: { overall_percent: 30 } });
  expect(presentation.screenItems.value).toHaveLength(1);
  expect(presentation.unlockedSteps.value).toContain("screen");
});

it("does not invent progress items for tracks that never entered the queue", async () => {
  const { presentation } = setup([
    track({ id: "t-done", platform: "boss", scrape_run_id: null, screen_run_id: null, status: "done", stage: "complete" }),
  ]);

  await presentation.refresh();

  expect(presentation.scrapeItems.value).toEqual([]);
  expect(presentation.screenItems.value).toEqual([]);
});

// 一张阶段卡的状态标签只说这一段的事：轨道级状态归「线当前所在的那一段」，
// 已经自己跑完的段不再替整条线背「已中断」。这个判定看得见的位置就是两张卡
// 各自定稿的快照——头部徽章与卡体都由 TaskProgress 按唯一词表念那一份。
it("hands the Track state only to the card of the stage the Track is currently at", async () => {
  const { presentation } = setup([
    track({
      id: "track-z", platform: "zhilian", status: "interrupted", stage: "ai",
      scrape_run_id: "scrape-z", screen_run_id: "screen-z",
    }),
  ], {
    fetchTaskState: async (runId) => (runId === "scrape-z"
      ? { status: "succeeded", progress: { overall_percent: 100, current: 377, total: 377 }, logs: [] }
      : { status: "interrupted", progress: {}, logs: [] }),
  });

  await presentation.refresh();

  expect(theRowSays(presentation.scrapeItems.value[0])).toBe("已完成");
  expect(theRowSays(presentation.screenItems.value[0])).toBe("已中断");
});

it("keeps the in-flight stage card carrying the Track state", async () => {
  const { presentation } = setup([
    track({ id: "track-z", platform: "zhilian", status: "running", stage: "scrape", scrape_run_id: "scrape-z" }),
  ], {
    fetchTaskState: async () => ({ status: "running", progress: { overall_percent: 40 }, logs: [] }),
  });

  await presentation.refresh();

  expect(presentation.scrapeItems.value[0]).toMatchObject({ kind: "scrape", status: "running" });
  expect(presentation.scrapeItems.value[0]?.snapshot.status).toBe("running");
  expect(theRowSays(presentation.scrapeItems.value[0])).toBe("运行中");
});

// 抓取段自己失败了、线已经停在抓取段：这一张卡既代表这一段也代表这条线，
// 头部继续走轨道状态；而抓取段已完成、线卡在 AI 段的暂停态时，抓取卡只说完成。
it("keeps a failed scrape stage carrying the Track state and reassigns it after the hand-off", async () => {
  const stuck = setup([
    track({
      id: "track-z", platform: "zhilian", status: "failed", stage: "scrape",
      scrape_run_id: "scrape-z", screen_run_id: null,
    }),
  ], { fetchTaskState: async () => ({ status: "failed", progress: {}, logs: [] }) });
  await stuck.presentation.refresh();
  expect(stuck.presentation.scrapeItems.value[0]?.snapshot.status).toBe("failed");
  expect(theRowSays(stuck.presentation.scrapeItems.value[0])).toBe("执行失败");

  const handedOff = setup([
    track({
      id: "track-z", platform: "zhilian", status: "paused", stage: "ai",
      scrape_run_id: "scrape-z", screen_run_id: "screen-z",
    }),
  ], {
    fetchTaskState: async (runId) => (runId === "scrape-z"
      ? { status: "succeeded", progress: { overall_percent: 100 }, logs: [] }
      : { status: "paused", progress: {}, logs: [] }),
  });
  await handedOff.presentation.refresh();
  expect(theRowSays(handedOff.presentation.scrapeItems.value[0])).toBe("已完成");
  expect(theRowSays(handedOff.presentation.screenItems.value[0])).toBe("已暂停");
});

// D-03 结构收敛：动作派生按轨道各算一份。一平台一行的动作、结束并保存与终止显隐
// 都由这里下发，轨道行组件只渲染；两条线速度不同时必须各说各的出口。
it("derives one action bar per Track from that Track's own stage facts", async () => {
  const { presentation } = setup([
    track({ id: "track-b", platform: "boss", scrape_run_id: "scrape-b", status: "running", stage: "scrape" }),
    track({ id: "track-z", platform: "zhilian", scrape_run_id: "scrape-z", status: "paused", stage: "scrape" }),
  ], { fetchTaskState: async (runId) => ({
    status: runId === "scrape-b" ? "running" : "paused", progress: {}, logs: [],
  }) });

  await presentation.refresh();

  expect(presentation.scrapeItems.value.map((entry) => entry.action)).toEqual([
    { kind: "pause-scrape", label: "暂停" },
    { kind: "continue-scrape", label: "继续" },
  ]);
  expect(presentation.scrapeItems.value.map((entry) => entry.showFinishSave)).toEqual([true, true]);
  expect(presentation.scrapeItems.value.map((entry) => entry.showCancel)).toEqual([true, true]);
  expect(presentation.scrapeItems.value[1]?.cancelLabel).toBe("终止本轨");
});

// 03 页的轨道行说 AI 的话；抓取段与筛选段的出口不能长成同一副样子。
it("derives AI screening wording for the screen stage rows", async () => {
  const { presentation } = setup([
    track({ id: "track-b", platform: "boss", scrape_run_id: "scrape-b", screen_run_id: "screen-b", status: "running", stage: "ai" }),
    track({ id: "track-z", platform: "zhilian", scrape_run_id: "scrape-z", screen_run_id: "screen-z", status: "paused", stage: "ai" }),
  ], { fetchTaskState: async (runId) => ({
    status: String(runId).endsWith("screen-b") ? "running" : "paused", progress: {}, logs: [],
  }) });

  await presentation.refresh();

  expect(presentation.screenItems.value.map((entry) => entry.platform)).toEqual(["boss", "zhilian"]);
  expect(presentation.screenItems.value.map((entry) => entry.action)).toEqual([
    { kind: "pause", label: "暂停筛选" },
    { kind: "continue", label: "继续 AI 筛选" },
  ]);
});

// 状态词表：中断没有活体 worker，只能开新一轮；轨道行上不得再出现「继续」。
it("gives an interrupted Track no continuation on either stage", async () => {
  const { presentation } = setup([
    track({ id: "track-b", platform: "boss", scrape_run_id: "scrape-b", screen_run_id: null, status: "interrupted", stage: "scrape" }),
    track({ id: "track-z", platform: "zhilian", scrape_run_id: "scrape-z", screen_run_id: "screen-z", status: "interrupted", stage: "ai" }),
  ], { fetchTaskState: async () => ({ status: "interrupted", progress: {}, logs: [] }) });

  await presentation.refresh();

  expect(presentation.scrapeItems.value[0]?.action).toEqual({ kind: "none" });
  // 抓取段中断仍留着「结束并保存」这条收口出口，与单平台同一条路径。
  expect(presentation.scrapeItems.value[0]?.showFinishSave).toBe(true);
  expect(presentation.scrapeItems.value[0]?.showCancel).toBe(false);
  expect(presentation.screenItems.value[0]?.action.kind).not.toBe("continue");
});

// 终态轨道没有任何出口：不给暂停、不给终止，也不给结束并保存。
it("leaves a terminal Track row without any action", async () => {
  const { presentation } = setup([
    track({ id: "track-b", platform: "boss", scrape_run_id: "scrape-b", status: "stopped", stage: "scrape" }),
  ], { fetchTaskState: async () => ({ status: "stopped", progress: {}, logs: [] }) });

  await presentation.refresh();

  expect(presentation.scrapeItems.value[0]?.action.kind).toBe("none");
  expect(presentation.scrapeItems.value[0]?.showFinishSave).toBe(false);
  expect(presentation.scrapeItems.value[0]?.showCancel).toBe(false);
});

// 轨道级「结束并保存」把这一条线自己的 run 交给既有 run 级收尾路径：
// AI 段用自己的 screen_run_id；交接窗口里 AI 段还没有 run 身份时用整条线的 scrape_run_id。
it("hands each row the run id its own stage closes out", async () => {
  const { presentation } = setup([
    track({
      id: "track-b", platform: "boss", scrape_run_id: "scrape-b", screen_run_id: "screen-b",
      status: "running", stage: "ai",
    }),
    track({
      id: "track-z", platform: "zhilian", scrape_run_id: "scrape-z", screen_run_id: null,
      status: "running", stage: "ai",
    }),
  ], { fetchTaskState: async () => ({ status: "running", progress: {}, logs: [] }) });

  await presentation.refresh();

  const bossScreen = presentation.screenItems.value.find((entry) => entry.platform === "boss");
  expect(bossScreen?.finishRunId).toBe("screen-b");
  expect(bossScreen?.finishTestId).toBe("parallel-track-boss-finish-save");
  expect(bossScreen?.cancelTestId).toBe("parallel-track-boss-cancel");
  expect(presentation.screenItems.value.find((entry) => entry.platform === "zhilian")?.finishRunId).toBe("scrape-z");
  expect(presentation.scrapeItems.value.find((entry) => entry.platform === "boss")?.finishRunId).toBe("scrape-b");
});

// 阶段卡状态口径从组件收回呈现层：头部徽章、卡体与轨道行拿到同一份定稿快照，
// 同一张卡不许两头各说一句（原先由 ParallelPlatformProgress 再压一遍，与呈现层重复派生）。
it.each([
  ["queued", "等待开始"],
  ["running", "运行中"],
  ["paused", "已暂停"],
  ["pausing", "正在暂停"],
  ["interrupted", "已中断"],
  ["failed", "执行失败"],
  ["cancelled", "已停止"],
  ["stopped", "已停止"],
  ["partial", "完成，但有待确认"],
  ["completed_with_pending", "完成，但有待确认"],
  ["succeeded", "已完成"],
  ["done", "已完成"],
  ["unavailable", "暂不可用"],
])("resolves one %s wording for the row header and the card body", async (status, label) => {
  const { presentation } = setup([
    track({ id: "track-b", platform: "boss", scrape_run_id: "scrape-b", status, stage: "scrape" }),
  ], { fetchTaskState: async () => ({ status, progress: {}, logs: [] }) });

  await presentation.refresh();

  const entry = presentation.scrapeItems.value[0];
  expect(entry?.snapshot.status).toBe(status);
  expect(theRowSays(entry)).toBe(label);
});

// 轨道已经停止、本段快照还停在「正在暂停」：以线为准把状态压进这一段，
// 头部与卡体同说「已停止」，这一层不再由组件补第二遍。
it("pushes a stopped Track down onto a stage still pausing", async () => {
  const { presentation } = setup([
    track({ id: "track-b", platform: "boss", scrape_run_id: "scrape-b", status: "stopped", stage: "scrape" }),
  ], { fetchTaskState: async () => ({ status: "pausing", progress: {}, logs: [] }) });

  await presentation.refresh();

  expect(presentation.scrapeItems.value[0]?.snapshot.status).toBe("stopped");
  expect(theRowSays(presentation.scrapeItems.value[0])).toBe("已停止");
});

// 抓取段自己已完成、整条线还在跑：这张卡只说自己那一段（头部与卡体都是「已完成」），
// 动作仍按轨道状态给暂停——一条线只有一个当前阶段。
it("freezes a finished scrape stage while its Track is still running", async () => {
  const { presentation } = setup([
    track({
      id: "track-z", platform: "zhilian", scrape_run_id: "scrape-z", screen_run_id: "screen-z",
      status: "running", stage: "ai",
    }),
  ], {
    fetchTaskState: async (runId) => (runId === "scrape-z"
      ? { status: "succeeded", progress: { overall_percent: 100, current: 7, total: 7 }, logs: [] }
      : { status: "running", progress: {}, logs: [] }),
  });

  await presentation.refresh();

  expect(presentation.scrapeItems.value[0]?.snapshot.status).toBe("succeeded");
  expect(theRowSays(presentation.scrapeItems.value[0])).toBe("已完成");
  expect(presentation.screenItems.value[0]?.action).toEqual({ kind: "pause", label: "暂停筛选" });
});

// 真实缺陷：智联轨道 interrupted、阶段在 AI；抓取 run 早在重启前就带白箱「完整成功」。
// 轨道问题态只归中断发生的那一段，别的段不背这个锅，头部与卡体仍须同说一句。
it("routes the Track-level 已中断 badge to the stage the interruption actually hit", async () => {
  const { presentation } = setup([
    track({
      id: "track-z", platform: "zhilian", scrape_run_id: "scrape-z", screen_run_id: "screen-z",
      status: "interrupted", stage: "ai",
    }),
  ], {
    fetchTaskState: async (runId) => (runId === "scrape-z"
      ? {
        status: "succeeded", progress: { overall_percent: 100, current: 377, total: 377 }, logs: [],
        scraped_count: 377, source_total: 377, integrity: { conclusion: "succeeded", label: "完整成功" },
      }
      : { status: "interrupted", progress: {}, logs: [] }),
  });

  await presentation.refresh();

  expect(theRowSays(presentation.scrapeItems.value[0])).toBe("完整成功");
  expect(theRowSays(presentation.screenItems.value[0])).toBe("已中断");
  // 中断不给继续：这一条线不再有注定 503 的继续，但保存与终止两条出口照旧留着。
  expect(presentation.screenItems.value[0]?.action).toEqual({ kind: "none" });
  expect(presentation.screenItems.value[0]?.showCancel).toBe(true);
  expect(presentation.screenItems.value[0]?.showFinishSave).toBe(true);
});

// 交接已发生、本段跑过却读不到状态：说「等待开始」会把跑过的段说成没跑过，
// 头部与卡体同说「状态更新中」，也不替整条线背「已中断」。
it("keeps a handed-off stage card that has a run but no readable state on the 状态更新中 fallback", async () => {
  const { presentation } = setup([
    track({
      id: "track-z", platform: "zhilian", scrape_run_id: "scrape-z", screen_run_id: "screen-z",
      status: "interrupted", stage: "ai",
    }),
  ], { fetchTaskState: async (runId) => (runId === "screen-z" ? { status: "interrupted", progress: {}, logs: [] } : null) });

  await presentation.refresh();

  expect(presentation.scrapeItems.value[0]?.snapshot.status).toBe("unknown");
  expect(theRowSays(presentation.scrapeItems.value[0])).toBe("状态更新中");
});

// 白箱 unverifiable 被后端公开成 completed_with_pending：两处必须同一句，
// 不许头部把无法确认报成「完成，但有待确认」。
it("says one single conclusion on a card whose whitebox verdict is unverifiable", async () => {
  const { presentation } = setup([
    track({ id: "track-b", platform: "boss", scrape_run_id: "scrape-b", status: "completed_with_pending", stage: "scrape" }),
  ], {
    fetchTaskState: async () => ({
      status: "completed_with_pending", progress: { overall_percent: 100 }, logs: [],
      integrity: { conclusion: "unverifiable", label: "无法确认", primary_reason: "证据不足" },
    }),
  });

  await presentation.refresh();

  expect(theRowSays(presentation.scrapeItems.value[0])).toBe("无法确认是否完成");
});

// SPEC 046 V2 FR-011 / contracts/flow-presentation.md 第 3 节第 1 条：
// 同一 Flow 的解锁集合只增不减，投影暂时缺位（重新水合、轮询间隙）也不能把已解锁页
// 锁回去——可达性的对外出口只有页面守卫的 enabledSteps 一处。
it("同一 Flow 重新水合与投影回退都不回退已解锁集合", async () => {
  const state = useDiscoveryState({ profileId: "presentation-monotonic" }, () => {});
  const published: Array<Set<string> | null> = [];
  const flow = ref<{ id: string; tracks: FlowPresentationTrack[] } | null>({
    id: "flow-monotonic",
    tracks: [track({ screen_run_id: "screen-m", result_run_id: "result-m" })],
  });
  const presentation = useDiscoveryFlowPresentation({
    flow,
    navigateStep: (step) => state.navigateStep(step, { source: "flow" }),
    projectReachableSteps: (steps, flowId) => {
      published.push(steps);
      state.setFlowReachableSteps(steps, flowId);
    },
    deps: { fetchTaskState: async () => ({ status: "running", progress: {}, logs: [] }) },
  });

  await presentation.refresh();
  expect(presentation.unlockedSteps.value).toEqual(new Set(["search", "screen", "results"]));
  expect(state.enabledSteps.value).toEqual(["upload", "search", "screen", "results"]);

  // 轮询间隙：同一条 Flow 的下一次快照暂时缺字段，解锁集合仍不得回退、也不得发布 null。
  flow.value = { id: "flow-monotonic", tracks: [track()] };
  await presentation.refresh();
  expect(presentation.unlockedSteps.value).toEqual(new Set(["search", "screen", "results"]));
  expect(published.filter((entry) => entry === null)).toHaveLength(0);

  // 守卫侧：投影给回更窄一份（重新水合的基线）时，已解锁的 03/04 仍可进入。
  state.setFlowReachableSteps(new Set(["search"]), "flow-monotonic");
  expect(state.enabledSteps.value).toEqual(["upload", "search", "screen", "results"]);

  // 换到另一条 Flow：上一轮的入口不带给这一轮。
  state.setFlowReachableSteps(new Set(["search"]), "flow-next-round");
  expect(state.enabledSteps.value).toEqual(["upload", "search"]);
});

// 项目规则：没有实际调用方就不写「预留」字段。轨道行项目上的 carriesLineState 与
// statusLabel 从来没有产品消费方（头部徽章与卡体的那句话由 TaskProgress 自己按
// 唯一词表算），只有测试夹具在喂值。删掉它们之后，判定仍然要从看得见的输出里验出来：
// 「这张卡承不承担整条线的状态」体现在它定稿的 snapshot.status 上。
it("publishes only the facts a Track row renders, without reserved fields", async () => {
  const { presentation } = setup([
    track({ id: "track-a", platform: "boss", status: "running", stage: "scrape" }),
  ], {
    fetchTaskState: async () => ({ status: "running", progress: { overall_percent: 30 }, logs: [] }),
  });

  await presentation.refresh();

  const item = presentation.scrapeItems.value[0]!;
  expect(Object.prototype.hasOwnProperty.call(item, "carriesLineState")).toBe(false);
  expect(Object.prototype.hasOwnProperty.call(item, "statusLabel")).toBe(false);
  // 正向：承载线状态的判定照旧落在卡自己那一份定稿快照上，TaskProgress 据此说「运行中」。
  expect(item.snapshot.status).toBe("running");
  expect(item.action.kind).toBe("pause-scrape");
});

// 046 D-08 的上半段：04 能不能进，与「这条线到底有没有结果」是同一份事实。
// 失败但已持久化部分岗位的那条线算已经有结果可见（contracts/flow-presentation.md 第 6 节）。
it("counts a failed Track's persisted jobs as a delivered result for the same predicate", async () => {
  const { presentation } = setup([
    track({
      id: "track-a", platform: "boss", status: "failed", stage: "ai",
      screen_run_id: "screen-a", result_run_id: null,
    }),
    track({
      id: "track-b", platform: "zhilian", status: "running", stage: "scrape",
      scrape_run_id: "scrape-b", screen_run_id: null, result_run_id: null,
    }),
  ], {
    fetchTaskState: async () => null,
    fetchFlowResults: async () => ({
      tracks: [{
        id: "track-a", platform: "boss", status: "failed", stage: "ai", result_run_id: null,
        jobs: [{ job_id: "job-1" }],
      }],
    }),
  });

  await presentation.refresh();

  expect(presentation.unlockedSteps.value.has("results")).toBe(true);
  // 同一份投影外抛给页面层：说明文案读的就是这一份，不再自己数 result_run_id。
  expect(presentation.flowTracks.value.some((entry) => entry.platform === "boss"
    && Array.isArray((entry as Record<string, unknown>).jobs))).toBe(true);
});
