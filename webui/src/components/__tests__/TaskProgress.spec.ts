import { flushPromises, mount } from "@vue/test-utils";
import TaskProgress from "../TaskProgress.vue";

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    status: "running",
    progress: { overall_percent: 10, current: 0, total: 10 },
    logs: [],
    ...overrides,
  };
}

describe("TaskProgress accessibility announcement", () => {
  it("does not announce percent-only changes", async () => {
    const wrapper = mount(TaskProgress, {
      props: { snapshot: snapshot() as never, kind: "screen" },
    });
    const announcement = () => wrapper.get('[data-testid="task-progress-announcement"]').text();
    const first = announcement();
    await wrapper.setProps({
      snapshot: snapshot({ progress: { overall_percent: 22, current: 0, total: 10 } }) as never,
    });
    expect(announcement()).toBe(first);
    wrapper.unmount();
  });

  it("announces stage and status changes once", async () => {
    const wrapper = mount(TaskProgress, {
      props: { snapshot: snapshot() as never, kind: "screen" },
    });
    const announcement = () => wrapper.get('[data-testid="task-progress-announcement"]').text();
    const first = announcement();

    await wrapper.setProps({
      snapshot: snapshot({ progress: { overall_percent: 22, stage: "ai_rough", current: 0, total: 10 } }) as never,
    });
    expect(announcement()).not.toBe(first);
    expect(announcement()).toContain("AI 粗筛");

    await wrapper.setProps({
      snapshot: snapshot({ status: "completed", progress: { overall_percent: 100 } }) as never,
    });
    expect(announcement()).toContain("已完成");
    wrapper.unmount();
  });
});

describe("TaskProgress diagnostics", () => {
  it("shows inline Chinese reason plus red error field for failed state", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "failed",
          error: "boom",
          pause_info: { error_code: "internal_error", error_reason: "boom" },
        }) as never,
        kind: "screen",
      },
    });
    expect(wrapper.get("[data-testid='pause-reason']").text()).toContain("boom · internal_error");
    expect(wrapper.get("[data-testid='error-field']").text()).toContain("internal_error");
    expect(wrapper.find("[data-testid='task-diagnostics']").exists()).toBe(false);
    expect(wrapper.find("[data-testid='copy-diagnostics']").exists()).toBe(false);
    wrapper.unmount();
  });

  it("shows inline reason plus code for paused state", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "paused",
          error: "captcha", pause_info: { error_code: "captcha_required", error_reason: "触发验证码" },
        }) as never,
        kind: "screen",
      },
    });
    expect(wrapper.get("[data-testid='pause-reason']").text()).toContain("触发验证码 · captcha_required");
    expect(wrapper.find("[data-testid='copy-diagnostics']").exists()).toBe(false);
    wrapper.unmount();
  });

  it("omits empty error field when no code is present", () => {
    const wrapper = mount(TaskProgress, {
      props: { snapshot: snapshot({ status: "failed", error: "boom" }) as never, kind: "screen" },
    });
    expect(wrapper.get("[data-testid='pause-reason']").text()).toBe("boom");
    expect(wrapper.find("[data-testid='error-field']").exists()).toBe(false);
    wrapper.unmount();
  });
});

describe("TaskProgress 033 V2 integrity", () => {
  const integrity = (conclusion: string, reason = "证据不足") => ({
    conclusion, label: conclusion, evidence_complete: conclusion === "succeeded",
    primary_reason: reason, recommendation: "建议重新执行", revision: 2,
  });

  it.each([
    ["succeeded", "完整成功"],
    ["empty", "已完成，没有找到岗位"],
    ["partial", "部分完成，部分结果可能缺失"],
    ["failed", "执行失败"],
    ["unverifiable", "无法确认是否完成"],
    ["interrupted", "任务已中断"],
  ])("uses the whitebox label for %s", (conclusion, label) => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "completed",
          integrity: integrity(conclusion, "主要原因"),
        }) as never,
        kind: "screen",
      },
    });
    expect(wrapper.get(".task-status").text()).toContain(label);
    expect(wrapper.get(".task-progress").attributes("data-integrity")).toBe(conclusion);
    if (["partial", "unverifiable"].includes(conclusion)) {
      expect(wrapper.get(".task-status").attributes("data-status")).not.toBe("completed");
    }
    wrapper.unmount();
  });

  it("无法确认时同时显示重新执行建议", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "completed",
          integrity: integrity("unverifiable", "缺少完成证据"),
        }) as never,
        kind: "screen",
      },
    });
    expect(wrapper.get('[data-testid="pause-reason"]').text()).toContain("建议重新执行");
    wrapper.unmount();
  });
});

