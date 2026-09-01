import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import DiscoveryView from "../DiscoveryView.vue";
import { setThemePlatform } from "../../composables/useTheme";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const settings = {
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

describe("DiscoveryView history mode", () => {
  beforeEach(() => {
    setBuildIdentity(expectedBackendBuildHash);
    setThemePlatform("boss");
    // 026 B078：已结束事实持久化在 localStorage，须随测试隔离清空
    sessionStorage.clear();
    localStorage.clear();
  });

  it("opens a round, locks platform/rewrite actions, and returns to latest", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true,
          selection: "balanced",
          settings,
          last_custom: null,
          mode_version: null,
          manual_ranges: {},
          config_schema_version: 1,
        });
      }
      if (url.includes("/api/filter-labels")) {
        return response({ ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/latest-running-task")) {
        return response({ ok: true, has_task: false });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url.endsWith("/api/result-history")) {
        return response({
          ok: true,
          items: [{
            run_id: "h1",
            platform: "boss",
            status: "interrupted",
            created_at: "2026-08-11 10:00:00",
            total_scraped: 10,
            total_kept: 1,
            total_matched: 1,
            mismatch_count: 0,
            total_dropped: 9,
            pending_count: 0,
            keyword_summary: "Python 后端 / 上海",
            profile_summary_preview: "3年Python后端",
            archived_at: null,
            is_latest: true,
          }],
        });
      }
      if (url.endsWith("/api/result-history/h1")) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "h1",
          platform: "boss",
          status: "interrupted",
          started_at: 1_720_000_000_000,
          finished_at: 1_720_000_036_000,
          result: {
            jobs: [{ job_id: "j1", platform: "boss", verdict: "match", title: "历史岗位" }],
            total_kept: 1,
            total_dropped: 9,
            profile_summary: "完整画像文本",
          },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    expect(wrapper.find('[data-testid="history-drawer"]').exists()).toBe(true);

    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(true);
    // 017-US3: 历史轮状态标签只有三种，不再出现"失败但有 N 个岗位"
    expect(wrapper.get('[data-testid="history-round-marker"]').text()).not.toContain("失败但有");
    expect(wrapper.find('[data-testid="result-platform-filter"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
    expect(wrapper.findAll("button").some((button) => button.text().includes("补抓 JD"))).toBe(false);
    expect(wrapper.find('[data-testid="history-round-profile"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="history-round-profile-body"]').exists()).toBe(false);

    await wrapper.get('[data-testid="history-round-profile"]').trigger("mouseenter");
    await nextTick();
    expect(wrapper.get('[data-testid="history-round-profile-body"]').text()).toContain("完整画像文本");
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    const status = wrapper.emitted("round-status")?.flat().at(-1);
    expect(status).toMatchObject({ scope: "history", platform: "boss", phase: "judged", judged: 1 });

    await wrapper.get('[data-testid="back-to-latest"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="latest-result-empty"]').exists()).toBe(true);

    vi.unstubAllGlobals();
  });

  it("binds zhilian history to zhilian mode and brand color", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true,
          selection: "balanced",
          settings,
          last_custom: null,
          mode_version: null,
          manual_ranges: {},
          config_schema_version: 1,
        });
      }
      if (url.includes("/api/filter-labels")) {
        return response({ ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/latest-running-task")) {
        return response({ ok: true, has_task: false });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url.endsWith("/api/result-history")) {
        return response({
          ok: true,
          items: [{
            run_id: "hz",
            platform: "zhilian",
            status: "done",
            created_at: "2026-08-11 11:00:00",
            total_scraped: 8,
            total_kept: 2,
            total_matched: 2,
            total_dropped: 6,
            pending_count: 0,
            keyword_summary: "前端 / 北京",
            profile_summary_preview: "5年前端",
            archived_at: null,
            is_latest: true,
          }],
        });
      }
      if (url.endsWith("/api/result-history/hz")) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "hz",
          platform: "zhilian",
          status: "done",
          started_at: 1_720_000_000_000,
          finished_at: 1_720_000_036_000,
          result: {
            jobs: [{ job_id: "z1", platform: "zhilian", verdict: "match", title: "智联历史岗位" }],
            total_kept: 2,
            total_dropped: 6,
            profile_summary: "智联画像文本",
          },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();

    expect(document.documentElement.getAttribute("data-platform")).toBe("zhilian");
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("aria-selected")).toBe("true");

    vi.unstubAllGlobals();
  });

  it("035: browsing history and returning to latest does not mark flow finished", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings,
          last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      if (url.includes("/api/filter-labels")) {
        return response({ ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/latest-running-task")) {
        return response({ ok: true, has_task: false });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url.endsWith("/api/result-history")) {
        return response({
          ok: true,
          items: [{
            run_id: "h1", platform: "boss", status: "interrupted",
            created_at: "2026-08-11 10:00:00", total_scraped: 10, total_kept: 1,
            total_matched: 1, mismatch_count: 0, total_dropped: 9, pending_count: 0,
            keyword_summary: "Python 后端 / 上海", profile_summary_preview: "3年Python后端",
            archived_at: null, is_latest: true,
          }],
        });
      }
      if (url.endsWith("/api/result-history/h1")) {
        return response({
          ok: true, has_result: true, source_run_id: "h1", platform: "boss", status: "interrupted",
          started_at: 1_720_000_000_000, finished_at: 1_720_000_036_000,
          result: {
            jobs: [{ job_id: "j1", platform: "boss", verdict: "match", title: "历史岗位" }],
            total_kept: 1, total_dropped: 9, profile_summary: "完整画像文本",
          },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();

    // 进入历史轮 04 页：不得写入「已结束」事实（B086 根因修复）
    expect(localStorage.getItem("career-scout-workflow:profile-1:finished")).toBeNull();

    await wrapper.get('[data-testid="back-to-latest"]').trigger("click");
    await flushPromises();
    // 回到最新过渡到 04：同样不得写入「已结束」事实
    expect(localStorage.getItem("career-scout-workflow:profile-1:finished")).toBeNull();

    vi.unstubAllGlobals();
  });

  // 035 US3（真机问题③，FR-012/013）：从历史回到任务页后，按钮集合与正常运行完全一致。
  // 基建：后台抓取运行中 + 一轮可看的历史，走「正常运行 → 进历史 → 回到最新」真实路径。
  function scrapeRunningFetch035() {
    return vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "scrape-035-us3", kind: "scrape",
          status: "running", platform: "boss", progress: { message: "正在抓取" }, logs: [],
        });
      }
      if (url.endsWith("/api/task-state/scrape-035-us3")) {
        return response({ status: "running", progress: { message: "正在抓取" }, logs: [] });
      }
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/result-history")) {
        return response({
          ok: true,
          items: [{
            run_id: "h035", platform: "boss", status: "done",
            created_at: "2026-08-11 10:00:00", total_scraped: 10, total_kept: 1,
            total_matched: 1, mismatch_count: 0, total_dropped: 9, pending_count: 0,
            keyword_summary: "Python 后端 / 上海", profile_summary_preview: "3年Python后端",
            archived_at: null, is_latest: false,
          }],
        });
      }
      if (url.endsWith("/api/result-history/h035")) {
        return response({
          ok: true, has_result: true, source_run_id: "h035", platform: "boss", status: "done",
          started_at: 1_720_000_000_000, finished_at: 1_720_000_036_000,
          result: {
            jobs: [{ job_id: "j1", platform: "boss", verdict: "match", title: "历史岗位" }],
            total_kept: 1, total_dropped: 9, profile_summary: "完整画像文本",
          },
        });
      }
      if (url.includes("/api/filter-labels")) {
        return response({ ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings,
          last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      return response({});
    });
  }

  it("035 T017: 抓取运行中看历史后回到最新 → 任务页按钮恰好 2 个，与正常运行一致（真机问题③）", async () => {
    const fetchMock = scrapeRunningFetch035();
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-035-us3" } });
    await flushPromises();

    // 正常运行基线：02 任务页恰好 2 个操作按钮（停止抓取、结束并保存结果）
    expect(wrapper.get('[data-testid="start-scrape"]').text()).toContain("停止抓取");
    expect(wrapper.find('[data-testid="finish-active-scrape"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="view-scraped-only"]').exists()).toBe(false);

    // 看历史（真实路径：开抽屉 → 进历史轮）
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(true);

    // 回到最新
    await wrapper.get('[data-testid="back-to-latest"]').trigger("click");
    await flushPromises();

    // 回到 02 任务页：按钮集合与正常运行完全一致（恰好 2 个，无多余入口）
    expect(wrapper.get('[data-testid="start-scrape"]').text()).toContain("停止抓取");
    expect(wrapper.find('[data-testid="finish-active-scrape"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="view-scraped-only"]').exists()).toBe(false);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("035 T016: 进入历史轮不置位当前轮「抓取已完成」标志（历史只读）", async () => {
    const fetchMock = scrapeRunningFetch035();
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-035-us3b" } });
    await flushPromises();

    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    // 历史轮浏览期间：当前轮「进行确认AI筛选条件」入口不得出现（scrapeCompleted 未被历史轮置位）
    //（历史模式只有 04 可进，该按钮属 02 任务页——以回到最新后仍不出现为准）
    await wrapper.get('[data-testid="back-to-latest"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="view-scraped-only"]').exists()).toBe(false);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("035 T018: 抓取运行中即使「抓取已完成」被异常置位，「直接查看结果」也不出现（防半截保存）", async () => {
    sessionStorage.setItem("career-scout-workflow:profile-035-t018", JSON.stringify({
      version: 1, unfinished: true, activeStep: "search", analysisReady: true,
      keywords: [{ word: "Python", recommended: true }], selectedKeywords: ["Python"], cityText: "上海",
      filterValues: { boss: {}, zhilian: {} }, profileSummary: "3年Python", profileFacts: {},
      scrapeTaskId: "scrape-035-us3", scrapeCompleted: true,
      scrapeSnapshot: { status: "completed", progress: {}, logs: [] },
      resultLoaded: false, resultsPageSeen: false,
    }));
    const fetchMock = scrapeRunningFetch035();
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-035-t018" } });
    await flushPromises();

    // 02 任务页：抓取运行中 →「直接查看结果」因无活任务守卫不渲染（FR-013 纵深防御）
    expect(wrapper.get('[data-testid="start-scrape"]').text()).toContain("停止抓取");
    expect(wrapper.find('[data-testid="view-scraped-only"]').exists()).toBe(false);
    // 不发生半截保存请求
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/scrape-only-snapshot"))).toBe(false);

    wrapper.unmount();
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  // 对照组（T017 后半）：抓取真正完成后，「进行确认AI筛选条件」「直接查看结果」正常出现。
  it("035 T017 对照组: 抓取真实完成后两个按钮正常出现（只由真实完成触发）", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "scrape-done-035", kind: "scrape",
          status: "completed", platform: "boss", scraped_count: 5, source_total: 5,
          progress: { message: "抓取完成" }, logs: [],
        });
      }
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) {
        return response({ ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings,
          last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-035-done" } });
    await flushPromises();

    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="view-scraped-only"]').exists()).toBe(true);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });
});
