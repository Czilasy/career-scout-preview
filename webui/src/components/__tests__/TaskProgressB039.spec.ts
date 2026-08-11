import { flushPromises, mount } from "@vue/test-utils";
import TaskProgress from "../TaskProgress.vue";

describe("TaskProgress B039 page_done counts", () => {
  it("shows the current combo as running while its pages are being scraped", async () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "running",
          total: 3,
          success_count: 0,
          unstarted_count: 3,
          progress: { stage: "page_done", current: 0, total: 3, page: 2, target_pages: 5, overall_percent: 20 },
        },
      },
    });
    await flushPromises();

    const text = wrapper.get('[data-testid="task-counts"]').text();
    expect(text).toContain("已完成 0 / 3");
    expect(text).toContain("进行中 1");
    expect(text).toContain("未开始 2");
    expect(wrapper.get(".task-stage").text()).toContain("列表抓取");
    expect(wrapper.get('[data-testid="page-progress"]').text()).toContain("第 2 / 5 页");
  });

  it("keeps a later combo running when page_done follows completed combos", async () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "running",
          total: 3,
          progress: { stage: "page_done", current: 1, total: 3, page: 3, target_pages: 3, overall_percent: 40 },
        },
      },
    });
    await flushPromises();

    const text = wrapper.get('[data-testid="task-counts"]').text();
    expect(text).toContain("已完成 1 / 3");
    expect(text).toContain("进行中 1");
    expect(text).toContain("未开始 1");
  });
});
