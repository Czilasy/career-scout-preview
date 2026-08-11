import { flushPromises, mount } from "@vue/test-utils";
import DiscoveryView from "../DiscoveryView.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SETTINGS = {
  inter_combo_delay: 10,
  detail_batch_size: 15,
  detail_interval: 2,
  detail_reset_every: 4,
  detail_batch_cooldown: 5,
  detail_tab_pool_size: 5,
  screen_batch_size: 50,
  screen_concurrency: 5,
  match_batch_size: 4,
  match_concurrency: 10,
};

function jobs(n = 2) {
  return Array.from({ length: n }, (_, i) => ({
    platform: "boss",
    platform_job_id: `job-${i}`,
    job_id: `job-${i}`,
    title: `岗位 ${i}`,
    company: "测试公司",
    salary: "20-30K",
    location: "上海",
    verdict: "",
  }));
}

interface FetchPlan {
  runningTask?: Record<string, unknown>;
  taskStates?: Record<string, Record<string, unknown>>;
  latestBoss?: Record<string, unknown>;
  latestZhilian?: Record<string, unknown>;
  scrapeSave?: Record<string, unknown>;
  historyList?: Record<string, unknown>;
  historyDetail?: Record<string, unknown>;
}

function mountWithFetch(plan: FetchPlan) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/latest-running-task")) {
      return response(plan.runningTask ?? { ok: true, has_task: false });
    }
    if (url.includes("/api/task-state/")) {
      const taskId = url.split("/api/task-state/")[1].split("?")[0];
      return response(plan.taskStates?.[taskId] ?? { status: "running" });
    }
    if (url.includes("/api/latest-pipeline-result") && url.includes("platform=boss")) {
      return response(plan.latestBoss ?? { ok: true, has_result: false });
    }
    if (url.includes("/api/latest-pipeline-result") && url.includes("platform=zhilian")) {
      return response(plan.latestZhilian ?? { ok: true, has_result: false });
    }
    if (url.includes("/api/scrape-result-save")) {
      return response(plan.scrapeSave ?? { ok: true, saved: false });
    }
    if (url.includes("/api/result-history/") && !url.endsWith("/api/result-history")) {
      const runId = url.split("/api/result-history/")[1].split("?")[0];
      return response((plan.historyDetail as Record<string, { run_id: string }> | undefined)?.[runId]
        ?? { ok: false });
    }
    if (url.endsWith("/api/result-history")) {
      return response(plan.historyList ?? { ok: true, items: [] });
    }
    if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
    if (url.endsWith("/api/options")) return response({ cities: [] });
    if (url.endsWith("/api/advanced-settings")) {
      return response({
        ok: true,
        selection: "balanced",
        settings: SETTINGS,
        last_custom: null,
        mode_version: null,
        manual_ranges: {},
        config_schema_version: 1,
      });
    }
    return response({});
  });
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
  return { wrapper, fetchMock };
}

