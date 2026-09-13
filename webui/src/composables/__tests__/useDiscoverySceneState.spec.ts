import { describe, expect, it } from "vitest";
import { useDiscoverySceneState } from "../useDiscoverySceneState";
import type { Platform, SceneIdentity } from "../../types";

const identity = (
  profileId: string,
  runEpoch = "run-1",
  platform: Platform = "boss",
): SceneIdentity => ({
  profileId,
  runEpoch,
  platform,
});

describe("useDiscoverySceneState", () => {
  it("保存并恢复当前页现场，同时按平台隔离", () => {
    const profileId = `scene-current-${Date.now()}-${Math.random()}`;
    const scene = useDiscoverySceneState();

    scene.saveCurrent(identity(profileId), {
      profileInputHeight: 280,
      sortKey: "salary_desc",
      listFilterDraft: { salary: ["15up"], experience: [], degree: [], welfare: [] },
      visibleCount: 42,
      selectedJobKey: "boss:job-1",
      userSelectedDetail: true,
      detailOpen: true,
      jdScrollTop: 128,
      listScrollTop: 64,
    });

    expect(scene.getCurrent(identity(profileId))).toMatchObject({
      profileInputHeight: 280,
      sortKey: "salary_desc",
      listFilterDraft: { salary: ["15up"], experience: [], degree: [], welfare: [] },
      visibleCount: 42,
      selectedJobKey: "boss:job-1",
      jdScrollTop: 128,
      listScrollTop: 64,
    });
    expect(scene.getCurrent(identity(profileId, "run-1", "zhilian"))).toMatchObject({
      profileInputHeight: null,
      sortKey: "default",
      selectedJobKey: null,
    });
  });

  it("归档旧轮次时保留历史现场，但不带入草稿筛选", () => {
    const profileId = `scene-history-${Date.now()}-${Math.random()}`;
    const current = identity(profileId, "run-2", "boss");
    const scene = useDiscoverySceneState();

    scene.saveCurrent(current, {
      selectedJobKey: "boss:old-job",
      listFilterDraft: { salary: ["15up"], experience: [], degree: [], welfare: [] },
      listScrollTop: 90,
    });
    scene.archiveCurrentForNewRound("run-2");

    expect(scene.getHistory("run-2", current)).toMatchObject({
      selectedJobKey: "boss:old-job",
      listScrollTop: 90,
    });
    expect(scene.getHistory("run-2", current).listFilterDraft).toEqual({
      salary: [],
      experience: [],
      degree: [],
      welfare: [],
    });
    expect(scene.getCurrent(identity(profileId, "run-3", "boss"))).toMatchObject({
      selectedJobKey: null,
      listScrollTop: 0,
    });
  });

  it("可写入并从会话存储恢复现场", () => {
    const profileId = `scene-session-${Date.now()}-${Math.random()}`;
    const current = identity(profileId, "run-1", "boss");
    const scene = useDiscoverySceneState();

    scene.saveCurrent(current, { selectedJobKey: "boss:session-job", visibleCount: 30 });
    scene.persistToSession(profileId);

    const restored = useDiscoverySceneState();
    restored.restoreFromSession(profileId);

    expect(restored.getCurrent(current)).toMatchObject({
      selectedJobKey: "boss:session-job",
      visibleCount: 30,
    });
  });

  it("现场变化后立即写入会话存档，不依赖页面卸载", () => {
    const profileId = `scene-immediate-${Date.now()}-${Math.random()}`;
    const current = identity(profileId, "run-immediate", "boss");
    const scene = useDiscoverySceneState();

    scene.saveCurrent(current, { selectedJobKey: "boss:immediate-job", listScrollTop: 88 });

    const raw = sessionStorage.getItem(`career-scout-workflow:${profileId}`);
    expect(raw).not.toBeNull();
    expect(JSON.parse(String(raw)).pageScene.current[`${profileId}::run-immediate::boss`]).toMatchObject({
      selectedJobKey: "boss:immediate-job",
      listScrollTop: 88,
    });
  });

  it("轮次身份跨刷新稳定，开新一轮才换发新身份", () => {
    const profileId = `scene-epoch-${Date.now()}-${Math.random()}`;
    const scene = useDiscoverySceneState();

    const epoch = scene.ensureRoundEpoch(profileId);
    expect(epoch).toBeTruthy();
    // 同一会话重复取回同一值（刷新后重新挂载也走这条）。
    expect(scene.ensureRoundEpoch(profileId)).toBe(epoch);
    expect(useDiscoverySceneState().ensureRoundEpoch(profileId)).toBe(epoch);

    scene.rotateRoundEpoch(profileId);
    expect(scene.roundEpoch.value).not.toBe(epoch);
    expect(scene.ensureRoundEpoch(profileId)).toBe(scene.roundEpoch.value);
  });

  it("开新轮归档：旧轮现场落到结果 run id 下，可作历史轮查看现场接回", () => {
    const profileId = `scene-archive-${Date.now()}-${Math.random()}`;
    const scene = useDiscoverySceneState();
    const epoch = scene.ensureRoundEpoch(profileId);
    const identity: SceneIdentity = { profileId, runEpoch: epoch, platform: "boss" };

    scene.saveCurrent(identity, {
      selectedJobKey: "boss:job-final",
      listScrollTop: 210,
    });
    scene.noteRoundRunId(profileId, epoch, "run-final");
    scene.getCurrent(identity);
    scene.archiveCurrentForNewRound(epoch, "run-final", "boss");

    // 历史轮浏览按结果 run id 取现场 → 接回该轮最后看到的现场。
    expect(scene.getHistory("run-final", identity)).toMatchObject({
      selectedJobKey: "boss:job-final",
      listScrollTop: 210,
    });
    // 新身份（换发后的轮次）没有旧现场。
    expect(scene.getCurrent({ ...identity, runEpoch: scene.rotateRoundEpoch(profileId) })).toMatchObject({
      selectedJobKey: null,
      listScrollTop: 0,
    });
  });

  it("会话存档被清空后不沿用上一次挂载留下的内存现场", () => {
    const profileId = `scene-cleared-${Date.now()}-${Math.random()}`;
    const current = identity(profileId);
    const scene = useDiscoverySceneState();

    scene.saveCurrent(current, { selectedJobKey: "boss:stale-job", visibleCount: 9 });
    scene.persistToSession(profileId);
    sessionStorage.clear();

    expect(scene.getCurrent(current)).toMatchObject({
      selectedJobKey: null,
      visibleCount: 0,
    });
  });
});
