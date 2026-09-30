import { nextTick, ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { vi } from "vitest";
import { useDiscoveryFlowPresentation } from "../useDiscoveryFlowPresentation";
import type { FlowPresentationDeps, FlowPresentationTrack } from "../useDiscoveryFlowPresentation";
import type { TaskSnapshot as ApiTaskSnapshot } from "../../types";
import { useDiscoveryState } from "../useDiscoveryState";

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

  expect(presentation.scrapeItems.value[0]).toMatchObject({ kind: "scrape", carriesLineState: false });
  expect(presentation.screenItems.value[0]).toMatchObject({ kind: "screen", carriesLineState: true });
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
    kind: "screen", runId: "", status: "running", carriesLineState: true,
  });
  // 抓取段自己已经跑完，照旧只说自己那一句「已完成」，不替 AI 段背状态。
  expect(presentation.scrapeItems.value[0]).toMatchObject({ kind: "scrape", carriesLineState: false });
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
  expect(scrape).toMatchObject({ kind: "scrape", carriesLineState: false, runId: "scrape-z" });
  const blind = scrape?.snapshot as ApiTaskSnapshot & { reason?: string; error_code?: string } | undefined;
  expect(scrape?.snapshot.status).toBe("unknown");
  expect(blind?.reason).toBeUndefined();
  expect(blind?.error).toBeUndefined();
  expect(blind?.error_code).toBeUndefined();
  expect(scrape?.snapshot.progress).toEqual({});
  expect(presentation.screenItems.value[0]).toMatchObject({ kind: "screen", carriesLineState: true });
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
    kind: "screen", runId: "", carriesLineState: false,
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
// 已经自己跑完的段不再替整条线背「已中断」。carriesLineState 是呈现层做出的
// 唯一判定，组件据此决定头部徽章走轨道状态还是阶段快照状态。
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

  expect(presentation.scrapeItems.value[0]).toMatchObject({ kind: "scrape", carriesLineState: false });
  expect(presentation.screenItems.value[0]).toMatchObject({ kind: "screen", carriesLineState: true });
});

it("keeps the in-flight stage card carrying the Track state", async () => {
  const { presentation } = setup([
    track({ id: "track-z", platform: "zhilian", status: "running", stage: "scrape", scrape_run_id: "scrape-z" }),
  ], {
    fetchTaskState: async () => ({ status: "running", progress: { overall_percent: 40 }, logs: [] }),
  });

  await presentation.refresh();

  expect(presentation.scrapeItems.value[0]).toMatchObject({ kind: "scrape", status: "running", carriesLineState: true });
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
  expect(stuck.presentation.scrapeItems.value[0]).toMatchObject({ kind: "scrape", carriesLineState: true });

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
  expect(handedOff.presentation.scrapeItems.value[0]).toMatchObject({ kind: "scrape", carriesLineState: false });
  expect(handedOff.presentation.screenItems.value[0]).toMatchObject({ kind: "screen", carriesLineState: true });
});
