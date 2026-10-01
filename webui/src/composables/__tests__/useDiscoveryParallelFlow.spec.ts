import {
  useDiscoveryParallelFlow,
  TRACK_ACTION_OPERATIONS,
  type TrackActionKind,
} from "../useDiscoveryParallelFlow";
import { ApiError } from "../../api";
import { MAPPER_VERSION, resolveMappedValues } from "../../parallelFilterMapping";
import type { ConditionSnapshotV2 } from "../../types";
import { ref } from "vue";

describe("useDiscoveryParallelFlow", () => {
  it("keeps platform final drafts independent and maps unified fields separately", () => {
    const flow = useDiscoveryParallelFlow({ profileId: "profile-1" });
    flow.setPlatformFilters("boss", { salary: ["406"] });
    flow.setPlatformFilters("zhilian", { salary: ["807"] });

    expect(flow.platformValues.boss.salary).toEqual(["406"]);
    expect(flow.platformValues.zhilian.salary).toEqual(["807"]);
    expect(flow.unifiedValues.salary).toEqual([]);
    expect(flow.setUnifiedFilters).toBeTypeOf("function");
  });

  it("starts one Flow for all and polls both tracks without collapsing states", async () => {
    const calls: Array<{ url: string; options?: Record<string, unknown> }> = [];
    let current = {
      id: "flow-1",
      profile_id: "profile-1",
      selection: "all" as const,
      tracks: [
        { id: "b", flow_id: "flow-1", platform: "boss" as const, status: "running", stage: "scrape" },
        { id: "z", flow_id: "flow-1", platform: "zhilian" as const, status: "paused", stage: "scrape" },
      ],
    };
    const request = async <T>(url: string, options?: Record<string, unknown>): Promise<T> => {
      calls.push({ url, options });
      if (url === "/api/flows") return { flow: current } as T;
      return { flow: current } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-1", request, pollIntervalMs: 10000 });
    state.setPlatformFilters("boss", { salary: [] });
    state.setPlatformFilters("zhilian", { salary: [] });
    await state.start("all", "start-1");

    expect(calls[0].url).toBe("/api/flows");
    expect((calls[0].options?.json as Record<string, unknown>).selection).toBe("all");
    expect(state.tracks.value.boss?.status).toBe("running");
    expect(state.tracks.value.zhilian?.status).toBe("paused");
    expect(state.hasUnfinishedRound.value).toBe(true);
    expect(state.canResetNewRound.value).toBe(false);
    state.clearPolling();
  });

  // SPEC 046 FR-015 + 状态词表：暂停的 worker 还在等处理，本轮未结束、锁住开新轮
  // （出路是就地继续，或「结束并保存 / 放弃本轮」收口后自然解锁）；
  // 服务重启打断的已中断轮没有活体 worker，唯一出路就是开新一轮，必须放行 reset。
  it("locks a new round while paused yet allows reset for restart-interrupted Tracks", () => {
    const state = useDiscoveryParallelFlow({ profileId: "profile-resettable-flow" });
    state.restore({
      id: "flow-resettable",
      profile_id: "profile-resettable-flow",
      selection: "all",
      status: "paused",
      tracks: [{
        id: "b", flow_id: "flow-resettable", platform: "boss" as const,
        status: "paused", stage: "ai", screen_run_id: "screen-paused",
      }],
    });
    expect(state.hasUnfinishedRound.value).toBe(true);
    expect(state.canStartNewRound.value).toBe(false);
    expect(state.canResetNewRound.value).toBe(false);
    expect(state.newRoundBlockReason.value).toBe("任务已暂停，平台已锁定");

    state.restore({
      id: "flow-resettable-interrupted",
      profile_id: "profile-resettable-flow",
      selection: "all",
      status: "interrupted",
      tracks: [{
        id: "b", flow_id: "flow-resettable-interrupted", platform: "boss" as const,
        status: "interrupted", stage: "ai", screen_run_id: "screen-interrupted",
      }],
    });
    expect(state.canResetNewRound.value).toBe(true);
    expect(state.newRoundBlockReason.value).toBe("");
    state.clearPolling();
  });

  // FR-015 挡新轮的原因分层沿用树干 scopeLockReason 口径（useDiscoveryState 821-829）：
  // 有排队/运行在说「进行中」，只剩暂停才说「已暂停」，不新写第三套文案。
  it("names the locking reason with the trunk scopeLockReason wording", () => {
    const running = useDiscoveryParallelFlow({ profileId: "profile-fr015-reason" });
    running.restore({
      id: "flow-fr015-running",
      profile_id: "profile-fr015-reason",
      selection: "all",
      status: "running",
      tracks: [{
        id: "b", flow_id: "flow-fr015-running", platform: "boss" as const,
        status: "paused", stage: "ai", screen_run_id: "screen-paused",
      }],
    });
    expect(running.canStartNewRound.value).toBe(false);
    expect(running.newRoundBlockReason.value).toBe("任务进行中，平台已锁定");
    running.clearPolling();

    const queued = useDiscoveryParallelFlow({ profileId: "profile-fr015-reason" });
    queued.restore({
      id: "flow-fr015-queued",
      profile_id: "profile-fr015-reason",
      selection: "all",
      status: "queued",
      tracks: [{
        id: "b", flow_id: "flow-fr015-queued", platform: "boss" as const,
        status: "queued", stage: "pending",
      }],
    });
    expect(queued.newRoundBlockReason.value).toBe("任务进行中，平台已锁定");
    queued.clearPolling();

    const finished = useDiscoveryParallelFlow({ profileId: "profile-fr015-reason" });
    finished.restore({
      id: "flow-fr015-finished",
      profile_id: "profile-fr015-reason",
      selection: "all",
      status: "succeeded",
      tracks: [{
        id: "b", flow_id: "flow-fr015-finished", platform: "boss" as const,
        status: "succeeded", stage: "complete", result_run_id: "r-finished",
      }],
    });
    expect(finished.canStartNewRound.value).toBe(true);
    expect(finished.canResetNewRound.value).toBe(true);
    expect(finished.newRoundBlockReason.value).toBe("");
    finished.clearPolling();
  });

  // 真实死胡同：一条轨道已出结果、另一条被重启打断，外壳如实报告「已中断」。
  // 本轮范围仍然锁死（没有别的过程能插进来），但「开始新一轮」必须可点——
  // 并行模式下这是唯一出口。
  it("keeps the round locked yet unlocks a new round for a finished plus interrupted Flow", () => {
    const state = useDiscoveryParallelFlow({ profileId: "profile-dead-ended-flow" });
    state.restore({
      id: "flow-dead-ended",
      profile_id: "profile-dead-ended-flow",
      selection: "all",
      status: "interrupted",
      tracks: [
        {
          id: "b", flow_id: "flow-dead-ended", platform: "boss" as const,
          status: "done", stage: "complete", result_run_id: "result-finished",
        },
        {
          id: "z", flow_id: "flow-dead-ended", platform: "zhilian" as const,
          status: "interrupted", stage: "ai", screen_run_id: "screen-interrupted",
        },
      ],
    });

    expect(state.hasUnfinishedRound.value).toBe(true);
    // 状态词表：已中断没有活体 worker，「开始新一轮」出口必须放行（提交闸门同一份清单）。
    expect(state.canStartNewRound.value).toBe(true);
    expect(state.canResetNewRound.value).toBe(true);
    state.clearPolling();
  });

  // 外壳谎报「排队中」时按钮锁死——那正是这条流程原本的死法，必须被钉住。
  it("keeps a genuinely queued Flow envelope blocking a new round", () => {
    const state = useDiscoveryParallelFlow({ profileId: "profile-queued-guard" });
    state.restore({
      id: "flow-queued-guard",
      profile_id: "profile-queued-guard",
      selection: "all",
      status: "queued",
      tracks: [
        {
          id: "b", flow_id: "flow-queued-guard", platform: "boss" as const,
          status: "done", stage: "complete", result_run_id: "result-finished",
        },
        {
          id: "z", flow_id: "flow-queued-guard", platform: "zhilian" as const,
          status: "queued", stage: "pending",
        },
      ],
    });

    expect(state.hasUnfinishedRound.value).toBe(true);
    expect(state.canResetNewRound.value).toBe(false);
    state.clearPolling();
  });

  it("uses the current profile for Flow reads and new Flow creation", async () => {
    const profileId = ref("profile-old");
    const calls: Array<{ url: string; options?: Record<string, unknown> }> = [];
    const request = async <T>(url: string, options?: Record<string, unknown>): Promise<T> => {
      calls.push({ url, options });
      if (url.startsWith("/api/flows/current")) return { flow: null } as T;
      if (url === "/api/flows") return {
        flow: { id: "flow-new-profile", profile_id: "profile-new", selection: "boss", tracks: [] },
      } as T;
      return {} as T;
    };
    const state = useDiscoveryParallelFlow({ profileId, request });

    await state.refresh();
    profileId.value = "profile-new";
    await state.refresh();
    expect(calls.filter((call) => call.url.startsWith("/api/flows/current"))).toEqual([
      { url: "/api/flows/current?profile_id=profile-old", options: undefined },
      { url: "/api/flows/current?profile_id=profile-new", options: undefined },
    ]);
    calls.length = 0;
    await state.start("boss");

    const create = calls.find((call) => call.url === "/api/flows");
    expect((create?.options?.json as Record<string, unknown>).profile_id).toBe("profile-new");
  });

  it("invalidates delayed preparation before a profile switch and does not submit stale context", async () => {
    const profileId = ref("profile-old");
    let releaseCurrent!: (value: { flow: null }) => void;
    const pendingCurrent = new Promise<{ flow: null }>((resolve) => { releaseCurrent = resolve; });
    const calls: Array<{ url: string; options?: Record<string, unknown> }> = [];
    let currentCalls = 0;
    const request = async <T>(url: string, options?: Record<string, unknown>): Promise<T> => {
      calls.push({ url, options });
      if (url.startsWith("/api/flows/current")) {
        currentCalls += 1;
        return (currentCalls === 1 ? pendingCurrent : { flow: null }) as T;
      }
      if (url.startsWith("/api/filter-labels")) return { fields: [] } as T;
      if (url === "/api/flows") {
        return {
          flow: { id: "flow-new", profile_id: "profile-new", selection: "all", tracks: [] },
        } as T;
      }
      return {} as T;
    };
    const state = useDiscoveryParallelFlow({ profileId, request });
    const preparing = state.prepareDialog({}, {
      keywords: ["旧画像关键词"], cities: ["上海"], locations: {},
      profileSummary: "旧画像画像", profileFacts: {}, selection: "all",
    });

    profileId.value = "profile-new";
    releaseCurrent({ flow: null });
    await preparing;
    await state.start("all");

    const create = calls.find((call) => call.url === "/api/flows");
    expect((create?.options?.json as Record<string, unknown>).profile_id).toBe("profile-new");
    expect(calls.filter((call) => call.url === "/api/execute-search")).toHaveLength(0);
    expect(state.unifiedValues.salary).toEqual([]);
  });

  it("does not write a delayed dialog context after its preparation intent expires", async () => {
    let releaseCurrent!: (value: { flow: null }) => void;
    const pendingCurrent = new Promise<{ flow: null }>((resolve) => { releaseCurrent = resolve; });
    const executeCalls: RequestInit[] = [];
    const request = async <T>(url: string, options?: RequestInit): Promise<T> => {
      if (url.startsWith("/api/flows/current")) return pendingCurrent as T;
      if (url === "/api/flows") return {
        flow: { id: "fresh-dialog-flow", profile_id: "profile-dialog-intent", selection: "all", tracks: [] },
      } as T;
      if (url === "/api/execute-search") {
        executeCalls.push(options || {});
        return { task_id: "stale-dialog-task" } as T;
      }
      return {} as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-dialog-intent", request });
    let current = true;
    const preparing = state.prepareDialog(
      {},
      { keywords: ["旧关键词"], cities: ["旧城市"], locations: {}, profileSummary: "旧画像" },
      { isCurrent: () => current },
    );

    current = false;
    releaseCurrent({ flow: null });
    await preparing;
    await state.start("all");

    expect(executeCalls).toHaveLength(0);
  });

  it("drops a Flow start response that returns after the profile changes", async () => {
    const profileId = ref("profile-old");
    let releaseCreate!: (value: { flow: { id: string; profile_id: string; selection: "all"; tracks: [] } }) => void;
    const pendingCreate = new Promise<{ flow: { id: string; profile_id: string; selection: "all"; tracks: [] } }>((resolve) => {
      releaseCreate = resolve;
    });
    const calls: Array<{ url: string; options?: Record<string, unknown> }> = [];
    const request = async <T>(url: string, options?: Record<string, unknown>): Promise<T> => {
      calls.push({ url, options });
      if (url === "/api/flows") return pendingCreate as T;
      if (url.startsWith("/api/flows/current")) return { flow: null } as T;
      return {} as T;
    };
    const state = useDiscoveryParallelFlow({ profileId, request });
    const starting = state.start("all");
    profileId.value = "profile-new";
    releaseCreate({ flow: { id: "stale-flow", profile_id: "profile-old", selection: "all", tracks: [] } });

    await expect(starting).rejects.toMatchObject({
      code: "FLOW_START_STALE",
      profileId: "profile-old",
      flow: { id: "stale-flow" },
    });
    expect(state.flow.value).toBeNull();
    expect(calls.filter((call) => call.url === "/api/execute-search")).toHaveLength(0);
  });

  it("rejects a second Flow start while the first request is loading", async () => {
    let releaseCreate!: (value: { flow: { id: string; profile_id: string; selection: "all"; tracks: [] } }) => void;
    const pendingCreate = new Promise<{ flow: { id: string; profile_id: string; selection: "all"; tracks: [] } }>((resolve) => {
      releaseCreate = resolve;
    });
    const calls: string[] = [];
    const request = async <T>(url: string): Promise<T> => {
      calls.push(url);
      if (url === "/api/flows") return pendingCreate as T;
      if (url.startsWith("/api/flows/current")) return { flow: null } as T;
      return {} as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-start-mutex", request });
    const first = state.start("all");
    const second = state.start("all");

    expect(calls.filter((url) => url === "/api/flows")).toHaveLength(1);
    releaseCreate({ flow: { id: "flow-start-mutex", profile_id: "profile-start-mutex", selection: "all", tracks: [] } });
    await first;
    await expect(second).rejects.toThrow("流程启动中");
    state.clearPolling();
  });

  it("does not let an older null refresh clear the newest Flow", async () => {
    const deferred = <T,>() => {
      let resolve!: (value: T) => void;
      const promise = new Promise<T>((resolvePromise) => {
        resolve = resolvePromise;
      });
      return { promise, resolve };
    };
    const pending: Array<{ resolve: (value: { flow: unknown }) => void }> = [];
    const newestFlow = {
      id: "flow-newest",
      profile_id: "profile-overlap",
      selection: "all" as const,
      tracks: [{ id: "boss", flow_id: "flow-newest", platform: "boss" as const, status: "running", stage: "scrape", scrape_run_id: "scrape-newest" }],
    };
    const request = async <T>(url: string): Promise<T> => {
      if (url.startsWith("/api/task-state/")) return {} as T;
      const wait = deferred<{ flow: unknown }>();
      pending.push(wait);
      return wait.promise as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-overlap", request });

    const older = state.refresh();
    const newer = state.refresh();
    pending[1].resolve({ flow: newestFlow });
    await newer;
    pending[0].resolve({ flow: null });
    await older;

    expect(state.flow.value?.id).toBe("flow-newest");
    expect(state.isFlowOwnedScrapeTask("scrape-newest")).toBe(true);
  });

  it("does not let an older Flow refresh overwrite the newest Flow or ownership", async () => {
    const deferred = <T,>() => {
      let resolve!: (value: T) => void;
      const promise = new Promise<T>((resolvePromise) => {
        resolve = resolvePromise;
      });
      return { promise, resolve };
    };
    const pending: Array<{ resolve: (value: { flow: unknown }) => void }> = [];
    const olderFlow = {
      id: "flow-older",
      profile_id: "profile-overlap",
      selection: "all" as const,
      tracks: [{ id: "boss", flow_id: "flow-older", platform: "boss" as const, status: "running", stage: "scrape", scrape_run_id: "scrape-older" }],
    };
    const newestFlow = {
      id: "flow-newest",
      profile_id: "profile-overlap",
      selection: "all" as const,
      tracks: [{ id: "boss", flow_id: "flow-newest", platform: "boss" as const, status: "running", stage: "scrape", scrape_run_id: "scrape-newest" }],
    };
    const request = async <T>(url: string): Promise<T> => {
      if (url.startsWith("/api/task-state/")) return {} as T;
      const wait = deferred<{ flow: unknown }>();
      pending.push(wait);
      return wait.promise as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-overlap", request });

    const older = state.refresh();
    const newer = state.refresh();
    pending[1].resolve({ flow: newestFlow });
    await newer;
    pending[0].resolve({ flow: olderFlow });
    await older;

    expect(state.flow.value?.id).toBe("flow-newest");
    expect(state.isFlowOwnedScrapeTask("scrape-newest")).toBe(true);
    expect(state.isFlowOwnedScrapeTask("scrape-older")).toBe(false);
  });

  it("identifies only scrape tasks owned by the current all-platform Flow", () => {
    const state = useDiscoveryParallelFlow({ profileId: "profile-owned" });
    state.restore({
      id: "flow-owned",
      profile_id: "profile-owned",
      selection: "all",
      tracks: [
        { id: "b", flow_id: "flow-owned", platform: "boss", status: "running", stage: "scrape", scrape_run_id: "scrape-owned" },
      ],
    });

    expect(state.isFlowOwnedScrapeTask("scrape-owned")).toBe(true);
    expect(state.isFlowOwnedScrapeTask("scrape-other")).toBe(false);
  });

  it("operates only the selected track and rejects a new round while another is live", async () => {
    const calls: string[] = [];
    const current = {
      id: "flow-1",
      profile_id: "profile-1",
      selection: "all" as const,
      tracks: [
        { id: "b", flow_id: "flow-1", platform: "boss" as const, status: "running", stage: "scrape" },
        { id: "z", flow_id: "flow-1", platform: "zhilian" as const, status: "done", stage: "complete" },
      ],
    };
    const request = async <T>(url: string): Promise<T> => {
      calls.push(url);
      return { flow: current } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-1", request });
    state.restore(current);
    await state.operate("boss", "pause");
    expect(calls.at(-1)).toBe("/api/flows/flow-1/tracks/boss/pause");
    expect(state.canStartNewRound.value).toBe(false);
    await expect(state.start("boss")).rejects.toThrow("运行或暂停");
    state.clearPolling();
  });

  it.each(["queued", "running"] as const)(
    "treats an active Flow envelope as busy even when %s has no tracks",
    async (status) => {
      const state = useDiscoveryParallelFlow({ profileId: "profile-envelope-active" });
      state.restore({
        id: `flow-envelope-${status}`,
        profile_id: "profile-envelope-active",
        selection: "all",
        status,
        tracks: [],
      });

      expect(state.hasUnfinishedRound.value).toBe(true);
      expect(state.canStartNewRound.value).toBe(false);
      expect(state.canResetNewRound.value).toBe(false);
      await expect(state.start("all")).rejects.toThrow("运行或暂停");
      state.clearPolling();
    },
  );

  // SPEC 046 判活口径：只有「排队中 / 运行中」的轨道算此刻有活体 worker（hasLiveWorker），
  // 已暂停与已中断属于「这一轮还没结束」（hasUnfinishedRound），但没有活体——04 能否接回本轮
  // 结果、迟到响应能否覆盖实时现场只按前者判。两个谓词必须分派，不得合并。
  it("separates live worker (queued/running) from an unfinished round (paused/interrupted)", () => {
    const cases: Array<[string, boolean]> = [
      ["queued", true],
      ["running", true],
      ["paused", false],
      ["interrupted", false],
      ["done", false],
      ["failed", false],
    ];
    for (const [status, liveWorker] of cases) {
      const state = useDiscoveryParallelFlow({ profileId: "profile-live-worker" });
      state.restore({
        id: `flow-live-worker-${status}`,
        profile_id: "profile-live-worker",
        selection: "all",
        status,
        tracks: [{
          id: "b", flow_id: `flow-live-worker-${status}`, platform: "boss" as const,
          status, stage: "scrape",
        }],
      });
      expect(state.hasLiveWorker.value).toBe(liveWorker);
      // 未结束（锁范围、锁提交、定落点）覆盖排队/运行/暂停/中断四种外壳。
      expect(state.hasUnfinishedRound.value).toBe(["queued", "running", "paused", "interrupted"].includes(status));
      state.clearPolling();
    }
  });

  it("restores active Tracks into polling and stops polling after a terminal refresh", async () => {
    let current = {
      id: "flow-refresh",
      profile_id: "profile-refresh",
      selection: "all" as const,
      tracks: [{ id: "b", flow_id: "flow-refresh", platform: "boss" as const, status: "running", stage: "scrape" }],
    };
    const request = async <T>(url: string): Promise<T> => {
      if (url.startsWith("/api/flows/current")) return { flow: current } as T;
      return { flow: current } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-refresh", request, pollIntervalMs: 10000 });
    state.restore(current);
    expect(state.polling.value).toBe(true);

    current = {
      ...current,
      tracks: [{ ...current.tracks[0], status: "done", stage: "complete" }],
    };
    await state.refresh();
    expect(state.polling.value).toBe(false);
    state.clearPolling();
  });

  it("forwards a selected track operation to the Flow coordinator", async () => {
    const calls: string[] = [];
    const current = {
      id: "flow-ops",
      profile_id: "profile-ops",
      selection: "all" as const,
      tracks: [
        { id: "b", flow_id: "flow-ops", platform: "boss" as const, status: "running", stage: "scrape", screen_run_id: "boss-run" },
        { id: "z", flow_id: "flow-ops", platform: "zhilian" as const, status: "running", stage: "scrape", screen_run_id: "zhilian-run" },
      ],
    };
    const request = async <T>(url: string): Promise<T> => {
      calls.push(url);
      return { flow: current } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-ops", request });
    state.restore(current);
    await Promise.resolve();
    state.clearPolling();
    calls.length = 0;

    await state.operate("boss", "pause");

    expect(calls).toEqual(["/api/flows/flow-ops/tracks/boss/pause"]);
    state.clearPolling();
  });

  it.each([
    ["boss", "pause"],
    ["boss", "resume"],
    ["boss", "stop"],
    ["zhilian", "pause"],
    ["zhilian", "resume"],
    ["zhilian", "stop"],
  ] as const)("uses one Flow action for a running %s Track %s", async (platform, action) => {
    const calls: string[] = [];
    const current = {
      id: "flow-single-action",
      profile_id: "profile-single-action",
      selection: "all" as const,
      tracks: [
        {
          id: "b", flow_id: "flow-single-action", platform: "boss" as const,
          status: "running", stage: "scrape", scrape_run_id: "boss-run",
        },
        {
          id: "z", flow_id: "flow-single-action", platform: "zhilian" as const,
          status: "running", stage: "scrape", scrape_run_id: "zhilian-run",
        },
      ],
    };
    const request = async <T>(url: string): Promise<T> => {
      calls.push(url);
      return { flow: current } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-single-action", request });
    state.restore(current);
    await Promise.resolve();
    state.clearPolling();
    calls.length = 0;

    await state.operate(platform, action);

    expect(calls).toEqual([
      `/api/flows/flow-single-action/tracks/${platform}/${action}`,
    ]);
    state.clearPolling();
  });

  // 真实一轮：AI 阶段点「暂停」后端回 503，界面却静默无反应。
  // 轨道动作被拒绝必须走 notify 口径给用户一句话中文提示。
  it("tells the user when a Track action is rejected", async () => {
    const notices: string[] = [];
    const current = {
      id: "flow-action-notify",
      profile_id: "profile-action-notify",
      selection: "all" as const,
      tracks: [
        {
          id: "z", flow_id: "flow-action-notify", platform: "zhilian" as const,
          status: "running", stage: "ai", scrape_run_id: "zhilian-scrape",
        },
      ],
    };
    const request = async <T>(url: string): Promise<T> => {
      if (url.endsWith("/pause")) {
        throw new ApiError(503, {
          ok: false,
          error_code: "flow_task_operation_failed",
          message: "目标任务操作失败，请刷新任务状态后重试",
        });
      }
      return { flow: current } as T;
    };
    const state = useDiscoveryParallelFlow({
      profileId: "profile-action-notify",
      request,
      onActionError: (message: string) => { notices.push(message); },
    });
    state.restore(current);
    await Promise.resolve();
    state.clearPolling();

    await expect(state.operate("zhilian", "pause")).rejects.toThrow();

    expect(notices).toHaveLength(1);
    expect(notices[0]).toContain("智联");
    expect(notices[0]).toContain("目标任务操作失败，请刷新任务状态后重试");
    state.clearPolling();
  });

  it("uses the Flow resume action for a paused preflight Track without a run", async () => {
    const calls: string[] = [];
    const current = {
      id: "flow-preflight-action",
      profile_id: "profile-preflight-action",
      selection: "boss" as const,
      tracks: [{
        id: "b", flow_id: "flow-preflight-action", platform: "boss" as const,
        status: "paused", stage: "pending",
        submission_snapshot: {
          script_params: { keyword: "Python", city: ["全国"], pages: 1 },
        },
      }],
    };
    const request = async <T>(url: string): Promise<T> => {
      calls.push(url);
      return { flow: current } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-preflight-action", request });
    state.restore(current);
    await Promise.resolve();
    state.clearPolling();
    calls.length = 0;

    await state.operate("boss", "resume");

    expect(calls).toEqual([
      "/api/flows/flow-preflight-action/tracks/boss/resume",
    ]);
    state.clearPolling();
  });

  it("refreshes authoritative Flow state after an action error without fabricating status", async () => {
    const calls: string[] = [];
    const initial = {
      id: "flow-action-error",
      profile_id: "profile-action-error",
      selection: "all" as const,
      tracks: [{
        id: "b", flow_id: "flow-action-error", platform: "boss" as const,
        status: "running", stage: "scrape", scrape_run_id: "boss-run",
      }],
    };
    const authoritative = {
      ...initial,
      tracks: [{ ...initial.tracks[0], status: "paused", stage: "scrape" }],
    };
    const request = async <T>(url: string): Promise<T> => {
      calls.push(url);
      if (url === "/api/flows/flow-action-error/tracks/boss/pause") {
        throw new Error("操作结果暂不可确认");
      }
      if (url.startsWith("/api/flows/current")) return { flow: authoritative } as T;
      if (url.startsWith("/api/task-state/")) return {} as T;
      return { flow: initial } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-action-error", request });
    state.restore(initial);
    await Promise.resolve();
    state.clearPolling();
    calls.length = 0;

    await expect(state.operate("boss", "pause")).rejects.toThrow("操作结果暂不可确认");

    expect(calls).toEqual([
      "/api/flows/flow-action-error/tracks/boss/pause",
      "/api/flows/current?profile_id=profile-action-error",
      "/api/task-state/boss-run?profile_id=profile-action-error",
    ]);
    expect(state.flow.value?.tracks[0].status).toBe("paused");
    state.clearPolling();
  });

  it("keeps a failed refresh Flow as stale and blocks new rounds", async () => {
    const existing = {
      id: "flow-refresh-stale",
      profile_id: "profile-refresh-stale",
      selection: "all" as const,
      tracks: [{
        id: "b", flow_id: "flow-refresh-stale", platform: "boss" as const,
        status: "done", stage: "complete",
      }],
    };
    const request = async <T>(url: string): Promise<T> => {
      if (url.startsWith("/api/flows/current")) throw new Error("状态读取失败");
      return { flow: existing } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-refresh-stale", request });
    state.restore(existing);

    await expect(state.refresh()).rejects.toThrow("状态读取失败");

    expect(state.flow.value).toMatchObject({ id: existing.id, profile_id: existing.profile_id });
    expect(state.error.value).toContain("状态读取失败");
    expect((state as unknown as { stale?: { value: boolean } }).stale?.value).toBe(true);
    expect(state.canStartNewRound.value).toBe(false);
  });

  it("preserves both operation and refresh errors when neither result is authoritative", async () => {
    const existing = {
      id: "flow-operate-refresh-fail",
      profile_id: "profile-operate-refresh-fail",
      selection: "all" as const,
      tracks: [{
        id: "b", flow_id: "flow-operate-refresh-fail", platform: "boss" as const,
        status: "running", stage: "scrape",
      }],
    };
    const request = async <T>(url: string): Promise<T> => {
      if (url.includes("/tracks/boss/pause")) throw new Error("操作结果暂不可确认");
      if (url.startsWith("/api/flows/current")) throw new Error("状态刷新失败");
      return { flow: existing } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-operate-refresh-fail", request });
    state.restore(existing);
    state.clearPolling();

    await expect(state.operate("boss", "pause")).rejects.toThrow("操作结果暂不可确认");

    expect(state.error.value).toContain("操作结果暂不可确认");
    expect(state.error.value).toContain("状态刷新失败");
    expect((state as unknown as { stale?: { value: boolean } }).stale?.value).toBe(true);
  });

  it("clears stale and read errors after the next authoritative refresh", async () => {
    const existing = {
      id: "flow-refresh-recover",
      profile_id: "profile-refresh-recover",
      selection: "all" as const,
      tracks: [{
        id: "b", flow_id: "flow-refresh-recover", platform: "boss" as const,
        status: "done", stage: "complete",
      }],
    };
    let fail = true;
    const request = async <T>(url: string): Promise<T> => {
      if (url.startsWith("/api/flows/current")) {
        if (fail) throw new Error("暂时无法读取流程");
        return { flow: null } as T;
      }
      return { flow: existing } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-refresh-recover", request });
    state.restore(existing);
    await expect(state.refresh()).rejects.toThrow("暂时无法读取流程");
    expect(state.error.value).toContain("暂时无法读取流程");

    fail = false;
    await state.refresh();

    expect(state.flow.value).toBeNull();
    expect(state.error.value).toBe("");
    expect((state as unknown as { stale?: { value: boolean } }).stale?.value).toBe(false);
    expect(state.canStartNewRound.value).toBe(true);
  });

  it("clears a read error when the Flow is explicitly discarded during a mode switch", async () => {
    const request = async <T>(url: string): Promise<T> => {
      if (url.startsWith("/api/flows/current")) throw new Error("旧画像流程读取失败");
      return {} as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-mode-error", request });
    await expect(state.refresh()).rejects.toThrow("旧画像流程读取失败");
    expect(state.error.value).toContain("旧画像流程读取失败");

    state.restore(null);

    expect(state.flow.value).toBeNull();
    expect(state.error.value).toBe("");
    expect((state as unknown as { stale?: { value: boolean } }).stale?.value).toBe(false);
  });

  it("surfaces malformed initial restore data instead of throwing silently", () => {
    const state = useDiscoveryParallelFlow({ profileId: "profile-restore-error" });
    const malformed = {
      id: "malformed-flow",
      profile_id: "profile-restore-error",
      selection: "all",
      tracks: null,
    } as never;

    expect(() => state.restore(malformed)).not.toThrow();
    expect(state.error.value).toContain("流程恢复失败");
    expect((state as unknown as { stale?: { value: boolean } }).stale?.value).toBe(true);
  });

  it("retains the last known Flow when a restore payload contains malformed Tracks", () => {
    const state = useDiscoveryParallelFlow({ profileId: "profile-restore-track-error" });
    const existing = {
      id: "known-good-flow",
      profile_id: "profile-restore-track-error",
      selection: "all" as const,
      tracks: [{ id: "b", flow_id: "known-good-flow", platform: "boss" as const, status: "done", stage: "complete" }],
    };
    state.restore(existing);

    expect(() => state.restore({
      ...existing,
      id: "malformed-track-flow",
      tracks: [null],
    } as never)).not.toThrow();

    expect(state.flow.value).toMatchObject({ id: "known-good-flow" });
    expect(state.error.value).toContain("流程恢复失败");
    expect((state as unknown as { stale?: { value: boolean } }).stale?.value).toBe(true);
  });

  it("loads each platform schema and preserves the projected drafts", async () => {
    const request = async <T>(url: string): Promise<T> => {
      if (url.includes("filter-labels?platform=boss")) {
        return {
          platform: "boss",
          fields: [{ key: "stage", label: "融资阶段", multiple: true, options: [
            { value: "0", label: "不限" }, { value: "804", label: "B轮" },
          ] }],
        } as T;
      }
      if (url.includes("filter-labels?platform=zhilian")) {
        return {
          platform: "zhilian",
          fields: [{ key: "company_nature", label: "企业性质", multiple: true, options: [
            { value: "0", label: "全部" }, { value: "1", label: "国企" },
          ] }],
        } as T;
      }
      return { flow: null } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-1", request });

    await state.prepareDialog({
      boss: { stage: ["804"] },
      zhilian: { company_nature: ["1"] },
    });

    expect(state.platformGroups.boss?.[0]).toMatchObject({
      key: "stage",
      sentinel: { label: "不限", code: "0" },
      options: [["B轮", "804"]],
    });
    expect(state.platformGroups.zhilian?.[0]).toMatchObject({
      key: "company_nature",
      sentinel: { label: "全部", code: "0" },
      options: [["国企", "1"]],
    });
    expect(state.platformValues.boss.stage).toEqual(["804"]);
    expect(state.platformValues.zhilian.company_nature).toEqual(["1"]);
  });

  it("starts an all-platform dialog from resume semantics without importing exclusive drafts", async () => {
    const fields = {
      experience: [{ value: "106", label: "3-5年" }],
      recruiter_activity: [{ value: "2", label: "近一个月" }],
      stage: [{ value: "804", label: "B轮" }],
      company_nature: [{ value: "1", label: "国企" }],
    };
    const request = async <T>(url: string): Promise<T> => {
      if (url.includes("filter-labels?platform=boss")) {
        return { platform: "boss", fields: [
          ...Object.entries(fields).filter(([key]) => key !== "company_nature").map(([key, options]) => ({ key, label: key, multiple: true, options })),
        ] } as T;
      }
      if (url.includes("filter-labels?platform=zhilian")) {
        return { platform: "zhilian", fields: [
          ...Object.entries(fields).filter(([key]) => key !== "stage").map(([key, options]) => ({ key, label: key, multiple: true, options })),
        ] } as T;
      }
      return { flow: null } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-resume", request });

    await state.prepareDialog(
      { boss: { experience: ["old"], stage: ["804"] }, zhilian: { company_nature: ["1"] } },
      {
        selection: "all",
        resumeSemantic: {
          experience: ["3-5年"], stage: ["B轮"], company_nature: ["国企"],
          "招聘者活跃时间": ["近一个月"],
        },
      } as never,
    );

    expect(state.unifiedValues.experience).toEqual(["3-5年"]);
    expect(state.unifiedValues.recruiter_activity).toEqual(["近一个月"]);
    expect(state.platformValues.boss.experience).toEqual(["106"]);
    expect(state.platformValues.zhilian.experience).toEqual(["106"]);
    expect(state.platformValues.boss.stage).toEqual([]);
    expect(state.platformValues.zhilian.company_nature).toEqual([]);
  });

  it("projects resume semantics once and preserves later platform tuning when all is reopened", async () => {
    const request = async <T>(url: string): Promise<T> => {
      if (url.includes("filter-labels?platform=boss")) {
        return { platform: "boss", fields: [
          { key: "experience", label: "经验", multiple: true, options: [{ value: "106", label: "3-5年" }] },
          { key: "stage", label: "阶段", multiple: true, options: [{ value: "0", label: "不限" }] },
        ] } as T;
      }
      if (url.includes("filter-labels?platform=zhilian")) {
        return { platform: "zhilian", fields: [
          { key: "experience", label: "经验", multiple: true, options: [{ value: "106", label: "3-5年" }] },
          { key: "company_nature", label: "性质", multiple: true, options: [{ value: "0", label: "全部" }] },
        ] } as T;
      }
      return { flow: null } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-reopen", request });
    const context = {
      selection: "all" as const,
      keywords: [], cities: [], locations: {},
      resumeSemantic: { experience: ["3-5年"] },
    };

    await state.prepareDialog({ boss: {}, zhilian: {} }, context);
    state.setPlatformFilters("zhilian", {
      ...state.platformValues.zhilian,
      degree: ["206"],
      industry: ["002"],
      company_nature: ["1"],
      zhilian_mba_tuning: ["emba"],
    });
    await state.prepareDialog({ boss: {}, zhilian: {} }, context);

    expect(state.unifiedValues.experience).toEqual(["3-5年"]);
    expect(state.platformValues.zhilian).toMatchObject({
      experience: ["106"], degree: ["206"], industry: ["002"],
      company_nature: ["1"], zhilian_mba_tuning: ["emba"],
    });
  });

  it("submits both platform runs after creating one Flow when search context is ready", async () => {
    const calls: Array<{ url: string; options?: Record<string, unknown> }> = [];
    const flow = {
      id: "flow-2",
      profile_id: "profile-1",
      selection: "all" as const,
      tracks: [
        { id: "b", flow_id: "flow-2", platform: "boss" as const, status: "queued", stage: "pending" },
        { id: "z", flow_id: "flow-2", platform: "zhilian" as const, status: "queued", stage: "pending" },
      ],
    };
    let currentFlow: typeof flow | null = null;
    const request = async <T>(url: string, options?: Record<string, unknown>): Promise<T> => {
      calls.push({ url, options });
      if (url === "/api/flows") { currentFlow = flow; return { flow } as T; }
      if (url === "/api/execute-search") return { task_id: `task-${calls.length}` } as T;
      return { flow: currentFlow } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-1", request });
    await state.prepareDialog(
      { boss: {}, zhilian: {} },
      { keywords: ["Python"], cities: ["全国"], locations: {} },
    );

    await state.start("all");

    const launches = calls.filter((call) => call.url === "/api/execute-search");
    expect(launches).toHaveLength(2);
    expect(launches.map((call) => (call.options?.json as Record<string, unknown>).platform))
      .toEqual(expect.arrayContaining(["boss", "zhilian"]));
    expect(launches[0].options?.json).toMatchObject({ flow_id: "flow-2", profile_id: "profile-1" });
  });

  it("freezes the same platform values into both execute-search requests", async () => {
    const calls: Array<{ url: string; options?: Record<string, unknown> }> = [];
    const flow = {
      id: "flow-frozen-fields", profile_id: "profile-1", selection: "all" as const,
      tracks: [
        { id: "b", flow_id: "flow-frozen-fields", platform: "boss" as const, status: "queued", stage: "pending" },
        { id: "z", flow_id: "flow-frozen-fields", platform: "zhilian" as const, status: "queued", stage: "pending" },
      ],
    };
    const request = async <T>(url: string, options?: Record<string, unknown>): Promise<T> => {
      calls.push({ url, options });
      if (url === "/api/flows") return { flow } as T;
      if (url === "/api/execute-search") return { task_id: `frozen-${calls.length}` } as T;
      if (url.startsWith("/api/flows/current")) return { flow: null } as T;
      return { flow } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-1", request });
    await state.prepareDialog(
      { boss: {}, zhilian: {} },
      { keywords: ["Python"], cities: ["全国"], locations: {} },
    );
    const frozen: ConditionSnapshotV2 = {
      snapshotVersion: 2,
      mappingVersion: MAPPER_VERSION,
      unifiedValues: { salary: [], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [] },
      platformValues: {
        boss: { salary: ["boss-salary"], stage: ["804"] },
        zhilian: { salary: ["zhilian-salary"], company_nature: ["1"] },
      },
      overrides: { boss: {}, zhilian: {} },
      exclusiveValues: { boss: { stage: ["804"] }, zhilian: { company_nature: ["1"] } },
    };

    await state.start("all", undefined, { snapshot: frozen });

    const launches = calls.filter((call) => call.url === "/api/execute-search");
    expect(launches).toHaveLength(2);
    expect(Object.fromEntries(launches.map((call) => {
      const body = call.options?.json as Record<string, unknown>;
      return [body.platform, body.auto_screen_fields];
    }))).toEqual({
      boss: { salary: ["boss-salary"], stage: ["804"] },
      zhilian: { salary: ["zhilian-salary"], company_nature: ["1"] },
    });
    state.clearPolling();
  });

  it("forwards the existing cross-platform dedupe switch to both platform requests", async () => {
    const calls: Array<{ url: string; options?: Record<string, unknown> }> = [];
    const flow = {
      id: "flow-dedupe",
      profile_id: "profile-1",
      selection: "all" as const,
      tracks: [
        { id: "b", flow_id: "flow-dedupe", platform: "boss" as const, status: "queued", stage: "pending" },
        { id: "z", flow_id: "flow-dedupe", platform: "zhilian" as const, status: "queued", stage: "pending" },
      ],
    };
    const request = async <T>(url: string, options?: Record<string, unknown>): Promise<T> => {
      calls.push({ url, options });
      if (url === "/api/flows") return { flow } as T;
      if (url === "/api/execute-search") return { task_id: "dedupe-task" } as T;
      if (url.startsWith("/api/flows/current")) return { flow: null } as T;
      return { flow } as T;
    };
    window.localStorage.setItem("cross_platform_dedupe_enabled", "false");
    const state = useDiscoveryParallelFlow({ profileId: "profile-1", request });
    await state.prepareDialog({ boss: {}, zhilian: {} }, { keywords: ["Python"], cities: ["全国"], locations: {} });
    await state.start("all");

    const launches = calls.filter((call) => call.url === "/api/execute-search");
    expect(launches).toHaveLength(2);
    expect(launches.map((call) => (call.options?.json as Record<string, unknown>).cross_platform_dedupe))
      .toEqual([false, false]);
    window.localStorage.removeItem("cross_platform_dedupe_enabled");
    state.clearPolling();
  });

  it("keeps the backend platform-specific unavailable message and refreshes durable Track failure", async () => {
    const flow = {
      id: "flow-disabled",
      profile_id: "profile-1",
      selection: "all" as const,
      tracks: [
        { id: "b", flow_id: "flow-disabled", platform: "boss" as const, status: "queued", stage: "pending" },
        { id: "z", flow_id: "flow-disabled", platform: "zhilian" as const, status: "failed", stage: "scrape", error_code: "platform_unavailable", reason: "智联平台暂不可用" },
      ],
    };
    let created = false;
    const request = async <T>(url: string): Promise<T> => {
      if (url === "/api/flows") {
        if (!created) {
          created = true;
          throw new Error("智联平台暂不可用");
        }
        return { flow } as T;
      }
      return { flow } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-1", request });
    await expect(state.start("all")).rejects.toThrow("智联平台暂不可用");
    expect(state.error.value).toBe("智联平台暂不可用");
  });

  it("resets condition source and platform drafts before projecting a new resume", async () => {
    const schemaFields = [
      { key: "salary", label: "薪资", multiple: true, options: [
        { value: "salary", label: "10-20K" }, { value: "salary-boss-new", label: "20-50K" },
        { value: "salary-z", label: "10K-15K" }, { value: "salary-z2", label: "15K-25K" },
        { value: "salary-z3", label: "25K-35K" }, { value: "salary-z4", label: "35K-50K" },
      ] },
      { key: "experience", label: "经验", multiple: true, options: [{ value: "experience", label: "3-5年" }, { value: "experience-new", label: "5-10年" }] },
      { key: "degree", label: "学历", multiple: true, options: [{ value: "degree", label: "本科" }, { value: "degree-new", label: "硕士" }] },
      { key: "industry", label: "行业", multiple: true, options: [
        { value: "industry", label: "互联网" }, { value: "industry-boss-new", label: "金融" },
        { value: "industry-boss-education", label: "教育培训" },
        { value: "industry-z", label: "互联网/AI/软件/IT服务" },
        { value: "industry-new", label: "金融业" }, { value: "industry-z-education", label: "教育/培训/科研" },
      ] },
      { key: "scale", label: "规模", multiple: true, options: [{ value: "scale", label: "100-499人" }, { value: "scale-z", label: "100-299人" }, { value: "scale-z2", label: "300-499人" }, { value: "scale-new", label: "500-999人" }] },
      { key: "recruiter_activity", label: "活跃", multiple: true, options: [{ value: "activity", label: "近一个月" }, { value: "activity-new", label: "近三个月" }] },
      { key: "stage", label: "阶段", multiple: true, options: [{ value: "stage-legacy", label: "B轮" }] },
      { key: "company_nature", label: "性质", multiple: true, options: [{ value: "nature-legacy", label: "国企" }] },
    ];
    const request = async <T>(url: string): Promise<T> => {
      if (url.includes("filter-labels?platform=boss")) {
        return { platform: "boss", fields: schemaFields.filter((field) => field.key !== "company_nature")} as T;
      }
      if (url.includes("filter-labels?platform=zhilian")) {
        return { platform: "zhilian", fields: schemaFields.filter((field) => field.key !== "stage")} as T;
      }
      return { flow: null } as T;
    };
    const state = useDiscoveryParallelFlow({ profileId: "profile-new-resume", request });
    const firstResume = {
      salary: ["10K-20K"], experience: ["3-5年"], degree: ["本科"],
      industry: ["金融"], scale: ["100-499人"], recruiter_activity: ["近一个月"],
    };
    const secondResume = {
      salary: ["20K-50K"], experience: ["5-10年"], degree: ["硕士"],
      industry: ["教育培训"], scale: ["500-999人"], recruiter_activity: ["近三个月"],
    };
    await state.prepareDialog({ boss: {}, zhilian: {} }, { selection: "all", resumeSemantic: firstResume } as never);
    state.setPlatformFilters("zhilian", {
      ...state.platformValues.zhilian,
      company_nature: ["nature-legacy"],
      zhilian_mba_tuning: ["emba"],
      platform_only_industry: ["legacy-industry"],
    });

    const reset = (state as unknown as { resetConditionState: () => void }).resetConditionState;
    expect(reset).toBeTypeOf("function");
    reset();
    await state.prepareDialog({ boss: {}, zhilian: {} }, { selection: "all", resumeSemantic: secondResume } as never);

    expect(state.unifiedValues).toMatchObject({
      salary: ["20K-50K"], experience: ["5-10年"], degree: ["硕士"],
      industry: ["教育培训"], scale: ["500-999人"], recruiter_activity: ["近三个月"],
    });
    expect(state.platformValues.zhilian).not.toHaveProperty("company_nature", ["nature-legacy"]);
    expect(state.platformValues.zhilian).not.toHaveProperty("zhilian_mba_tuning");
    expect(state.platformValues.zhilian).not.toHaveProperty("platform_only_industry");
  });
});

// ---------------------------------------------------------------------------
// 046 D-03：轨道行上的动作必须真的做得到事。operateTrack 的映射表（kind → 后端操作）
// 是唯一的落点来源：表里有的 kind 一定要打到对应端点，表里没有的 kind 不许悄悄发请求，
// 也不许在界面上留一颗点了没反应的按钮（轨道行只渲染这张表覆盖的 kind）。
// ---------------------------------------------------------------------------
describe("useDiscoveryParallelFlow 轨道动作 kind 的实际落点", () => {
  const KIND_TO_OPERATION: Record<string, string> = {
    pause: "pause",
    "pause-scrape": "pause",
    continue: "resume",
    "continue-scrape": "resume",
    cancel: "stop",
  };

  function flowWithRecordedRequests() {
    const urls: string[] = [];
    const request = async <T>(url: string, options?: Record<string, unknown>): Promise<T> => {
      urls.push(String(url));
      return {
        flow: {
          id: "flow-kinds", profile_id: "profile-kinds", selection: "all", status: "running", tracks: [],
        },
        options,
      } as T;
    };
    const flow = useDiscoveryParallelFlow({
      profileId: "profile-kinds", request, pollIntervalMs: 1000000,
    });
    flow.restore({
      id: "flow-kinds",
      profile_id: "profile-kinds",
      selection: "all",
      status: "running",
      tracks: [{
        id: "b", flow_id: "flow-kinds", platform: "boss" as const, status: "running", stage: "scrape",
      }],
    });
    return { flow, urls };
  }

  it("登记的每个轨道动作 kind 都打到它对应的那一个后端操作", async () => {
    expect(Object.keys(TRACK_ACTION_OPERATIONS).sort()).toEqual(Object.keys(KIND_TO_OPERATION).sort());
    for (const kind of Object.keys(KIND_TO_OPERATION)) {
      const { flow, urls } = flowWithRecordedRequests();
      await flow.operateTrack("boss", kind as TrackActionKind);
      expect(urls.at(-1)).toBe(`/api/flows/flow-kinds/tracks/boss/${KIND_TO_OPERATION[kind]}`);
      flow.clearPolling();
    }
  });

  it("映射表之外的 kind 既不发请求，也不冒充做成了什么", async () => {
    const { flow, urls } = flowWithRecordedRequests();
    expect(await flow.operateTrack("boss", "start" as TrackActionKind)).toBeNull();
    expect(await flow.operateTrack("boss", "recrawl" as TrackActionKind)).toBeNull();
    expect(urls).toEqual([]);
    flow.clearPolling();
  });
});
