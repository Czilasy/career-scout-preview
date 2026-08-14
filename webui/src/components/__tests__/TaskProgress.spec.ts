import { mount } from "@vue/test-utils";
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
