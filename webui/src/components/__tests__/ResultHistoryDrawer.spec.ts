import { mount } from "@vue/test-utils";
import ResultHistoryDrawer from "../ResultHistoryDrawer.vue";
import type { HistoryRoundItem } from "../../composables/resultHistory";

function item(overrides: Partial<HistoryRoundItem> = {}): HistoryRoundItem {
  return {
    run_id: "h1",
    platform: "boss",
    status: "done",
    created_at: "2026-08-11 10:00:00",
    started_at: null,
    finished_at: null,
    total_scraped: 10,
    total_kept: 4,
    total_matched: 3,
    total_dropped: 6,
    pending_count: 1,
    keyword_summary: "Python 后端 / 上海",
    profile_summary_preview: "3年Python后端",
    archived_at: null,
    is_latest: true,
    ...overrides,
  };
}

describe("ResultHistoryDrawer", () => {
  function mountDrawer(overrides: Record<string, unknown> = {}) {
    return mount(ResultHistoryDrawer, {
      props: {
        open: true,
        items: [
          item({ run_id: "h1", platform: "boss", status: "done", is_latest: true }),
          item({ run_id: "h2", platform: "boss", status: "partial", is_latest: false }),
          item({
            run_id: "h3",
            platform: "zhilian",
            status: "interrupted",
            total_kept: 2,
            is_latest: true,
          }),
        ],
        loading: false,
        error: "",
        deleting: false,
        deleteTarget: null,
        ...overrides,
      },
    });
  }

  it("groups rounds by platform, maps machine statuses to Chinese, and marks latest", () => {
    const wrapper = mountDrawer();
    expect(wrapper.findAll('[data-platform="boss"]')).toHaveLength(1);
    expect(wrapper.findAll('[data-platform="zhilian"]')).toHaveLength(1);
    expect(wrapper.get('[data-testid="history-platform-tab-boss"]').text()).toContain("2");
    expect(wrapper.get('[data-testid="history-platform-tab-zhilian"]').text()).toContain("1");
    const rows = wrapper.findAll('[data-testid="history-round-row"]');
    expect(rows).toHaveLength(3);
    expect(rows[0].attributes("role")).toBeUndefined();
    expect(rows[0].attributes("tabindex")).toBeUndefined();
    expect(rows[0].text()).toContain("完成");
    expect(rows[1].text()).toContain("部分结果");
    expect(rows[2].text()).toContain("失败但有 2 个岗位");
    expect(wrapper.findAll('[data-testid="history-latest-badge"]')).toHaveLength(2);
  });

  it("colors the round count parts by status tone", () => {
    const wrapper = mountDrawer();
    const meta = wrapper.find('[data-run-id="h1"] [data-testid="history-round-meta"]');
    const metrics = meta.findAll(".history-metric");
    expect(metrics.map((metric) => metric.attributes("data-tone"))).toEqual(["match", "unsure", "reject"]);
    expect(metrics[0].text()).toContain("匹配 3");
    expect(metrics[1].text()).toContain("待确认 1");
    expect(metrics[2].text()).toContain("剔除 6");
    expect(meta.text()).not.toContain("·");
  });

  it("switches the visible round group with the top platform tabs", async () => {
    const wrapper = mountDrawer();
    expect(wrapper.get('[data-platform="boss"]').attributes("aria-hidden")).toBe("false");
    expect(wrapper.get('[data-platform="zhilian"]').attributes("aria-hidden")).toBe("true");

    await wrapper.get('[data-testid="history-platform-tab-zhilian"]').trigger("click");
    expect(wrapper.get('[data-platform="boss"]').attributes("aria-hidden")).toBe("true");
    expect(wrapper.get('[data-platform="zhilian"]').attributes("aria-hidden")).toBe("false");
  });

  it("emits open-round on row click", async () => {
    const wrapper = mountDrawer();
    await wrapper.get('[data-run-id="h2"]').trigger("click");
    expect(wrapper.emitted("open-round")).toEqual([["h2"]]);
  });

  it("confirms before deleting a round", async () => {
    const wrapper = mountDrawer({ deleteTarget: item({ run_id: "h2", status: "partial" }) });
    await wrapper.get('[data-testid="history-delete-confirm-yes"]').trigger("click");
    expect(wrapper.emitted("delete-round")).toHaveLength(1);
    expect(wrapper.emitted("delete-round")![0]).toEqual([expect.objectContaining({ run_id: "h2" })]);
  });
});
