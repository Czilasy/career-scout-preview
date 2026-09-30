import { effectScope, nextTick, type EffectScope } from "vue";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { requestCapsuleNavigation, useDiscoveryState, type DiscoveryState } from "../useDiscoveryState";
import { useDiscoveryIslandBridge } from "../useDiscoveryIslandBridge";
import { useDiscoverySceneState } from "../useDiscoverySceneState";

const flush = async () => {
  await nextTick();
  await Promise.resolve();
};

let scope: EffectScope | null = null;

// 桥接内注册的 watch 需要一个可回收的作用域：否则上一个用例的监听会继续消费
// 模块级导航信号，把下一个用例的点击吃掉。
function bridge(state: DiscoveryState): void {
  scope = effectScope();
  scope.run(() => useDiscoveryIslandBridge(state));
}

describe("useDiscoveryIslandBridge", () => {
  beforeEach(() => {
    sessionStorage.clear();
    scope = null;
  });

  afterEach(() => {
    scope?.stop();
    scope = null;
  });

  it("灵动岛按真实进度落点，且不清当前轮现场（硬红线）", async () => {
    const profileId = `island-bridge-${Date.now()}-${Math.random()}`;
    const state = useDiscoveryState({ profileId }, () => {});
    state.workflowStateRestored.value = true;
    bridge(state);

    const scene = useDiscoverySceneState();
    const epoch = scene.ensureRoundEpoch(profileId);
    const identity = { profileId, runEpoch: epoch, platform: "boss" as const };
    scene.saveCurrent(identity, { selectedJobKey: "boss:kept", listScrollTop: 77 });
    state.pipelineResult.value = { jobs: [{ job_id: "kept", title: "岗位" }], dropped: [] };
    state.resultLoaded.value = true;

    // 抓取运行中 → 落 02 页（不落未启用的 03）。
    state.scrapeBusy.value = true;
    state.scrapeSnapshot.value = { status: "running", progress: {}, logs: [] };
    requestCapsuleNavigation("task");
    await flush();
    expect(state.activeStep.value).toBe("search");
    expect(state.pipelineResult.value).not.toBeNull();
    expect(scene.getCurrent(identity).selectedJobKey).toBe("boss:kept");
    expect(scene.roundEpoch.value).toBe(epoch);

    // 筛选中 → 落 03 页，现场仍在。
    state.scrapeBusy.value = false;
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenBusy.value = true;
    state.screenSnapshot.value = { status: "running", progress: {}, logs: [] };
    requestCapsuleNavigation("task");
    await flush();
    expect(state.activeStep.value).toBe("screen");
    expect(scene.getCurrent(identity).selectedJobKey).toBe("boss:kept");

    // 筛选暂停 → attention 按卡住的那步落 03 页（不无脑送 02）。
    state.screenBusy.value = false;
    state.screenSnapshot.value = { status: "paused", progress: {}, logs: [] };
    state.pausedRunId.value = "screen-paused-1";
    requestCapsuleNavigation("attention");
    await flush();
    expect(state.activeStep.value).toBe("screen");
    expect(scene.getCurrent(identity).selectedJobKey).toBe("boss:kept");
    expect(scene.roundEpoch.value).toBe(epoch);

    // 跑完 → 落 04 页；任何路径都没有被清成空白上传页。
    state.pausedRunId.value = "";
    state.screenSnapshot.value = { status: "completed", progress: {}, logs: [] };
    requestCapsuleNavigation("results");
    await flush();
    expect(state.activeStep.value).toBe("results");
    expect(state.resultLoaded.value).toBe(true);
    expect(scene.getCurrent(identity).selectedJobKey).toBe("boss:kept");
  });

  it("启动加载占位期间的点击不吞不锁，加载完执行一次有效落点", async () => {
    const profileId = `island-bridge-boot-${Date.now()}-${Math.random()}`;
    const state = useDiscoveryState({ profileId }, () => {});
    state.workflowStateRestored.value = false;
    bridge(state);
    state.resultLoaded.value = true;
    state.pipelineResult.value = { jobs: [{ job_id: "j1" }], dropped: [] };

    requestCapsuleNavigation("results");
    await flush();
    // 占位期间：明确不响应（不落页、不清现场），但这次点击留着。
    expect(state.activeStep.value).toBe("upload");

    state.workflowStateRestored.value = true;
    await flush();
    expect(state.activeStep.value).toBe("results");
    expect(state.pipelineResult.value).not.toBeNull();
  });

  it("新一轮（无活任务、无结果）点灵动岛不越级：落回第一页，不落空页", async () => {
    const profileId = `island-bridge-fresh-${Date.now()}-${Math.random()}`;
    const state = useDiscoveryState({ profileId }, () => {});
    state.workflowStateRestored.value = true;
    bridge(state);
    // 全新一轮：只有第一页可达（第 2 步要等简历就绪）。
    expect(state.enabledSteps.value).toEqual(["upload"]);

    // 出错/暂停通知行的落点、打断行的落点，都是"当前没有活任务"时的猜测；
    // 结果页落点在"这一轮没有结果"时同样不可进——三条都不许越级。
    requestCapsuleNavigation("attention");
    await flush();
    expect(state.activeStep.value).toBe("upload");

    requestCapsuleNavigation("task");
    await flush();
    expect(state.activeStep.value).toBe("upload");

    requestCapsuleNavigation("results");
    await flush();
    expect(state.activeStep.value).toBe("upload");
  });


  it("Flow projection can unlock screen even when legacy platform enabledSteps cannot", async () => {
    const profileId = `island-bridge-flow-${Date.now()}-${Math.random()}`;
    const state = useDiscoveryState({ profileId }, () => {});
    state.workflowStateRestored.value = true;
    state.setFlowReachableSteps(new Set(["search", "screen"]));
    scope = effectScope();
    scope.run(() => useDiscoveryIslandBridge(state));
    requestCapsuleNavigation("task-screen");
    await flush();
    expect(state.activeStep.value).toBe("screen");

    const legacyState = useDiscoveryState({ profileId: `${profileId}-legacy` }, () => {});
    legacyState.workflowStateRestored.value = true;
    scope?.stop();
    scope = effectScope();
    scope.run(() => useDiscoveryIslandBridge(legacyState));
    requestCapsuleNavigation("task-screen");
    await flush();
    expect(legacyState.activeStep.value).not.toBe("screen");
  });

  it("活任务所在的页直接放行：抓取运行中回 02，不被可达清单挡住", async () => {
    const profileId = `island-bridge-live-${Date.now()}-${Math.random()}`;
    const state = useDiscoveryState({ profileId }, () => {});
    state.workflowStateRestored.value = true;
    bridge(state);
    // 真实现场：刷新恢复时简历分析标记可能还没回来（清单里没有第 2 步），
    // 但抓取确实在跑——落点必须是真实进度页 02，而不是被回落成别处。
    expect(state.enabledSteps.value).not.toContain("search");
    state.scrapeBusy.value = true;
    state.scrapeSnapshot.value = { status: "running", progress: {}, logs: [] };

    requestCapsuleNavigation("task");
    await flush();
    expect(state.activeStep.value).toBe("search");
  });

  it("灵动岛与普通导航共享 State 守卫，不采信过期的桥接投影", async () => {
    const profileId = `island-bridge-authority-${Date.now()}-${Math.random()}`;
    const state = useDiscoveryState({ profileId }, () => {});
    state.workflowStateRestored.value = true;
    const navigation = state as typeof state & {
      setFlowReachableSteps: (steps: Set<string> | null) => void;
    };
    navigation.setFlowReachableSteps(new Set(["search", "screen"]));
    scope = effectScope();
    scope.run(() => useDiscoveryIslandBridge(state));

    requestCapsuleNavigation("task-screen");
    await flush();

    expect(state.activeStep.value).toBe("screen");
  });

  it("Flow 终态失败优先于遗留暂停快照，灵动岛落到可恢复的筛选页", async () => {
    const profileId = `island-bridge-flow-failed-${Date.now()}-${Math.random()}`;
    const state = useDiscoveryState({ profileId }, () => {});
    state.workflowStateRestored.value = true;
    state.setFlowReachableSteps(new Set(["search", "screen", "results"]));
    state.pipelineResult.value = {
      flow_id: "flow-result-incomplete",
      flow_tracks: [{
        platform: "boss",
        status: "failed",
        stage: "ai",
        error_code: "flow_result_incomplete",
        message: "AI 筛选未完成，仍有岗位待确认",
        jobs: [{ job_id: "pending-1", platform: "boss", title: "待确认岗位" }],
      }],
      jobs: [{ job_id: "pending-1", platform: "boss", title: "待确认岗位" }],
      dropped: [],
    } as never;
    state.resultLoaded.value = true;
    // 这是刷新后仍留在 state 的旧抓取暂停事实；服务端 latest-running-task
    // 已为空，不能让它覆盖当前 Flow 的终态生命周期。
    state.scrapeSnapshot.value = { status: "paused", progress: {}, logs: [] };
    state.pausedRunId.value = "legacy-paused-scrape";
    bridge(state);

    const capsule = state.roundStatusPayload.value?.capsule;
    expect(capsule?.state).toBe("attention");
    if (capsule?.state !== "attention") throw new Error("expected Flow failure attention capsule");
    expect(capsule.attention.kind).toBe("error");
    expect(capsule.attention.message).not.toContain("任务已暂停");

    requestCapsuleNavigation("attention");
    await flush();

    expect(state.activeStep.value).toBe("screen");
  });
});