describe("TaskProgress elapsed time: hours and pause-excluded duration", () => {
  afterEach(() => vi.useRealTimers());

  it("formats durations over one hour as X小时Y分Z秒", () => {
    // 57875s = 16h 4m 35s；旧实现会错误显示成 964分35秒。
    const finished = 1_000 + 57_875_000;
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "completed",
          progress: { stage: "done", overall_percent: 100 },
          started_at: 1_000,
          finished_at: finished,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 16小时4分35秒");
    wrapper.unmount();
  });

  it("shows the frozen cumulative elapsed while paused (paused time excluded)", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-01T00:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        // active_elapsed_ms=90s：实际运行 90 秒；started_at 到 now 为 10 分钟，
        // 说明 8.5 分钟处于暂停，暂停时长不得计入。
        snapshot: snapshot({
          status: "paused",
          progress: { stage: "fetch_jd", overall_percent: 40 },
          started_at: baseTime - 600_000,
          active_elapsed_ms: 90_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 1分30秒");
    // 暂停状态时间流逝不得回流到"已用"
    vi.advanceTimersByTime(30_000);
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 1分30秒");
    wrapper.unmount();
  });

  it("running shows cumulative + current segment via active_elapsed_ms", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-01T01:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "running",
          progress: { stage: "fetch_jd", overall_percent: 50 },
          started_at: baseTime - 600_000,
          active_elapsed_ms: 90_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分30秒");
    vi.advanceTimersByTime(2_000);
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分32秒");
    wrapper.unmount();
  });

  it("rebases active_elapsed_ms when a paused task resumes", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-01T02:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "paused",
          progress: { stage: "fetch_jd", overall_percent: 40 },
          started_at: baseTime - 600_000,
          active_elapsed_ms: 90_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 1分30秒");

    // 续跑：后端 active_elapsed_ms 从定格值继续增长（暂停的 5s 不计入）
    vi.setSystemTime(baseTime + 5_000);
    await wrapper.setProps({
      snapshot: snapshot({
        status: "running",
        progress: { stage: "fetch_jd", overall_percent: 45 },
        started_at: baseTime - 600_000,
        active_elapsed_ms: 95_000,
      }) as never,
    });
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分35秒");
    vi.advanceTimersByTime(2_000);
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分37秒");
    wrapper.unmount();
  });

  it("uses the fresh active_elapsed_ms baseline on each poll while running", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-01T03:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "running",
          progress: { stage: "fetch_jd", overall_percent: 50 },
          started_at: baseTime - 600_000,
          active_elapsed_ms: 90_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分30秒");

    // 下一轮轮询：后端按响应时刻重新计算，前端重置基准后不得跳变/重复计段
    vi.setSystemTime(baseTime + 2_000);
    await wrapper.setProps({
      snapshot: snapshot({
        status: "running",
        progress: { stage: "fetch_jd", overall_percent: 50 },
        started_at: baseTime - 600_000,
        active_elapsed_ms: 93_000,
      }) as never,
    });
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分33秒");
    vi.advanceTimersByTime(1_000);
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分34秒");
    wrapper.unmount();
  });

  it("falls back to started_at delta when active_elapsed_ms is absent", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "completed",
          progress: { stage: "done", overall_percent: 100 },
          started_at: 1_000,
          finished_at: 61_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 1分00秒");
    wrapper.unmount();
  });
});
