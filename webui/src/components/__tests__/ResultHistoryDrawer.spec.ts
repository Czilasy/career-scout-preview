import { readFileSync } from "node:fs";
import path from "node:path";
import { mount } from "@vue/test-utils";
import ResultHistoryDrawer from "../ResultHistoryDrawer.vue";
import type { HistoryRoundItem } from "../../composables/resultHistory";
import type { HistoryRoundDetail } from "../../composables/resultHistory";

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
    mismatch_count: 2,
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

  it("keeps latest badge immediately after the time", () => {
    const source = readFileSync(path.join(__dirname, "../../components/ResultHistoryDrawer.vue"), "utf8");
    const head = source.match(/\.history-round-head\s*\{[^}]*\}/s)?.[0] || "";
    expect(head).toContain("justify-content: flex-start");
    expect(head).not.toContain("space-between");
  });

  it("colors the round count parts by status tone", () => {
    const wrapper = mountDrawer();
    const meta = wrapper.find('[data-run-id="h1"] [data-testid="history-round-meta"]');
    const metrics = meta.findAll(".history-metric");
    expect(metrics.map((metric) => metric.attributes("data-tone"))).toEqual(["match", "mismatch", "unsure", "reject"]);
    expect(metrics[0].text()).toContain("匹配 3");
    expect(metrics[1].text()).toContain("不匹配 2");
    expect(metrics[2].text()).toContain("待确认 1");
    expect(metrics[3].text()).toContain("剔除 6");
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

  it("shows the full-row glass confirm with icon-only actions", () => {
    const wrapper = mountDrawer({ deleteTarget: item({ run_id: "h2" }) });
    const confirm = wrapper.get('[data-testid="history-delete-confirm"]');
    expect(confirm.text()).toContain("确认删除");
    expect(confirm.text()).not.toContain("删除后保留任务日志");
    expect(confirm.get('[data-testid="history-delete-confirm-yes"]').find("svg").exists()).toBe(true);
    expect(confirm.get('[data-testid="history-delete-confirm-no"]').find("svg").exists()).toBe(true);
  });

  it("does not open a round while the delete confirm overlay is visible", async () => {
    const wrapper = mountDrawer({ deleteTarget: item({ run_id: "h2" }) });
    await wrapper.get('[data-testid="history-delete-confirm"]').trigger("click");
    expect(wrapper.emitted("open-round")).toBeUndefined();
  });

  it("cancels delete from the x button", async () => {
    const wrapper = mountDrawer({ deleteTarget: item({ run_id: "h2" }) });
    await wrapper.get('[data-testid="history-delete-confirm-no"]').trigger("click");
    expect(wrapper.emitted("cancel-delete")).toHaveLength(1);
  });
  it("does not render round location summary", () => {
    const detail = {
      ok: true,
      has_result: true,
      source_run_id: "h1",
      platform: "boss",
      status: "done",
      script_params: {
        locations: [{
          platform: "boss",
          city_name: "上海",
          city_code: "101020100",
          district_name: "浦东新区",
          district_code: "310115",
        }],
      },
      result: { jobs: [] },
    } as HistoryRoundDetail;
    const wrapper = mountDrawer({ detail });
    expect(wrapper.find('[data-testid="history-detail-location"]').exists()).toBe(false);
  });
});