describe("DiscoveryView B038 跳过 AI 直接查看", () => {
  beforeEach(() => {
    setBuildIdentity(expectedBackendBuildHash);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function completedScrape(scrapedCount = 2) {
    const plan: FetchPlan = {
      runningTask: {
        ok: true, has_task: true, kind: "scrape", task_id: "scrape-1",
        status: "running", platform: "boss", auto_screen: false,
      },
      taskStates: {
        "scrape-1": {
          status: "done", scraped_count: scrapedCount, source_total: scrapedCount,
          platform: "boss",
        },
      },
    };
    const { wrapper, fetchMock } = mountWithFetch(plan);
    await flushPromises();
    await flushPromises();
    return { wrapper, fetchMock };
  }

  it("抓取完成后并排显示两个按钮（改名 + 新增）", async () => {
    const { wrapper } = await completedScrape();
    const buttons = wrapper.findAll("button").map((b) => b.text());
    expect(buttons).toContain("进行确认AI筛选条件");
    expect(buttons).toContain("直接查看结果");
    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="view-scraped-only"]').exists()).toBe(true);
  });

  it("直接查看：保存成功进入 04 页待筛选模式，顶栏已抓取", async () => {
    const { wrapper, fetchMock } = await completedScrape();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/scrape-result-save")) {
        return response({
          ok: true, saved: true, run_id: "run-1",
          result: {
            ok: true, jobs: jobs(2), dropped: [], total_scraped: 2,
            total_kept: 2, total_matched: 0, total_dropped: 0, profile_summary: "",
          },
        });
      }
      if (url.includes("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result") && url.includes("platform=boss")) {
        return response({ ok: true, has_result: false });
      }
      if (url.includes("/api/latest-pipeline-result") && url.includes("platform=zhilian")) {
        return response({ ok: true, has_result: false });
      }
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.endsWith("/api/options")) return response({ cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings: SETTINGS, last_custom: null,
          mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      return response({});
    });
    await wrapper.get('[data-testid="view-scraped-only"]').trigger("click");
    await flushPromises();

    // 进入 04 页：单"待筛选"tab、岗位展示
    expect(wrapper.text()).toContain("待筛选");
    expect(wrapper.text()).toContain("岗位 0");
    expect(wrapper.text()).not.toContain("判定依据");
    // 顶栏上抛 scraped 相位（已抓取数 = 2）
    const scraped = wrapper.emitted("round-status")?.findLast(([p]) => (p as { phase: string }).phase === "scraped");
    expect(scraped?.[0]).toMatchObject({ phase: "scraped", judged: 2 });
  });

  it("0 岗位：不调用保存接口，04 页显示 0", async () => {
    const { wrapper, fetchMock } = await completedScrape(0);
    await wrapper.get('[data-testid="view-scraped-only"]').trigger("click");
    await flushPromises();

    const saveCalls = fetchMock.mock.calls.filter(([input]) =>
      String(input).includes("/api/scrape-result-save"));
    expect(saveCalls.length).toBe(0);
    expect(wrapper.text()).toContain("待筛选");
    expect(wrapper.text()).toContain("0");
  });

  it("刷新恢复 scraped_only 轮：未筛选模式展示", async () => {
    const { wrapper } = mountWithFetch({
      latestBoss: {
        ok: true, has_result: true, source_run_id: "run-1",
        status: "scraped_only",
        scrape_task_id: "scrape-1",
        result: {
          ok: true, jobs: jobs(2), dropped: [], total_scraped: 2,
          total_kept: 2, total_matched: 0, total_dropped: 0, profile_summary: "画像",
        },
      },
    });
    await flushPromises();
    await flushPromises();
    // 进入 04 页
    const stepBtn = wrapper.findAll("button").find((b) => b.text().includes("查看结果"));
    await stepBtn!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("待筛选");
    expect(wrapper.text()).not.toContain("判定依据");
    expect(wrapper.text()).not.toContain("补抓 JD");
    expect(wrapper.text()).not.toContain("全部重抓");
    const scraped = wrapper.emitted("round-status")?.findLast(([p]) => (p as { phase: string }).phase === "scraped");
    expect(scraped?.[0]).toMatchObject({ phase: "scraped", judged: 2 });
  });

  it("历史未筛选轮显示补筛入口，点击进入 AI 筛选步骤", async () => {
    const { wrapper } = mountWithFetch({
      historyList: {
        ok: true,
        items: [{
          run_id: "run-1", platform: "boss", status: "scraped_only",
          created_at: "2026-08-12T00:00:00", total_scraped: 2, total_kept: 2,
          total_matched: 0, total_dropped: 0, pending_count: 0,
          keyword_summary: "Python / 上海", profile_summary_preview: "",
          is_latest: true,
        }],
      },
      historyDetail: {
        "run-1": {
          ok: true, has_result: true, source_run_id: "run-1",
          platform: "boss", status: "scraped_only", scrape_task_id: "scrape-1",
          result: {
            ok: true, jobs: jobs(2), dropped: [], total_scraped: 2,
            total_kept: 2, total_matched: 0, total_dropped: 0,
            profile_summary: "3年Python后端",
          },
        },
      },
    });
    await flushPromises();
    // 打开历史抽屉并打开该轮（历史入口由 App 顶栏触发，组件内走 exposed 方法）
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    const row = wrapper.find('[data-testid="history-round-row"]');
    await row.trigger("click");
    await flushPromises();

    // 历史未筛选轮：显示"开始 AI 筛选"，不显示本轮画像
    expect(wrapper.find('[data-testid="screen-from-history"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="history-round-marker"]').text()).toContain("已抓取，未筛选");
    expect(wrapper.text()).not.toContain("本轮画像");

    await wrapper.get('[data-testid="screen-from-history"]').trigger("click");
    await flushPromises();
    // 退出历史模式进入步骤 3（AI 筛选条件确认）
    expect(wrapper.text()).toContain("确认 6 类筛选条件");
  });
});