import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import DiscoveryView from "../DiscoveryView.vue";
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

function commonResponse(url: string, withCompletedScreenTask = false): Response | null {
  if (url.includes("/api/latest-running-task")) {
    if (withCompletedScreenTask) {
      return response({
        ok: true,
        has_task: true,
        task_id: "screen-bootstrap",
        kind: "ai_screen",
        status: "running",
        platform: "boss",
        scrape_task_id: "scrape-bootstrap",
        scrape_completed: true,
        progress: { stage: "ai_fine", message: "AI 筛选中" },
        logs: [],
      });
    }
    return response({ ok: true, has_task: false });
  }
  if (url.includes("/api/task-state/screen-bootstrap")) {
    return response({ ok: true, status: "completed", progress: {}, logs: [], platform: "boss" });
  }
  if (url.includes("/api/filter-labels")) {
    return response({ ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
  }
  if (url.includes("/api/options")) {
    return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
  }
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
  return null;
}

describe("Discovery recovery paths", () => {
  beforeEach(() => {
    setBuildIdentity(expectedBackendBuildHash);
    sessionStorage.clear();
    localStorage.clear();
  });

  it("shows the recrawl action only in the pending header and keeps it after dismissing the reminder", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) return response({ ok: true, has_result: false });
        return response({
          ok: true,
          has_result: true,
          source_run_id: "latest-boss-run",
          platform: "boss",
          status: "completed_with_pending",
          result: {
            jobs: [
              { job_id: "matched", platform: "boss", verdict: "match", title: "匹配岗位" },
              { job_id: "unmatched", platform: "boss", verdict: "not_match", title: "不匹配岗位" },
              { job_id: "uncertain", platform: "boss", verdict: "uncertain", title: "待确认岗位" },
            ],
            dropped: [{ job_id: "dropped", platform: "boss", title: "已筛除岗位" }],
            total_scraped: 4,
            total_kept: 3,
            total_matched: 1,
            total_dropped: 1,
            profile_summary: "3年后端开发",
          },
        });
      }
      if (url.endsWith("/api/pipeline/recrawl")) {
        return response({ ok: true, task_id: "header-recrawl" }, 202);
      }
      if (url.includes("/api/task-state/header-recrawl")) {
        return response({ status: "paused", progress: { message: "等待处理" }, logs: [] });
      }
      return commonResponse(url, true) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-header-recrawl" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("待确认"))!.trigger("click");
    await nextTick();
    const headingAction = wrapper.get('[data-testid="pending-recrawl-heading"]');
    expect(headingAction.text()).toContain("全部重抓（1）");

    await wrapper.get('[data-testid="pending-recrawl-dismiss"]').trigger("click");
    await nextTick();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="pending-recrawl-heading"]').exists()).toBe(true);

    await wrapper.findAll("button").find((button) => button.text().includes("匹配"))!.trigger("click");
    await nextTick();
    expect(wrapper.find('[data-testid="pending-recrawl-heading"]').exists()).toBe(false);

    await wrapper.findAll("button").find((button) => button.text().includes("已筛除"))!.trigger("click");
    await nextTick();
    expect(wrapper.find('[data-testid="pending-recrawl-heading"]').exists()).toBe(false);

    await wrapper.findAll("button").find((button) => button.text().includes("待确认"))!.trigger("click");
    await wrapper.get('[data-testid="pending-recrawl-heading"]').trigger("click");
    await flushPromises();

    // 040 批三：重抓请求体（source_run_id/job_ids）由 RecrawlContinue.spec.ts 正本覆盖，
    // 此处只验证头部按钮点击确实发起重抓。
    const recrawlCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/pipeline/recrawl"));
    expect(recrawlCall).toBeTruthy();

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("allows the same pending header action in history and sends the selected history run as source", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) {
        return response({
          ok: true,
          items: [{
            run_id: "history-pending-run",
            platform: "boss",
            status: "partial",
            created_at: "2026-08-11 10:00:00",
            total_scraped: 1,
            total_kept: 1,
            total_matched: 0,
            mismatch_count: 0,
            total_dropped: 0,
            pending_count: 1,
            keyword_summary: "后端 / 上海",
            profile_summary_preview: "3年后端开发",
            archived_at: "2026-08-12 10:00:00",
            is_latest: false,
          }],
        });
      }
      if ((url.includes("/api/result-history/history-pending-run?") || url.endsWith("/api/result-history/history-pending-run"))) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "history-pending-run",
          platform: "boss",
          status: "partial",
          result: {
            jobs: [{
              job_id: "history-job",
              platform: "boss",
              verdict: "uncertain",
              title: "历史待确认岗位",
              jd: "岗位详情",
            }],
            dropped: [],
            total_scraped: 1,
            total_kept: 1,
            total_dropped: 0,
            profile_summary: "历史轮画像",
            profile_facts: { experience: "3年" },
          },
        });
      }
      if (url.endsWith("/api/pipeline/recrawl")) {
        return response({ ok: true, task_id: "history-recrawl" }, 202);
      }
      if (url.includes("/api/task-state/history-recrawl")) {
        return response({ status: "paused", progress: { message: "等待处理" }, logs: [] });
      }
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-history-recrawl" } });
    await flushPromises();
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="pending-recrawl-heading"]').text()).toContain("全部重抓（1）");
    await wrapper.get('[data-testid="pending-recrawl-heading"]').trigger("click");
    await flushPromises();

    const recrawlCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/pipeline/recrawl"));
    expect(recrawlCall).toBeTruthy();
    expect(JSON.parse(String(recrawlCall![1]?.body))).toMatchObject({
      source_run_id: "history-pending-run",
      job_ids: ["history-job"],
    });

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // ---------- Spec041 返工（真实验收失败项一）：完成结果页刷新原地接回 ----------

  interface CompletedSceneOptions {
    profileId: string;
    platform: "boss" | "zhilian";
    jobs: Array<Record<string, unknown>>;
    activeCategory: string;
    selectedJobKey: string;
    stage?: Partial<{
      listScrollTop: number;
      jdScrollTop: number;
      visibleCount: number;
      detailOpen: boolean;
      userSelectedDetail: boolean;
    }>;
  }

  /** 造一份"已完成结果页"的会话现场 + 已结束事实（与真实运行时同结构）。 */
  function seedCompletedScene(options: CompletedSceneOptions): void {
    const { profileId, platform, jobs } = options;
    const epoch = `round-${platform}-1`;
    sessionStorage.setItem(`career-scout-round-epoch:${profileId}`, epoch);
    sessionStorage.setItem("career-scout-scene-live:" + profileId, "1");
    sessionStorage.setItem(`career-scout-workflow:${profileId}`, JSON.stringify({
      version: 2,
      unfinished: false,
      completed: true,
      activeStep: "results",
      analysisReady: true,
      keywords: [{ word: "Python后端", recommended: false }],
      selectedKeywords: ["Python后端"],
      cityText: "广州",
      filterValues: { boss: {}, zhilian: {} },
      profileSummary: "3年Python后端",
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
      pipelineResultRunId: `${platform}-run`,
      currentRoundStatus: "screened",
      resultLoaded: true,
      resultsPageSeen: true,
      activeCategory: options.activeCategory,
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
            visibleCount: options.stage?.visibleCount ?? 60,
            selectedJobKey: options.selectedJobKey,
            userSelectedDetail: options.stage?.userSelectedDetail ?? true,
            detailOpen: options.stage?.detailOpen ?? true,
            jdScrollTop: options.stage?.jdScrollTop ?? 0,
            listScrollTop: options.stage?.listScrollTop ?? 0,
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
      runId: `${platform}-run`,
    }));
  }

  it("Spec041: 智联完成结果页刷新后仍在智联结果页（平台/分类/选中/滚动全保留）", async () => {
    const profileId = "profile-refresh-zhilian";
    seedCompletedScene({
      profileId,
      platform: "zhilian",
      jobs: [
        { job_id: "z1", platform_job_id: "z1", platform: "zhilian", verdict: "match", title: "智联匹配岗位A" },
        { job_id: "z2", platform_job_id: "z2", platform: "zhilian", verdict: "match", title: "智联匹配岗位B" },
        {
          job_id: "z3", platform_job_id: "z3", platform: "zhilian", verdict: "uncertain",
          title: "智联待确认岗位C", jd: "这是 JD 正文",
        },
        {
          job_id: "z4", platform_job_id: "z4", platform: "zhilian", verdict: "uncertain",
          title: "智联待确认岗位D", jd: "这是另一条 JD",
        },
      ],
      activeCategory: "uncertain",
      selectedJobKey: "zhilian:z4",
      stage: { listScrollTop: 140, jdScrollTop: 212, visibleCount: 60 },
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
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
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();

    // 刷新后仍在当前智联结果页，不闪 BOSS、不闪空上传页。
    expect(wrapper.find(".results-stage").isVisible()).toBe(true);
    expect(wrapper.find('[data-testid="resume-input"]').isVisible()).toBe(false);
    expect(wrapper.find('[data-testid="latest-result-empty"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("aria-selected")).toBe("true");
    // 结果分类（待确认）与选中岗位按身份接回：选中的是第 4 条，不是列表第一条。
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("智联待确认岗位D");
    expect(wrapper.get('[data-testid="job-detail-jd-scroll"]').element.scrollTop).toBe(212);
    expect((wrapper.get(".job-list").element as HTMLElement).scrollTop).toBe(140);
    // 匹配分类计数仍是 2（结果没被清空/换轮）。
    expect(wrapper.text()).toContain("匹配2");
    // 没有被"已结束就开新一轮"清空/归档。
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("archive-latest"))).toBe(false);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("Spec041: BOSS 完成结果页刷新后仍在 BOSS 结果页", async () => {
    const profileId = "profile-refresh-boss";
    seedCompletedScene({
      profileId,
      platform: "boss",
      jobs: [
        { job_id: "b1", platform_job_id: "b1", platform: "boss", verdict: "match", title: "BOSS匹配岗位A" },
        { job_id: "b2", platform_job_id: "b2", platform: "boss", verdict: "match", title: "BOSS匹配岗位B" },
      ],
      activeCategory: "matched",
      selectedJobKey: "boss:b2",
      stage: { listScrollTop: 90 },
    });
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
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();

    expect(wrapper.find(".results-stage").isVisible()).toBe(true);
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("BOSS匹配岗位B");
    expect((wrapper.get(".job-list").element as HTMLElement).scrollTop).toBe(90);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("Spec041: 恢复后的完成结果不被旧异步任务响应写回 BOSS 默认态", async () => {
    const profileId = "profile-refresh-stale-task";
    seedCompletedScene({
      profileId,
      platform: "zhilian",
      jobs: [{
        job_id: "z1", platform_job_id: "z1", platform: "zhilian",
        verdict: "match", title: "智联岗位",
      }],
      activeCategory: "matched",
      selectedJobKey: "zhilian:z1",
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        // 后端残留的终态 BOSS 抓取任务：不得把恢复好的智联结果页改回 BOSS。
        return response({
          ok: true, has_task: true, task_id: "stale-boss-scrape", kind: "scrape",
          status: "completed", platform: "boss", scraped_count: 5, source_total: 5,
          progress: {}, logs: [],
        });
      }
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
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
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    expect(wrapper.find(".results-stage").isVisible()).toBe(true);
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("智联岗位");

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("does not render an empty latest results page while the latest result is loading after history", async () => {
    let holdLatest = false;
    let releaseLatest!: () => void;
    const latestGate = new Promise<void>((resolve) => { releaseLatest = resolve; });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        if (holdLatest) await latestGate;
        if (url.includes("platform=zhilian")) return response({ ok: true, has_result: false });
        return response({
          ok: true,
          has_result: true,
          source_run_id: "current-latest-run",
          platform: "boss",
          status: "completed_with_pending",
          result: {
            jobs: [{ job_id: "current-job", platform: "boss", verdict: "uncertain", title: "当前最新岗位" }],
            dropped: [],
            total_scraped: 1,
            total_kept: 1,
            total_dropped: 0,
          },
        });
      }
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) {
        return response({
          ok: true,
          items: [{
            run_id: "old-history-run",
            platform: "boss",
            status: "done",
            created_at: "2026-08-11 10:00:00",
            total_scraped: 1,
            total_kept: 1,
            total_matched: 1,
            mismatch_count: 0,
            total_dropped: 0,
            pending_count: 0,
            keyword_summary: "后端 / 上海",
            profile_summary_preview: "历史画像",
            archived_at: "2026-08-12 10:00:00",
            is_latest: false,
          }],
        });
      }
      if ((url.includes("/api/result-history/old-history-run?") || url.endsWith("/api/result-history/old-history-run"))) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "old-history-run",
          platform: "boss",
          status: "done",
          result: {
            jobs: [{ job_id: "old-job", platform: "boss", verdict: "match", title: "历史岗位" }],
            dropped: [],
            total_scraped: 1,
            total_kept: 1,
            total_matched: 1,
            total_dropped: 0,
          },
        });
      }
      return commonResponse(url, true) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-return-latest" } });
    await flushPromises();
    expect(wrapper.text()).toContain("当前最新岗位");

    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="history-round-marker"]')).toBeTruthy();

    holdLatest = true;
    const returnClick = wrapper.get('[data-testid="back-to-latest"]').trigger("click");
    await nextTick();

    expect(wrapper.find('[data-testid="latest-result-empty"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(true);

    releaseLatest();
    await returnClick;
    await flushPromises();
    expect(wrapper.find('[data-testid="latest-result-empty"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("当前最新岗位");
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(false);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });
});
