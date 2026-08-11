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
