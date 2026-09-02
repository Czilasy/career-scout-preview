// 037 提醒角标回退测试：单源 = 服务端 count total；seq 守卫；99+ 截断；
// profile 切换清零重取；请求失败保留上次已知值。mock 掉 jobFeedback 网络层，
// 只测 composable 自身语义（App 集成路径由 App.spec.ts 覆盖）。
import { describe, expect, it, beforeEach, vi } from "vitest";
import { ref } from "vue";
import { useReminderBadge } from "../useReminderBadge";
import { getJobReminderCount, type JobReminderCountResponse } from "../../jobFeedback";

vi.mock("../../jobFeedback", () => ({
  getJobReminderCount: vi.fn(),
}));

const mockCount = vi.mocked(getJobReminderCount);

function ok(total: number): Promise<JobReminderCountResponse> {
  return Promise.resolve({ ok: true, profile_id: "p1", threshold_hours: 720, total });
}

describe("useReminderBadge", () => {
  beforeEach(() => {
    mockCount.mockReset();
  });

  it("refresh：把服务端 total 写入 reminderTotal", async () => {
    mockCount.mockReturnValue(ok(3));
    const profileId = ref("p1");
    const badge = useReminderBadge(profileId);
    await badge.refreshReminderCount();
    expect(mockCount).toHaveBeenCalledWith("p1");
    expect(badge.reminderTotal.value).toBe(3);
    expect(badge.badgeText.value).toBe("3");
  });

  it("profileId 为空：不请求，总数归零", async () => {
    const profileId = ref<string | null>(null);
    const badge = useReminderBadge(profileId);
    await badge.refreshReminderCount();
    expect(mockCount).not.toHaveBeenCalled();
    expect(badge.reminderTotal.value).toBe(0);
  });

  it("seq 守卫：切换 profile 后，旧 profile 的晚到响应不得覆盖新值", async () => {
    let resolveP1: (v: JobReminderCountResponse) => void = () => {};
    // p1 的请求挂起；p2 一律返回 4（内部 watch 与显式 refresh 都会取）。
    mockCount.mockImplementation((id: string) => {
      if (id === "p1") return new Promise((r) => { resolveP1 = r; });
      return ok(4);
    });

    const profileId = ref("p1");
    const badge = useReminderBadge(profileId);
    void badge.refreshReminderCount(); // p1 的请求挂起（seq=1）

    profileId.value = "p2"; // 内部 watch 清零并以新 seq 请求 p2
    await Promise.resolve();
    await Promise.resolve();
    expect(badge.reminderTotal.value).toBe(4);

    resolveP1({ ok: true, profile_id: "p1", threshold_hours: 720, total: 99 }); // p1 晚到：seq 已过期，不得覆盖
    await Promise.resolve();
    await Promise.resolve();
    expect(badge.reminderTotal.value).toBe(4);
  });

  it("profile 切换：先清零再取新 profile 的 count", async () => {
    mockCount.mockReturnValue(ok(2));
    const profileId = ref("p1");
    const badge = useReminderBadge(profileId);
    await badge.refreshReminderCount();
    expect(badge.reminderTotal.value).toBe(2);

    mockCount.mockReturnValue(ok(7));
    profileId.value = "p2";
    await Promise.resolve(); // 触发内部 watch 的 refresh
    await Promise.resolve();
    expect(mockCount).toHaveBeenLastCalledWith("p2");
    expect(badge.reminderTotal.value).toBe(7);
  });

  it("请求失败：保留上次已知数量（FR-019）", async () => {
    mockCount.mockReturnValue(ok(5));
    const profileId = ref("p1");
    const badge = useReminderBadge(profileId);
    await badge.refreshReminderCount();
    expect(badge.reminderTotal.value).toBe(5);

    mockCount.mockRejectedValue(new Error("offline"));
    await badge.refreshReminderCount();
    expect(badge.reminderTotal.value).toBe(5);
  });

  it("badgeText：99+ 截断，真实数量留在 aria-label", async () => {
    mockCount.mockReturnValue(ok(137));
    const profileId = ref("p1");
    const badge = useReminderBadge(profileId);
    await badge.refreshReminderCount();
    expect(badge.badgeText.value).toBe("99+");
    expect(badge.ariaLabel.value).toContain("137");
  });

  it("ariaLabel：0 → 查看提醒；>0 → 查看投递提醒，共 N 个逾期岗位", async () => {
    mockCount.mockReturnValue(ok(3));
    const profileId = ref("p1");
    const badge = useReminderBadge(profileId);
    await badge.refreshReminderCount();
    expect(badge.ariaLabel.value).toBe("查看投递提醒，共 3 个逾期岗位");

    mockCount.mockReturnValue(ok(0));
    await badge.refreshReminderCount();
    expect(badge.ariaLabel.value).toBe("查看提醒");
  });
});
