import { readFileSync } from "node:fs";
import path from "node:path";
import { mount } from "@vue/test-utils";
import ResultHistoryDrawer from "../ResultHistoryDrawer.vue";
import type { FlowHistoryItem, HistoryRoundItem } from "../../composables/resultHistory";
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
  async function mountDrawer(overrides: Record<string, unknown> = {}) {
    const wrapper = mount(ResultHistoryDrawer, {
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
    // 明确进入夹具所属的历史页；产品默认页仍是聚合，由独立用例检查。
    const flows = (overrides.flowItems || []) as FlowHistoryItem[];
    const rounds = (overrides.items || [item()]) as HistoryRoundItem[];
    const platform = flows.find((flow) => !flow.legacy)?.selection
      || rounds[0]?.platform || "boss";
    const tab = platform === "all" ? "aggregate" : platform;
    await wrapper.get(`[data-testid="history-platform-tab-${tab}"]`).trigger("click");
    return wrapper;
  }

  it.each(["queued", "running", "paused"])("disables deleting unfinished legacy and Flow rounds (%s)", async (status) => {
    const legacy = await mountDrawer({ items: [item({ status })] });
    const legacyDelete = legacy.get('[data-testid="history-delete-trigger"]');
    expect(legacyDelete.attributes("disabled")).toBeDefined();
    expect(legacyDelete.attributes("title")).toContain("请先结束或取消流程");
    await legacyDelete.trigger("click");
    expect(legacy.emitted("confirm-delete")).toBeUndefined();
    const flow = await mountDrawer({
      items: [],
      flowItems: [{ flow_id: "busy-flow", profile_id: "profile", selection: "boss", status,
        created_at: "2026-10-04", updated_at: "2026-10-04", legacy: false, screened_count: 0,
        tracks: [{ id: "busy-track", platform: "boss", status, jobs: [], screened_count: 0 }],
      }],
    });
    const flowDelete = flow.get('[data-testid="history-delete-trigger"]');
    expect(flowDelete.attributes("disabled")).toBeDefined();
    expect(flowDelete.attributes("title")).toContain("请先结束或取消流程");
    await flowDelete.trigger("click");
    expect(flow.emitted("confirm-delete")).toBeUndefined();
  });

  it("opens on the aggregate tab before explicit platform navigation", () => {
    const wrapper = mount(ResultHistoryDrawer, {
      props: { open: true, items: [item()], loading: false, error: "", deleting: false, deleteTarget: null },
    });
    expect(wrapper.get('[data-testid="history-platform-tab-aggregate"]').attributes("aria-selected")).toBe("true");
    expect(wrapper.get('[data-testid="history-empty"]').text()).toBe("暂无聚合流程");
    expect(wrapper.find('[data-testid="history-round-row"]').exists()).toBe(false);
  });

  it("groups rounds by platform, maps machine statuses to Chinese, and marks latest", async () => {
    const wrapper = await mountDrawer();
    expect(wrapper.findAll('[data-platform="boss"]')).toHaveLength(1);
    expect(wrapper.findAll('[data-platform="zhilian"]')).toHaveLength(1);
    expect(wrapper.get('[data-testid="history-platform-tab-boss"]').text()).toContain("2");
    expect(wrapper.get('[data-testid="history-platform-tab-zhilian"]').text()).toContain("1");
    const rows = wrapper.findAll('[data-testid="history-round-row"]');
    expect(rows).toHaveLength(3);
    expect(rows[0].text()).toContain("完成");
    expect(rows[1].text()).toContain("部分结果");
    // 017-US3: "失败但有 N 个岗位" 文案永久消失；未知状态不渲染标签
    expect(rows[2].text()).not.toContain("失败但有");
    expect(wrapper.get('[data-run-id="h3"] .history-round-status').text()).toBe("");
    expect(wrapper.findAll('[data-testid="history-latest-badge"]')).toHaveLength(2);
  });

  it("renders one outer Flow card with two platform tracks", async () => {
    const flow: FlowHistoryItem = {
      flow_id: "flow-history",
      profile_id: "profile-flow",
      selection: "all",
      status: "failed",
      created_at: "2026-08-11 10:00:00",
      updated_at: "2026-08-11 10:05:00",
      legacy: false,
      screened_count: 1,
      tracks: [
        { platform: "boss", status: "done", result_run_id: "boss-result", jobs: [{ job_id: "b1" }], screened_count: 1 },
        { platform: "zhilian", status: "failed", jobs: [], message: "未完成 AI 筛选", screened_count: 0 },
      ],
    };
    const wrapper = await mountDrawer({ items: [], flowItems: [flow] });

    expect(wrapper.find('[data-testid="history-flow-card"]').exists()).toBe(true);
    expect(wrapper.findAll('[data-testid="history-flow-track"]')).toHaveLength(2);
    expect(wrapper.text()).toContain("未完成 AI 筛选");
  });

  it.each(["boss", "zhilian"] as const)("uses compact rows for new and legacy %s rounds without a flow shell", async (platform) => {
    const recent = item({ run_id: "single-result", platform, scrape_task_id: "single-scrape", finished_at: "2026-10-02 04:03:00" });
    const older = item({ run_id: "older-result", platform, is_latest: false });
    const wrapper = await mountDrawer({
      items: [recent, older],
      flowItems: [{
        flow_id: "single-flow", profile_id: "profile", selection: platform, status: "done", legacy: false,
        updated_at: "2026-10-02 04:05:00", tracks: [{ platform, status: "done", result_run_id: recent.run_id, scrape_run_id: "single-scrape" }],
      }],
    });
    expect(wrapper.find('[data-testid="history-flow-card"]').exists()).toBe(false);
    expect(wrapper.find(".history-flow-head").exists()).toBe(false);
    const rows = wrapper.findAll('[data-testid="history-round-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].classes()).not.toContain("history-flow-track-line");
    expect(rows[0].get(".history-round-time").text()).toBe("2026-10-02 04:03");
    expect(rows[0].text()).toContain("匹配 3");
    await rows[0].trigger("click");
    await rows[0].get('[data-testid="history-log-trigger"]').trigger("click");
    await rows[0].get('[data-testid="history-delete-trigger"]').trigger("click");
    expect(wrapper.emitted("open-round")).toEqual([[recent.run_id]]);
    expect(wrapper.emitted("view-log")).toEqual([[recent]]);
    expect(wrapper.emitted("confirm-delete")).toEqual([[recent]]);
    await wrapper.setProps({ deleteTarget: recent });
    await wrapper.get('[data-testid="history-delete-confirm-yes"]').trigger("click");
    expect(wrapper.emitted("delete-round")).toEqual([[recent]]);
  });

  it("only opens a Flow track when a result snapshot id exists", async () => {
    const flow: FlowHistoryItem = {
      flow_id: "flow-track-identities",
      profile_id: "profile-flow",
      selection: "all",
      status: "failed",
      created_at: "2026-08-11 10:00:00",
      updated_at: "2026-08-11 10:05:00",
      legacy: false,
      screened_count: 0,
      tracks: [
        {
          platform: "boss",
          status: "failed",
          screen_run_id: "screen-only",
          scrape_run_id: "scrape-only",
          jobs: [],
          screened_count: 0,
        },
        {
          platform: "zhilian",
          status: "done",
          result_run_id: "result-snapshot",
          screen_run_id: "screen-with-result",
          jobs: [{ job_id: "z1" }],
          screened_count: 1,
        },
      ],
    };
    const wrapper = await mountDrawer({ items: [], flowItems: [flow] });

    // 详情入口只认结果轮；抓取任务线在、日志入口就必须在（见下方删除轮回归）。
    expect(wrapper.find(
      '[data-testid="history-flow-track"][data-platform="boss"] .history-flow-track-line[role=button]',
    ).exists()).toBe(false);
    expect(wrapper.get('[data-testid="history-flow-track"][data-platform="boss"] .history-flow-track-line--static').text()).toContain("失败");
    await wrapper.get('[data-testid="history-flow-track"][data-platform="zhilian"] .history-flow-track-line[role=button]').trigger("click");
    expect(wrapper.emitted("open-round")).toEqual([["result-snapshot"]]);
  });

  // 删除结果轮 = DELETE FROM screening_runs：外键把 flow_tracks.result_run_id 清成
  // NULL，流程行与抓取任务线都还在。三个入口各自依据自己的事实判定，不得一起
  // 消失（日志仍要能看、没有可删轮次就不再给删除），状态列更不得继续谎称「完成」。
  it("keeps the log entry and stops claiming 完成 after a Flow track's result round is deleted", async () => {
    const flow: FlowHistoryItem = {
      flow_id: "flow-after-delete",
      profile_id: "profile-after-delete",
      selection: "all",
      status: "done",
      created_at: "2026-08-12 09:00:00",
      updated_at: "2026-08-12 09:20:00",
      legacy: false,
      tracks: [
        { id: "boss-track", platform: "boss", status: "done", result_run_id: null, scrape_run_id: "boss-scrape", jobs: [], dropped: [] },
        { id: "zhilian-track", platform: "zhilian", status: "done", result_run_id: null, scrape_run_id: "zhilian-scrape", jobs: [], dropped: [] },
      ],
    };
    const wrapper = await mountDrawer({ items: [], flowItems: [flow] });

    const card = wrapper.get('[data-testid="history-flow-card"]');
    // 两条轨道都没有结果轮：整卡不出现任何「完成」字样。
    expect(card.text()).not.toContain("完成");
    expect(card.get(".history-round-status").text()).toBe("无结果");

    const cases = [
      ["boss", "boss-scrape"],
      ["zhilian", "zhilian-scrape"],
    ] as const;
    for (const [platform, scrapeTaskId] of cases) {
      const track = wrapper.get(`[data-testid="history-flow-track"][data-platform="${platform}"]`);
      expect(track.get('[data-testid="history-flow-track-status"]').text()).toBe("无结果");
      expect(track.find('[data-testid="history-log-trigger"]').exists()).toBe(true);
      await track.get('[data-testid="history-log-trigger"]').trigger("click");
      expect(wrapper.emitted("view-log")?.at(-1)).toEqual([expect.objectContaining({
        scrape_task_id: scrapeTaskId,
      })]);
      // 旧空壳也能手动删除，详情入口仍然只认真实结果轮。
      await track.get('[data-testid="history-delete-trigger"]').trigger("click");
      expect(wrapper.emitted("confirm-delete")?.at(-1)).toEqual([expect.objectContaining({ run_id: `${platform}-track` })]);
      expect(track.find(".history-flow-track-line[role=button]").exists()).toBe(false);
    }
  });

  // 结果轮被删、但抓取台账还在的轨道：如实说明「已抓取，未筛选」，不得说完成。
  it("labels a Flow track without a result round but with scraped jobs as unscreened", async () => {
    const flow: FlowHistoryItem = {
      flow_id: "flow-deleted-but-scraped",
      profile_id: "profile-deleted-but-scraped",
      selection: "boss",
      status: "done",
      created_at: "2026-08-12 09:00:00",
      updated_at: "2026-08-12 09:20:00",
      legacy: false,
      tracks: [{
        platform: "boss", status: "done", result_run_id: null, scrape_run_id: "boss-scrape",
        jobs: [{ job_id: "b1" }], dropped: [], message: "未完成 AI 筛选",
      }],
    };
    const wrapper = await mountDrawer({ items: [], flowItems: [flow] });
    const card = wrapper.get('[data-testid="history-round-row"]');
    expect(wrapper.find('[data-testid="history-flow-card"]').exists()).toBe(false);
    expect(card.get('[data-testid="history-flow-track-status"]').text()).toBe("已抓取，未筛选");
    expect(card.find('[data-testid="history-log-trigger"]').exists()).toBe(true);
  });

  // 界面文案口径属树干统一规则：后端状态枚举不得以任何形式出现在界面上。
  it("maps every Flow status the drawer can receive to Chinese copy without echoing enums", async () => {
    const cases: Array<[string, string]> = [
      ["interrupted", "已中断"],
      ["stopped", "已停止"],
      ["cancelled", "已停止"],
      ["queued", "排队中"],
      ["running", "进行中"],
      ["paused", "已暂停"],
      ["failed", "失败"],
      ["empty", "无结果"],
      ["unknown", "状态未知"],
      ["some_future_machine_value", "状态未知"],
    ];
    const flows: FlowHistoryItem[] = cases.map(([status]) => ({
      flow_id: `flow-status-${status}`,
      profile_id: "profile-status",
      selection: "boss",
      status,
      created_at: "2026-08-12 09:00:00",
      updated_at: "2026-08-12 09:20:00",
      legacy: false,
      tracks: [{
        platform: "boss", status, result_run_id: `result-${status}`,
        scrape_run_id: `scrape-${status}`, jobs: [{ job_id: "j1" }], dropped: [],
      }],
    }));
    const wrapper = await mountDrawer({ items: [], flowItems: flows });
    const cards = wrapper.findAll('[data-testid="history-round-row"]');
    expect(cards).toHaveLength(cases.length);
    for (const [status, label] of cases) {
      const card = wrapper.get(`[data-testid="history-round-row"][data-run-id="result-${status}"]`);
      expect(card.text().toLowerCase()).not.toContain(status);
      expect(card.get(".history-round-status").text()).toBe(label);
      expect(card.get('[data-testid="history-flow-track-status"]').text()).toBe(label);
    }
  });

  // B096 返修：后端把每一条旧结果轮都合成成一个 legacy Flow，flowItems 恒非空。
  // Flow 卡片视图不得因此顶掉平铺轮次视图——043 的「删除轮次」「查看运行日志」
  // 与计数明细必须留在旧轮上（回归：旧能力整块在界面上消失、父级接线成死线）。
  it("keeps legacy rounds on the flat round list with delete and log entries", async () => {
    const legacyFlow: FlowHistoryItem = {
      flow_id: "h1",
      profile_id: "profile-legacy",
      selection: "boss",
      status: "done",
      created_at: "2026-08-11 10:00:00",
      updated_at: "2026-08-11 10:05:00",
      legacy: true,
      tracks: [{ platform: "boss", status: "done", result_run_id: "h1", screen_run_id: "h1", jobs: [] }],
    };
    const wrapper = await mountDrawer({
      items: [
        item({ run_id: "h1", scrape_task_id: "scrape-h1" }),
        item({ run_id: "h2", status: "partial", is_latest: false, scrape_task_id: "scrape-h2" }),
      ],
      flowItems: [legacyFlow],
    });

    expect(wrapper.findAll('[data-testid="history-flow-card"]')).toHaveLength(0);
    const rows = wrapper.findAll('[data-testid="history-round-row"]');
    expect(rows).toHaveLength(2);
    expect(wrapper.get('[data-run-id="h1"] [data-testid="history-round-total"]').text()).toContain("共 10 个岗位");
    expect(wrapper.get('[data-run-id="h1"] [data-testid="history-round-meta"]').text()).toContain("匹配 3");
    expect(wrapper.get('[data-run-id="h1"] .history-round-keyword').text()).toContain("Python 后端");
    expect(wrapper.get(".history-drawer-total").text()).toContain("共 2 轮");

    await wrapper.get('[data-run-id="h1"] [data-testid="history-log-trigger"]').trigger("click");
    expect(wrapper.emitted("view-log")).toEqual([[expect.objectContaining({ run_id: "h1" })]]);
    await wrapper.get('[data-run-id="h2"] [data-testid="history-delete-trigger"]').trigger("click");
    expect(wrapper.emitted("confirm-delete")).toEqual([[expect.objectContaining({ run_id: "h2" })]]);
  });

  // Flow 卡片只承载有 durable flow 身份的真实 Flow；旧轮继续走平铺列表，
  // 聚合页展示组合流程；平台页分别展示该平台的结果轮，保留旧轮动作。
  it("separates aggregate Flow cards and platform rounds without duplicating rows", async () => {
    const realFlow: FlowHistoryItem = {
      flow_id: "flow-1",
      profile_id: "profile-mixed",
      selection: "all",
      status: "done",
      created_at: "2026-08-12 09:00:00",
      updated_at: "2026-08-12 09:20:00",
      legacy: false,
      tracks: [
        { platform: "boss", status: "done", result_run_id: "flow-round", scrape_run_id: "flow-scrape", jobs: [{ job_id: "b1" }] },
        { platform: "zhilian", status: "done", result_run_id: "flow-round-z", jobs: [] },
      ],
    };
    const wrapper = await mountDrawer({
      items: [
        item({ run_id: "flow-round", scrape_task_id: "flow-scrape", is_latest: false }),
        item({ run_id: "legacy-round", scrape_task_id: "scrape-legacy" }),
      ],
      flowItems: [realFlow],
    });

    expect(wrapper.findAll('[data-testid="history-flow-card"]')).toHaveLength(1);
    // Flow 卡片也要给出这一轮的时间：平铺行不再覆盖这些轮次后，时间是
    // 用户分辨「哪一轮是刚才那次」的唯一线索。
    expect(wrapper.get('[data-testid="history-flow-card"]').text()).toContain("2026-08-12 09:20");
    expect(wrapper.findAll('[data-testid="history-round-row"]')).toHaveLength(0);
    expect(wrapper.text()).toContain("共 1 个流程");
    await wrapper.get('[data-testid="history-platform-tab-boss"]').trigger("click");
    expect(wrapper.findAll('[data-testid="history-flow-card"]')).toHaveLength(0);
    const rows = wrapper.findAll('[data-testid="history-round-row"]');
    expect(rows.map((row) => row.attributes("data-run-id"))).toEqual(["flow-round", "legacy-round"]);
    expect(wrapper.text()).toContain("共 2 轮");
    await wrapper.get('[data-testid="history-platform-tab-zhilian"]').trigger("click");
    expect(wrapper.get('[data-testid="history-platform-tab-zhilian"]').attributes("aria-selected")).toBe("true");
    await wrapper.get('[data-testid="history-platform-tab-aggregate"]').trigger("click");
    expect(wrapper.findAll('[data-testid="history-flow-card"]')).toHaveLength(1);
    expect(wrapper.findAll('[data-testid="history-round-row"]')).toHaveLength(0);
  });

  // 真实 Flow 的结果轮同样是可删除、可看日志的轮次：卡片内必须保留等价入口，
  // 不能因为换成分层卡片就把 043 的能力弱化掉。
  it("keeps delete and log entries on a Flow track that owns a result round", async () => {
    const realFlow: FlowHistoryItem = {
      flow_id: "flow-actions",
      profile_id: "profile-actions",
      selection: "all",
      status: "done",
      created_at: "2026-08-12 09:00:00",
      updated_at: "2026-08-12 09:20:00",
      legacy: false,
      tracks: [
        {
          platform: "boss", status: "done", result_run_id: "flow-result",
          scrape_run_id: "flow-scrape", jobs: [{ job_id: "b1" }],
        },
      ],
    };
    const wrapper = await mountDrawer({ items: [], flowItems: [realFlow] });

    const track = wrapper.get('[data-testid="history-flow-track"][data-platform="boss"]');
    await track.get('[data-testid="history-log-trigger"]').trigger("click");
    expect(wrapper.emitted("view-log")).toEqual([[expect.objectContaining({
      run_id: "flow-result", scrape_task_id: "flow-scrape",
    })]]);

    await track.get('[data-testid="history-delete-trigger"]').trigger("click");
    expect(wrapper.emitted("confirm-delete")).toEqual([[expect.objectContaining({ run_id: "flow-result" })]]);

    const confirming = await mountDrawer({
      items: [],
      flowItems: [realFlow],
      deleteTarget: { run_id: "flow-result", platform: "boss" } as HistoryRoundItem,
    });
    await confirming.get('[data-testid="history-delete-confirm-yes"]').trigger("click");
    expect(confirming.emitted("delete-round")).toEqual([[expect.objectContaining({ run_id: "flow-result" })]]);
    expect(confirming.emitted("open-round")).toBeUndefined();
  });

  it("shows finished_at as the primary time and falls back to created_at", async () => {
    // 017-US3: 主时间=定稿时间（重抓/补筛后刷新）；缺失回退创建时间
    const wrapper = await mountDrawer({
      items: [
        item({
          run_id: "h1", status: "done",
          created_at: "2026-08-01 09:00:00",
          finished_at: "2026-08-11 10:30:00",
        }),
        item({
          run_id: "h2", status: "partial",
          created_at: "2026-08-02 09:00:00",
          finished_at: null,
        }),
      ],
    });
    const times = wrapper.findAll(".history-round-time");
    expect(times[0].text()).toContain("2026-08-11 10:30");
    expect(times[1].text()).toContain("2026-08-02 09:00");
  });

  it("renders only the three allowed status labels", async () => {
    // 017-US3: 标签只有 完成 / 部分结果 / 已抓取，未筛选 三种
    const wrapper = await mountDrawer({
      items: [
        item({ run_id: "h1", status: "done" }),
        item({ run_id: "h2", status: "partial" }),
        item({ run_id: "h3", status: "scraped_only" }),
      ],
    });
    const statuses = wrapper.findAll(".history-round-status");
    expect(statuses[0].text()).toBe("完成");
    expect(statuses[1].text()).toBe("部分结果");
    expect(statuses[2].text()).toBe("已抓取，未筛选");
  });

  it("shows the total scraped jobs after every history status", async () => {
    const wrapper = await mountDrawer({
      items: [
        item({ run_id: "h1", status: "done", total_scraped: 54, total_kept: 47, total_dropped: 7 }),
        item({ run_id: "h2", status: "partial", total_scraped: 19, total_kept: 12 }),
        item({ run_id: "h3", status: "scraped_only", total_scraped: 238, total_kept: 0 }),
      ],
    });

    const totals = wrapper.findAll('[data-testid="history-round-total"]');
    expect(totals).toHaveLength(3);
    expect(totals.map((total) => total.text())).toEqual([
      "共 54 个岗位",
      "共 19 个岗位",
      "共 238 个岗位",
    ]);
  });

  it("keeps latest badge immediately after the time", async () => {
    const source = readFileSync(path.join(__dirname, "../../components/ResultHistoryDrawer.vue"), "utf8");
    const head = source.match(/\.history-round-head\s*\{[^}]*\}/s)?.[0] || "";
    expect(head).toContain("justify-content: flex-start");
    expect(head).not.toContain("space-between");
  });

  it("colors the round count parts by status tone", async () => {
    const wrapper = await mountDrawer();
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
    const wrapper = await mountDrawer();
    expect(wrapper.get('[data-platform="boss"]').attributes("aria-hidden")).toBe("false");
    expect(wrapper.get('[data-platform="zhilian"]').attributes("aria-hidden")).toBe("true");

    await wrapper.get('[data-testid="history-platform-tab-zhilian"]').trigger("click");
    expect(wrapper.get('[data-platform="boss"]').attributes("aria-hidden")).toBe("true");
    expect(wrapper.get('[data-platform="zhilian"]').attributes("aria-hidden")).toBe("false");
  });

  it("emits open-round on row click", async () => {
    const wrapper = await mountDrawer();
    await wrapper.get('[data-run-id="h2"]').trigger("click");
    expect(wrapper.emitted("open-round")).toEqual([["h2"]]);
  });

  // 平铺轮次行整行 cursor:pointer、点了就开，但它是 div 且没有键盘入口：
  // 只用键盘的用户 Tab 进抽屉只能删轮和看日志，一轮历史内容都打不开。
  it("opens a flat round row from the keyboard while keeping nested buttons independent", async () => {
    const wrapper = await mountDrawer();
    const row = wrapper.get('[data-run-id="h2"]');

    expect(row.attributes("tabindex")).toBe("0");
    expect(row.attributes("role")).toBe("button");

    await row.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("open-round")).toEqual([["h2"]]);
    await row.trigger("keydown", { key: " " });
    expect(wrapper.emitted("open-round")).toEqual([["h2"], ["h2"]]);
    wrapper.unmount();
  });

  it("does not open a round when the keyboard event belongs to a nested row action", async () => {
    const wrapper = await mountDrawer({
      items: [item({ run_id: "h1", scrape_task_id: "scrape-h1" })],
    });
    const logButton = wrapper.get('[data-run-id="h1"] [data-testid="history-log-trigger"]');

    await logButton.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("open-round")).toBeUndefined();
    await logButton.trigger("click");
    expect(wrapper.emitted("open-round")).toBeUndefined();
    expect(wrapper.emitted("view-log")).toHaveLength(1);
    wrapper.unmount();
  });

  it("keeps a confirming row closed to keyboard opening", async () => {
    const wrapper = await mountDrawer({ deleteTarget: item({ run_id: "h2", status: "partial" }) });
    await wrapper.get('[data-run-id="h2"]').trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("open-round")).toBeUndefined();
    wrapper.unmount();
  });

  // 键盘可达必须看得见落点：焦点环样式必须存在（窄屏深色主题下同样可辨）。
  it("gives the keyboard-focusable round row a visible focus ring", async () => {
    const source = readFileSync(path.join(__dirname, "../../components/ResultHistoryDrawer.vue"), "utf8");
    const focusBlock = source.match(/\.history-round-row:focus-visible\s*\{[^}]*\}/s)?.[0] || "";
    expect(focusBlock).toContain("outline");
  });

  it("confirms before deleting a round", async () => {
    const wrapper = await mountDrawer({ deleteTarget: item({ run_id: "h2", status: "partial" }) });
    await wrapper.get('[data-testid="history-delete-confirm-yes"]').trigger("click");
    expect(wrapper.emitted("delete-round")).toHaveLength(1);
    expect(wrapper.emitted("delete-round")![0]).toEqual([expect.objectContaining({ run_id: "h2" })]);
  });

  it("shows the full-row glass confirm with icon-only actions", async () => {
    const wrapper = await mountDrawer({ deleteTarget: item({ run_id: "h2" }) });
    const confirm = wrapper.get('[data-testid="history-delete-confirm"]');
    expect(confirm.text()).toContain("确认删除");
    expect(confirm.text()).not.toContain("删除后保留任务日志");
    expect(confirm.get('[data-testid="history-delete-confirm-yes"]').find("svg").exists()).toBe(true);
    expect(confirm.get('[data-testid="history-delete-confirm-no"]').find("svg").exists()).toBe(true);
  });

  it("does not open a round while the delete confirm overlay is visible", async () => {
    const wrapper = await mountDrawer({ deleteTarget: item({ run_id: "h2" }) });
    await wrapper.get('[data-testid="history-delete-confirm"]').trigger("click");
    expect(wrapper.emitted("open-round")).toBeUndefined();
  });

  it("cancels delete from the x button", async () => {
    const wrapper = await mountDrawer({ deleteTarget: item({ run_id: "h2" }) });
    await wrapper.get('[data-testid="history-delete-confirm-no"]').trigger("click");
    expect(wrapper.emitted("cancel-delete")).toHaveLength(1);
  });
  it("does not render round location summary", async () => {
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
    const wrapper = await mountDrawer({ detail });
    expect(wrapper.find('[data-testid="history-detail-location"]').exists()).toBe(false);
  });
});
