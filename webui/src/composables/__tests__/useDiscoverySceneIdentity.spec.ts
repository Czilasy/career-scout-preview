import { nextTick } from "vue";
import { beforeEach, describe, expect, it } from "vitest";

import { useDiscoverySceneState } from "../useDiscoverySceneState";
import { useDiscoverySceneIdentity } from "../useDiscoverySceneIdentity";
import { useDiscoveryState } from "../useDiscoveryState";

function setup(profileId: string) {
  const state = useDiscoveryState({ profileId }, () => {});
  const { identity } = useDiscoverySceneIdentity(state, { profileId });
  return { state, identity, scene: useDiscoverySceneState() };
}

describe("useDiscoverySceneIdentity", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("同一轮从上传/抓取/筛选到结果完成期间轮次身份不变", () => {
    const { state, identity } = setup(`scene-identity-stable-${Date.now()}-${Math.random()}`);
    const epoch = identity.value.runEpoch;
    expect(epoch).toBeTruthy();

    // 旧实现用 task_id / 结果 run id 当身份：下面每一步都会把它换成新键，
    // 组件随后按"新身份"恢复成默认空现场。现在身份必须原地不动。
    state.scrapeTaskId.value = "scrape-task-1";
    expect(identity.value.runEpoch).toBe(epoch);
    state.screenTaskId.value = "screen-task-1";
    expect(identity.value.runEpoch).toBe(epoch);
    state.pipelineResultRunId.value = "result-run-1";
    expect(identity.value.runEpoch).toBe(epoch);

    // 任务 id / 结果 run id 只登记为该轮的历史查看键，不充当轮次身份。
    expect(identity.value.runEpoch).not.toContain("scrape-task-1");
    expect(identity.value.runEpoch).not.toContain("result-run-1");
    expect(state.pipelineResultRunId.value).toBe("result-run-1");
  });

  it("结果 run id 登记进现场存档（开新轮时给旧轮归档用）", async () => {
    const profileId = `scene-identity-runid-${Date.now()}-${Math.random()}`;
    const { state, scene } = setup(profileId);
    state.pipelineResultRunId.value = "run-abc";
    await nextTick();
    expect(scene.resolveRoundRunId(profileId, scene.roundEpoch.value)).toBe("run-abc");
  });

  it("刷新（重新挂载）后接回同一轮次身份", () => {
    const profileId = `scene-identity-refresh-${Date.now()}-${Math.random()}`;
    const first = setup(profileId).identity.value.runEpoch;
    const second = setup(profileId).identity.value.runEpoch;
    expect(second).toBe(first);
  });

  it("开新一轮换新身份；切平台只换平台段、轮次身份不变", () => {
    const profileId = `scene-identity-rotate-${Date.now()}-${Math.random()}`;
    const { state, identity, scene } = setup(profileId);
    const epoch = identity.value.runEpoch;

    state.platformState.setDraftPlatform("zhilian");
    state.draftPlatform.value = "zhilian";
    expect(identity.value.platform).toBe("zhilian");
    expect(identity.value.runEpoch).toBe(epoch);

    scene.rotateRoundEpoch(profileId);
    expect(identity.value.runEpoch).not.toBe(epoch);
  });
});
