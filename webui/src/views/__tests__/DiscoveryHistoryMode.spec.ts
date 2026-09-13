import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import DiscoveryView from "../DiscoveryView.vue";
import { setThemePlatform } from "../../composables/useTheme";
import { requestCapsuleNavigation } from "../../composables/useDiscoveryState";
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
      if (url.includes("/api/latest-running-task")) {
        return response({ ok: true, has_task: false });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) {
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
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1"))) {
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
    // 当前没有最新结果时，回到最新应回到干净的 01，而不是短暂/长期停在空 04。
    expect(wrapper.find('[data-testid="latest-result-empty"]').isVisible()).toBe(false);
    expect(wrapper.find('[data-testid="resume-input"]').isVisible()).toBe(true);

    wrapper.unmount();

    vi.unstubAllGlobals();
  });

  // ---------- Spec041 返工（真实验收失败项二）：历史往返 + 灵动岛落点 ----------

  /** 造当前轮"已完成结果页"现场（平台固定），历史轮另由 mock 提供。 */
  function seedCurrentCompletedRound(options: {
    profileId: string;
    platform: "boss" | "zhilian";
    jobs: Array<Record<string, unknown>>;
    selectedJobKey: string;
  }): void {
    const { profileId, platform, jobs } = options;
    const epoch = `round-current-${platform}`;
    sessionStorage.setItem(`career-scout-round-epoch:${profileId}`, epoch);
    sessionStorage.setItem(`career-scout-workflow:${profileId}`, JSON.stringify({
      version: 2,
      unfinished: false,
      completed: true,
      activeStep: "results",
      analysisReady: true,
      keywords: [{ word: "当前轮关键词", recommended: false }],
      selectedKeywords: ["当前轮关键词"],
      cityText: "广州",
      filterValues: { boss: {}, zhilian: {} },
      profileSummary: "当前轮画像",
      profileFacts: {},
      scrapeTaskId: `scrape-${platform}`,
      screenTaskId: `screen-${platform}`,
      pausedRunId: "",
      interruptedRunId: "",
      recrawlTaskId: "",
      scrapeCompleted: true,
      scrapeSnapshot: { status: "completed", progress: {}, logs: [], platform },
      screenSnapshot: { status: "completed", progress: {}, logs: [], platform },
      recrawlSnapshot: null,
      pipelineResult: {
        ok: true,
        platform,
        jobs,
        dropped: [],
        total_scraped: jobs.length,
        total_kept: jobs.length,
        total_matched: jobs.length,
        total_dropped: 0,
      },
      pipelineResultRunId: `${platform}-current-run`,
      currentRoundStatus: "screened",
      resultLoaded: true,
      resultsPageSeen: true,
      activeCategory: "matched",
      resultPlatformFilter: "all",
      platform,
      resultPlatform: platform,
      pageScene: {
        version: 2,
        current: {
          [`${profileId}::${epoch}::${platform}`]: {
            profileInputHeight: null,
            profileInputWidth: null,
            profileInputContent: "",
            cityPanels: {},
            cardOpenStates: {},
            cardScrollTops: {},
            sortKey: "default",
            listFilterDraft: { salary: [], experience: [], degree: [], welfare: [] },
            visibleCount: 30,
            selectedJobKey: options.selectedJobKey,
            userSelectedDetail: true,
            detailOpen: true,
            jdScrollTop: 0,
            listScrollTop: 0,
          },
        },
        history: {},
        runIds: {},
      },
    }));
    localStorage.setItem(`career-scout-workflow:${profileId}:finished`, JSON.stringify({
      resultsPageSeen: true,
      finishedPartial: false,
      platform,
      runId: `${platform}-current-run`,
    }));
  }

  function historyItems(platform: "boss" | "zhilian", runId: string) {
    return {
      ok: true,
      items: [{
        run_id: runId,
        platform,
        status: "done",
        created_at: "2026-09-13 03:34:00",
        total_scraped: 2,
        total_kept: 2,
        total_matched: 2,
        mismatch_count: 0,
        total_dropped: 0,
        pending_count: 0,
        keyword_summary: "历史关键词",
        profile_summary_preview: "历史画像",
        archived_at: null,
        is_latest: false,
      }],
    };
  }

  function historyDetail(platform: "boss" | "zhilian", runId: string) {
    return {
      ok: true,
      has_result: true,
      source_run_id: runId,
      platform,
      status: "done",
      result: {
        jobs: [
          {
            job_id: "h1", platform_job_id: "h1", platform, verdict: "match",
            title: "历史岗位一",
          },
          {
            job_id: "h2", platform_job_id: "h2", platform, verdict: "match",
            title: "历史岗位二",
          },
        ],
        dropped: [],
        total_scraped: 2,
        total_kept: 2,
        total_matched: 2,
        total_dropped: 0,
      },
    };
  }

  it("Spec041: 智联历史 → 点当前智联任务灵动岛 → 回智联结果页，历史现场保留", async () => {
    const profileId = "profile-history-island-zhilian";
    seedCurrentCompletedRound({
      profileId,
      platform: "zhilian",
      jobs: [{
        job_id: "c1", platform_job_id: "c1", platform: "zhilian",
        verdict: "match", title: "当前轮智联岗位",
      }],
      selectedJobKey: "zhilian:c1",
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true, has_result: true, source_run_id: "zhilian-current-run",
          platform: "zhilian", status: "succeeded",
          result: {
            jobs: [{
              job_id: "c1", platform_job_id: "c1", platform: "zhilian",
              verdict: "match", title: "当前轮智联岗位",
            }],
            dropped: [], total_scraped: 1, total_kept: 1, total_matched: 1, total_dropped: 0,
          },
        });
      }
      if (url.includes("/api/filter-labels")) {
        return response({ ok: true, platform: "zhilian", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "zhilian", city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings,
          last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      if (url.includes("/api/result-history?")) return response(historyItems("zhilian", "hist-z"));
      if (url.includes("/api/result-history/hist-z")) return response(historyDetail("zhilian", "hist-z"));
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();
    // 当前轮：智联结果页
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("当前轮智联岗位");

    // 进智联历史轮，选第二条（历史现场）
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    await wrapper.findAll('[data-testid="job-row"]')[1].trigger("click");
    await nextTick();

    // 点灵动岛（跑完态 → 目标 results）：先完整退出历史，再按当前轮真实落点。
    requestCapsuleNavigation("results");
    await flushPromises();

    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("当前轮智联岗位");
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("aria-selected")).toBe("true");
    // 灵动岛导航绝不触发 resetWorkflow（不清场、不归档）。
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("archive-latest"))).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/task/cancel/"))).toBe(false);

    // 再进同一历史轮：历史现场（选中的第二条）仍在。
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("历史岗位二");

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("Spec041: BOSS 历史 → 点当前智联任务灵动岛 → 回智联，不受历史平台影响", async () => {
    const profileId = "profile-history-island-cross";
    seedCurrentCompletedRound({
      profileId,
      platform: "zhilian",
      jobs: [{
        job_id: "c1", platform_job_id: "c1", platform: "zhilian",
        verdict: "match", title: "当前轮智联岗位",
      }],
      selectedJobKey: "zhilian:c1",
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true, has_result: true, source_run_id: "zhilian-current-run",
          platform: "zhilian", status: "succeeded",
          result: {
            jobs: [{
              job_id: "c1", platform_job_id: "c1", platform: "zhilian",
              verdict: "match", title: "当前轮智联岗位",
            }],
            dropped: [], total_scraped: 1, total_kept: 1, total_matched: 1, total_dropped: 0,
          },
        });
      }
      if (url.includes("/api/filter-labels")) {
        return response({ ok: true, platform: "zhilian", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "zhilian", city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings,
          last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      if (url.includes("/api/result-history?")) return response(historyItems("boss", "hist-b"));
      if (url.includes("/api/result-history/hist-b")) return response(historyDetail("boss", "hist-b"));
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("当前轮智联岗位");

    // 看一条 BOSS 历史（顶部平台段跟着历史轮展示 BOSS，但只是展示）。
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="platform-segment-boss"]').attributes("aria-selected")).toBe("true");

    // 点灵动岛：回当前轮（智联），不被历史轮的 BOSS 覆盖。
    requestCapsuleNavigation("results");
    await flushPromises();

    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("aria-selected")).toBe("true");
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("当前轮智联岗位");

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("Spec041: 智联历史 → 点当前 BOSS 任务灵动岛 → 回 BOSS 任务进度页", async () => {
    const profileId = "profile-history-island-boss-task";
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "scrape-current-boss", kind: "scrape",
          status: "running", platform: "boss", progress: { message: "正在抓取" }, logs: [],
        });
      }
      if (url.includes("/api/task-state/scrape-current-boss")) {
        return response({ status: "running", progress: { message: "正在抓取" }, logs: [] });
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
      if (url.includes("/api/result-history?")) return response(historyItems("zhilian", "hist-z2"));
      if (url.includes("/api/result-history/hist-z2")) return response(historyDetail("zhilian", "hist-z2"));
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();
    // 当前轮：BOSS 抓取任务运行中（02 页）
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="pause-scrape"]').isVisible()).toBe(true);

    // 进智联历史
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);

    // 点灵动岛：虽然历史轮是"跑完"态（目标 results），但当前轮真实进度是 BOSS 抓取 → 回 02。
    requestCapsuleNavigation("results");
    await flushPromises();

    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="pause-scrape"]').isVisible()).toBe(true);
    expect(wrapper.find('[data-testid="resume-input"]').isVisible()).toBe(false);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("Spec041: 当前轮没有结果时点灵动岛不制造空结果页", async () => {
    const profileId = "profile-history-island-empty";
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response({ ok: true, has_task: false });
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
      if (url.includes("/api/result-history?")) return response(historyItems("boss", "hist-b2"));
      if (url.includes("/api/result-history/hist-b2")) return response(historyDetail("boss", "hist-b2"));
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();
    expect(wrapper.get('[data-testid="resume-input"]').isVisible()).toBe(true);

    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(true);

    requestCapsuleNavigation("results");
    await flushPromises();

    // 当前轮确实没有结果：落到真实进度（干净 01），不是空结果页。
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="resume-input"]').isVisible()).toBe(true);
    expect(wrapper.find(".results-stage").isVisible()).toBe(false);
    // 结果页整段收起（空态占位/恢复占位都不露给用户）。
    expect((wrapper.get(".results-stage").element as HTMLElement).style.display).toBe("none");
    expect(wrapper.find('[data-testid="latest-result-loading"]').exists()).toBe(false);

    wrapper.unmount();
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
      if (url.includes("/api/latest-running-task")) {
        return response({ ok: true, has_task: false });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) {
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
      if ((url.includes("/api/result-history/hz?") || url.endsWith("/api/result-history/hz"))) {
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

    await wrapper.get('[data-testid="back-to-latest"]').trigger("click");
    await flushPromises();
    expect(document.documentElement.getAttribute("data-platform")).toBe("boss");
    expect(wrapper.find('[data-testid="resume-input"]').exists()).toBe(true);

    wrapper.unmount();
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
      if (url.includes("/api/latest-running-task")) {
        return response({ ok: true, has_task: false });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) {
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
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1"))) {
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

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // 035 US3（真机问题③，FR-012/013）：从历史回到任务页后，按钮集合与正常运行完全一致。
  // 基建：后台抓取运行中 + 一轮可看的历史，走「正常运行 → 进历史 → 回到最新」真实路径。
  function scrapeRunningFetch035() {
    return vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "scrape-035-us3", kind: "scrape",
          status: "running", platform: "boss", progress: { message: "正在抓取" }, logs: [],
        });
      }
      if (url.includes("/api/task-state/scrape-035-us3")) {
        return response({ status: "running", progress: { message: "正在抓取" }, logs: [] });
      }
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) {
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
      if ((url.includes("/api/result-history/h035?") || url.endsWith("/api/result-history/h035"))) {
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

    // 正常运行基线：02 任务页使用共享的可见动作按钮。
    expect(wrapper.find('[data-testid="start-scrape"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="pause-scrape"]').text()).toContain("暂停");
    expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="cancel-scrape"]').exists()).toBe(true);
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

    // 回到 02 任务页：仍使用同一组可见动作按钮。
    expect(wrapper.find('[data-testid="start-scrape"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="pause-scrape"]').text()).toContain("暂停");
    expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="cancel-scrape"]').exists()).toBe(true);
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
    expect(wrapper.find('[data-testid="start-scrape"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="pause-scrape"]').text()).toContain("暂停");
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
      if (url.includes("/api/latest-running-task")) {
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
