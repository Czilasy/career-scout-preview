import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import DiscoveryView from "../DiscoveryView.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";
import { setThemePlatform } from "../../composables/useTheme";

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

  it("shows an initial Flow restore failure instead of silently falling back", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) throw new Error("初始流程恢复失败");
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-initial-flow-failure" } });
    await flushPromises();

    expect(wrapper.find('[data-testid="parallel-flow-error"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="parallel-flow-error"]').text()).toContain("初始流程恢复失败");
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/latest-running-task"))).toHaveLength(0);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/latest-pipeline-result"))).toHaveLength(0);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("does not apply delayed Flow recovery after the view is unmounted", async () => {
    let releaseCurrent!: (value: Response) => void;
    const pendingCurrent = new Promise<Response>((resolve) => { releaseCurrent = resolve; });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return pendingCurrent;
      if (url.includes("/api/latest-running-task")) return response({ ok: true, has_task: false });
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-unmount-recovery" } });
    await Promise.resolve();
    const flowRef = (wrapper.vm as unknown as {
      parallelFlow?: { flow?: { value?: unknown } };
    }).parallelFlow?.flow;
    wrapper.unmount();

    releaseCurrent(response({ ok: true, flow: {
      id: "late-unmount-flow", profile_id: "profile-unmount-recovery", selection: "all", status: "running", tracks: [],
    } }));
    await flushPromises();
    await flushPromises();

    expect(flowRef?.value).toBeNull();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/latest-running-task"))).toHaveLength(0);
    expect(wrapper.emitted("notify") || []).toHaveLength(0);
    vi.unstubAllGlobals();
  });

  it("does not continue a delayed profile recovery after the view is unmounted", async () => {
    let releaseNewProfile!: (value: Response) => void;
    const pendingNewProfile = new Promise<Response>((resolve) => { releaseNewProfile = resolve; });
    const newProfileTaskCalls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current?profile_id=profile-unmount-old")) return response({ ok: true, flow: null });
      if (url.includes("/api/flows/current?profile_id=profile-unmount-new")) return pendingNewProfile;
      if (url.includes("/api/latest-running-task?profile_id=profile-unmount-new")) {
        newProfileTaskCalls.push(url);
        return response({ ok: true, has_task: false });
      }
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-unmount-old" } });
    await flushPromises();
    await wrapper.setProps({ profileId: "profile-unmount-new" });
    await Promise.resolve();
    const flowRef = (wrapper.vm as unknown as {
      parallelFlow?: { flow?: { value?: unknown } };
    }).parallelFlow?.flow;
    wrapper.unmount();

    releaseNewProfile(response({ ok: true, flow: {
      id: "late-profile-unmount-flow", profile_id: "profile-unmount-new", selection: "all", status: "running", tracks: [],
    } }));
    await flushPromises();
    await flushPromises();

    expect(flowRef?.value).toBeNull();
    expect(newProfileTaskCalls).toHaveLength(0);
    vi.unstubAllGlobals();
  });

  it("does not continue legacy auto-new-round after unmount while latest result is pending", async () => {
    let releaseLatest!: (value: Response) => void;
    const pendingLatest = new Promise<Response>((resolve) => { releaseLatest = resolve; });
    const profileId = "profile-unmount-latest-result";
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return response({ ok: true, flow: null });
      if (url.includes("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return pendingLatest;
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/latest-pipeline-result"))).toHaveLength(1);
    const epochKey = `career-scout-round-epoch:${profileId}`;
    const epochBeforeUnmount = sessionStorage.getItem(epochKey);
    expect(epochBeforeUnmount).toBeTruthy();

    wrapper.unmount();
    releaseLatest(response({
      ok: true,
      has_result: true,
      source_run_id: "late-completed-run",
      platform: "boss",
      status: "completed",
      result: {
        jobs: [{ job_id: "late-job", platform: "boss", verdict: "match", title: "晚到结果" }],
        dropped: [],
        total_scraped: 1,
        total_kept: 1,
        total_matched: 1,
        total_dropped: 0,
      },
    }));
    await flushPromises();
    await flushPromises();

    expect(sessionStorage.getItem(epochKey)).toBe(epochBeforeUnmount);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/latest-running-task"))).toHaveLength(1);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/result-history/archive-latest"))).toBe(false);
    expect(wrapper.emitted("notify") || []).toHaveLength(0);
    vi.unstubAllGlobals();
  });

  it("clears the previous Flow error when switching to a new profile", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current?profile_id=profile-error-old")) {
        throw new Error("旧画像流程读取失败");
      }
      if (url.includes("/api/flows/current?profile_id=profile-error-new")) {
        return response({ ok: true, flow: null });
      }
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-error-old" } });
    await flushPromises();
    expect(wrapper.get('[data-testid="parallel-flow-error"]').text()).toContain("旧画像流程读取失败");

    await wrapper.setProps({ profileId: "profile-error-new" });
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-testid="parallel-flow-error"]').exists()).toBe(false);
    wrapper.unmount();
    vi.unstubAllGlobals();
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
    await wrapper.get('[data-testid="history-platform-tab-boss"]').trigger("click");
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

  it("Spec046 T037: refresh preserves the same unlocked page and does not auto-advance", async () => {
    sessionStorage.setItem("career-scout-workflow:profile-v2-recovery", JSON.stringify({
      version: 2, unfinished: true, activeStep: "screen", analysisReady: true,
      keywords: [], selectedKeywords: [], cityText: "", filterValues: { boss: {}, zhilian: {} },
      profileSummary: "", profileFacts: {}, scrapeTaskId: "scrape-r", screenTaskId: "screen-r",
      scrapeCompleted: true, scrapeSnapshot: { status: "completed", progress: {}, logs: [] },
      screenSnapshot: { status: "running", progress: {}, logs: [] }, resultLoaded: false, resultsPageSeen: false,
    }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) {
        return response({
          ok: true,
          flow: {
            id: "flow-recovery", profile_id: "profile-v2-recovery", selection: "all", status: "running",
            tracks: [
              { id: "boss-track", platform: "boss", scrape_run_id: "scrape-r", screen_run_id: "screen-r", status: "running", stage: "screen" },
              { id: "zhilian-track", platform: "zhilian", scrape_run_id: "scrape-z", screen_run_id: "screen-z", status: "running", stage: "screen" },
            ],
          },
        });
      }
      if (url.includes("/api/task-state/scrape-r") || url.includes("/api/task-state/screen-r")) {
        return response({ status: "running", progress: {}, logs: [] });
      }
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-v2-recovery" } });
    await flushPromises();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/task-state/scrape-r"))).toBe(true);
    expect(wrapper.findAll('[data-testid="parallel-track-boss"]')).toHaveLength(2);
    expect(wrapper.findAll('[data-testid="parallel-track-zhilian"]')).toHaveLength(2);
    expect(wrapper.findAll("section.workflow-stack")[1]?.isVisible()).toBe(true);
    const stepButtons = wrapper.findAll(".step-nav button");
    expect(stepButtons[2]?.attributes("disabled")).toBeUndefined();
    expect(stepButtons[3]?.attributes("disabled")).toBe("");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("preserves a valid restored page when the all-platform Flow hydrates later", async () => {
    sessionStorage.setItem("career-scout-workflow:profile-flow-restore", JSON.stringify({
      version: 2, unfinished: true, activeStep: "search", analysisReady: false,
      keywords: [], selectedKeywords: [], cityText: "", filterValues: { boss: {}, zhilian: {} },
      profileSummary: "", profileFacts: {}, scrapeTaskId: "", screenTaskId: "",
      scrapeCompleted: false, scrapeSnapshot: null, screenSnapshot: null,
      resultLoaded: false, resultsPageSeen: false,
    }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) {
        return response({ ok: true, flow: {
          id: "flow-valid-restore", profile_id: "profile-flow-restore", selection: "all", status: "running",
          tracks: [
            { id: "boss-track", platform: "boss", scrape_run_id: "scrape-flow", screen_run_id: "screen-flow", status: "running", stage: "screen" },
            { id: "zhilian-track", platform: "zhilian", scrape_run_id: "scrape-flow-z", screen_run_id: "screen-flow-z", status: "running", stage: "screen" },
          ],
        } });
      }
      if (url.includes("/api/task-state/")) return response({ status: "running", progress: {}, logs: [] });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-flow-restore" } });
    await flushPromises();
    await flushPromises();

    expect(wrapper.findAll("section.workflow-stack")[0]?.isVisible()).toBe(true);
    expect(wrapper.findAll("section.workflow-stack")[1]?.isVisible()).toBe(false);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("keeps a restored all-platform Flow in the neutral theme", async () => {
    setThemePlatform("boss");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) {
        return response({
          ok: true,
          flow: {
            id: "flow-neutral-recovery", profile_id: "profile-neutral-recovery", selection: "all", status: "running",
            tracks: [
              { id: "boss-track", platform: "boss", scrape_run_id: "scrape-neutral-b", status: "running", stage: "scrape" },
              { id: "zhilian-track", platform: "zhilian", scrape_run_id: "scrape-neutral-z", status: "running", stage: "scrape" },
            ],
          },
        });
      }
      if (url.includes("/api/task-state/")) return response({ status: "running", progress: {}, logs: [] });
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-neutral-recovery" } });
    await flushPromises();

    expect(document.documentElement.getAttribute("data-platform")).toBe("all");
    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("keeps the all-platform theme when a late legacy task restore reports BOSS", async () => {
    let legacyStarted = false;
    let releaseLegacy!: () => void;
    const legacyGate = new Promise<void>((resolve) => { releaseLegacy = resolve; });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) {
        return response({
          ok: true,
          flow: {
            id: "flow-neutral-race", profile_id: "profile-neutral-race", selection: "all", status: "running",
            tracks: [
              { id: "boss-track", platform: "boss", status: "running", stage: "scrape" },
              { id: "zhilian-track", platform: "zhilian", status: "running", stage: "scrape" },
            ],
          },
        });
      }
      if (url.includes("/api/latest-running-task")) {
        legacyStarted = true;
        await legacyGate;
        return response({
          ok: true, has_task: true, task_id: "legacy-boss-task", kind: "scrape", status: "running",
          platform: "boss", progress: {}, logs: [],
        });
      }
      if (url.includes("/api/task-state/")) return response({ status: "running", progress: {}, logs: [] });
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-neutral-race" } });
    for (let attempt = 0; attempt < 10 && !legacyStarted; attempt += 1) {
      await Promise.resolve();
      await nextTick();
    }
    expect(legacyStarted).toBe(true);
    releaseLegacy();
    await flushPromises();
    await flushPromises();

    expect(document.documentElement.getAttribute("data-platform")).toBe("all");
    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("wires a later Flow result into the live view and emits one island notice", async () => {
    const initialFlow = {
      id: "flow-live-notice", profile_id: "profile-live-notice", selection: "all" as const, status: "running",
      tracks: [
        { id: "boss-track", platform: "boss" as const, scrape_run_id: "scrape-live-b", screen_run_id: null as string | null, result_run_id: null as string | null, status: "running", stage: "scrape" },
        { id: "zhilian-track", platform: "zhilian" as const, scrape_run_id: "scrape-live-z", screen_run_id: null as string | null, result_run_id: null as string | null, status: "running", stage: "scrape" },
      ],
    };
    let currentFlow = initialFlow;
    let resultLoads = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return response({ ok: true, flow: currentFlow });
      if (url.includes("/api/task-state/")) return response({ status: "running", progress: {}, logs: [] });
      if (url.endsWith("/api/task/pause/scrape-live-b")) return response({ ok: true });
      if (url.endsWith("/api/flows/flow-live-notice/tracks/boss/pause")) {
        currentFlow = {
          ...currentFlow,
          tracks: [
            { ...currentFlow.tracks[0], status: "done", stage: "complete", result_run_id: "result-live-b" },
            currentFlow.tracks[1],
          ],
        };
        return response({ ok: true, flow: currentFlow });
      }
      if (url.includes("/api/flows/flow-live-notice/results")) {
        resultLoads += 1;
        const bossTrack = currentFlow.tracks[0];
        return response({
          ok: true,
          results: {
            flow_id: "flow-live-notice", selection: "all", status: "running",
            tracks: [bossTrack.result_run_id ? {
              platform: "boss", status: "done", stage: "complete", result_run_id: "result-live-b",
              jobs: [{ job_id: "live-boss-job", platform: "boss", title: "新增岗位", verdict: "match" }], dropped: [],
            } : { platform: "boss", status: "running", stage: "scrape", scrape_run_id: "scrape-live-b", jobs: [], dropped: [] }, { platform: "zhilian", status: "running", stage: "scrape", scrape_run_id: "scrape-live-z", jobs: [], dropped: [] }],
          },
        });
      }
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-live-notice" } });
    await flushPromises();
    await wrapper.get('[data-testid="parallel-track-boss"]').get('[data-testid="pause-scrape"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(resultLoads).toBeGreaterThan(0);
    const notices = wrapper.emitted("island-notice") || [];
    expect(notices).toHaveLength(1);
    expect(notices[0]?.[0]).toMatchObject({ id: "flow-live-notice:boss:result-live-b", target: "results" });
    expect(wrapper.find(".results-stage").exists()).toBe(true);
    vi.unstubAllGlobals();
  });

  it("refreshes 04 in place when a failed track adds projected jobs without a result id", async () => {
    vi.useFakeTimers();
    let currentFlow: Record<string, unknown> = {
      id: "flow-failed-projection", profile_id: "profile-failed-projection", selection: "all", status: "running",
      tracks: [
        { id: "boss-track", platform: "boss", scrape_run_id: "scrape-failed-b", result_run_id: "boss-result", status: "running", stage: "scrape" },
        { id: "zhilian-track", platform: "zhilian", result_run_id: null, status: "failed", stage: "screen" },
      ],
    };
    let resultLoads = 0;
    let projectionPhase = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return response({ ok: true, flow: currentFlow });
      if (url.includes("/api/task-state/scrape-failed-b")) return response({ status: "running", progress: {}, logs: [] });
      if (url.includes("/api/flows/flow-failed-projection/results")) {
        resultLoads += 1;
        const zhilianJobs = projectionPhase > 0
          ? [{ job_id: "failed-zhilian-job", platform: "zhilian", title: "失败轨仍保留的岗位", verdict: "" }]
          : [];
        return response({ ok: true, results: {
          flow_id: "flow-failed-projection", selection: "all", status: "running",
          tracks: [
            { platform: "boss", status: "done", stage: "complete", result_run_id: "boss-result", jobs: [{ job_id: "boss-existing-job", platform: "boss", title: "已有岗位", verdict: "match" }], dropped: [] },
            { platform: "zhilian", status: "failed", stage: "screen", result_run_id: null, unfinished_ai_screening: projectionPhase > 0, message: projectionPhase > 0 ? "智联 AI 筛选未完成" : "", jobs: zhilianJobs, dropped: [] },
          ],
        } });
      }
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-failed-projection" } });
    await flushPromises();
    await flushPromises();
    const resultsStep = wrapper.findAll(".step-nav button").find((button) => button.text().includes("查看结果"));
    expect(resultsStep).toBeTruthy();
    await resultsStep!.trigger("click");
    await flushPromises();
    expect(wrapper.find(".results-stage").isVisible()).toBe(true);
    expect(wrapper.text()).toContain("已有岗位");
    const noticesBeforeProjection = (wrapper.emitted("island-notice") || []).length;

    projectionPhase = 1;
    await vi.advanceTimersByTimeAsync(2100);
    await flushPromises();
    await flushPromises();

    expect(resultLoads).toBeGreaterThan(1);
    await wrapper.findAll("button").find((button) => button.text().includes("待确认"))!.trigger("click");
    await nextTick();
    expect(wrapper.text()).toContain("失败轨仍保留的岗位");
    expect(wrapper.text()).toContain("智联 AI 筛选未完成");
    expect((wrapper.emitted("island-notice") || []).slice(noticesBeforeProjection)).toHaveLength(0);
    wrapper.unmount();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  // ---------- SPEC 046 判活口径返修：已中断轮不是活体 ----------
  // 用户真实现场（画像 296b32cc038e494a / 流程 7c2e865496964794）：一条轨道已完成并
  // 带全量结果（匹配 11 + 不匹配 23 + 已筛除 642），另一条轨道停在 AI 阶段、被服务
  // 重启打断（377 条未判定）。此刻内存里没有任何活体 worker，用户点进 04 却看到
  // 「匹配 0 / 不匹配 0 / 待确认 0 / 已筛除 0」——本轮结果从未提交进现场。
  // 三条用例按真实调用链走（挂载 → 点 04 → 结果页数字），不单独测谓词返回值。
  const doneLaneMatched = Array.from({ length: 11 }, (_unused, index) => ({
    job_id: `kept-${index}`, platform_job_id: `kept-${index}`, title: `保留岗位 ${index}`, verdict: "match",
  }));
  const doneLaneUnmatched = Array.from({ length: 23 }, (_unused, index) => ({
    job_id: `rejected-${index}`, platform_job_id: `rejected-${index}`, title: `不匹配岗位 ${index}`, verdict: "not_match",
  }));
  const doneLaneDropped = Array.from({ length: 642 }, (_unused, index) => ({
    job_id: `dropped-${index}`, platform_job_id: `dropped-${index}`, title: `筛除岗位 ${index}`,
  }));
  const brokenLaneUnjudged = Array.from({ length: 377 }, (_unused, index) => ({
    job_id: `unjudged-${index}`, platform_job_id: `unjudged-${index}`, title: `未判定岗位 ${index}`,
  }));

  /** 已完成轨道 + 指定状态的另一条轨道；zhilianStatus 用中断/运行两种口径复用同一形状。 */
  function interruptedRoundFlow(zhilianStatus: string): Record<string, unknown> {
    return {
      id: "flow-interrupted-round",
      profile_id: "profile-interrupted-round",
      selection: "all",
      status: zhilianStatus === "running" ? "running" : "interrupted",
      tracks: [
        {
          id: "track-finished-lane", platform: "boss",
          scrape_run_id: "scrape-finished-lane", screen_run_id: "screen-finished-lane",
          result_run_id: "result-finished-lane", status: "done", stage: "complete",
        },
        {
          id: "track-unjudged-lane", platform: "zhilian",
          scrape_run_id: "scrape-unjudged-lane", screen_run_id: "screen-unjudged-lane",
          result_run_id: null, status: zhilianStatus, stage: zhilianStatus === "running" ? "screen" : "ai",
        },
      ],
    };
  }

  function interruptedRoundResults(zhilianStatus: string): Record<string, unknown> {
    return {
      flow_id: "flow-interrupted-round",
      selection: "all",
      status: zhilianStatus === "running" ? "running" : "interrupted",
      tracks: [
        {
          platform: "boss", status: "done", stage: "complete",
          scrape_run_id: "scrape-finished-lane", screen_run_id: "screen-finished-lane",
          result_run_id: "result-finished-lane",
          jobs: [...doneLaneMatched, ...doneLaneUnmatched], dropped: doneLaneDropped,
        },
        {
          platform: "zhilian", status: zhilianStatus, stage: zhilianStatus === "running" ? "screen" : "ai",
          scrape_run_id: "scrape-unjudged-lane", screen_run_id: "screen-unjudged-lane",
          result_run_id: null, jobs: brokenLaneUnjudged, dropped: [],
        },
      ],
    };
  }

  /** 用户被中断那一轮的会话现场：停在 03、没进过 04、也没有结果。 */
  function seedInterruptedRoundScene(
    profileId: string,
    options: { interruptedRunId?: string } = {},
  ): void {
    sessionStorage.setItem(`career-scout-workflow:${profileId}`, JSON.stringify({
      version: 2, unfinished: true, activeStep: "screen", analysisReady: true,
      keywords: [], selectedKeywords: [], cityText: "", filterValues: { boss: {}, zhilian: {} },
      profileSummary: "", profileFacts: {},
      scrapeTaskId: "", screenTaskId: "", pausedRunId: "",
      interruptedRunId: options.interruptedRunId || "", recrawlTaskId: "",
      scrapeCompleted: true,
      scrapeSnapshot: { status: "completed", progress: { message: "上次抓取已完成" }, logs: [] },
      screenSnapshot: { status: "interrupted", progress: {}, logs: [] },
      recrawlSnapshot: null,
      pipelineResult: null, pipelineResultRunId: "", currentRoundStatus: "",
      resultLoaded: false, resultsPageSeen: false,
    }));
  }

  function stubInterruptedRoundFetch(zhilianStatus: string) {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) {
        return response({ ok: true, flow: interruptedRoundFlow(zhilianStatus) });
      }
      if (url.includes("/api/flows/flow-interrupted-round/results")) {
        return response({ ok: true, results: interruptedRoundResults(zhilianStatus) });
      }
      if (url.includes("/api/task-state/")) {
        return response({
          status: zhilianStatus === "running" && url.includes("screen-unjudged-lane") ? "running" : "completed",
          progress: {}, logs: [], total: 411, success_count: 34, fail_count: 0, unstarted_count: 0,
        });
      }
      if (url.includes("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  async function enterResultsStep(wrapper: ReturnType<typeof mount>) {
    const resultsStep = wrapper.findAll(".step-nav button").find((button) => button.text().includes("查看结果"));
    expect(resultsStep).toBeTruthy();
    await resultsStep!.trigger("click");
    await flushPromises();
    await flushPromises();
  }

  /** 视图实例上的现场读数：沿用本文件的 (wrapper.vm as unknown as ...) 惯例，
   *  只补类型，不改任何断言口径（script setup 顶层 ref 在 vm 上已解包，嵌套对象不解包）。 */
  function recoveryViewModel(wrapper: ReturnType<typeof mount>) {
    return wrapper.vm as unknown as {
      activeStep: string;
      resultsPageSeen: boolean;
      resultLoaded: boolean;
      scopeLocked: boolean;
      pipelineBusy: boolean;
      hasLiveTaskState(): boolean;
      parallelFlow: { canStartNewRound: { value: boolean } };
    };
  }

  it("已中断的并行流程进入 04 后按本轮结果给出四个桶", async () => {
    const profileId = "profile-interrupted-round";
    seedInterruptedRoundScene(profileId);
    stubInterruptedRoundFetch("interrupted");

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();
    await flushPromises();

    await enterResultsStep(wrapper);

    const vm = recoveryViewModel(wrapper);
    expect(vm.activeStep).toBe("results");
    expect(vm.resultsPageSeen).toBe(true);
    expect(vm.resultLoaded).toBe(true);
    expect(wrapper.findAll(".vtab-count").map((count) => count.text())).toEqual(["11", "23", "377", "642"]);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // SPEC 046 v2 D-13（本条原先钉的是错的一半）：「分轨合流」的用户口径是**谁先出结果谁先可看**
  // （FR-013 第二个平台原地加入的前提，也是用户 2026-09-30 亲自确认的设计）。真实轮实测过：
  // 智联 15:25 已交付、BOSS 还在抓，04 却整段空、四桶全 0——接口每 2 秒 200 且 payload 里
  // 就有已交付那一条的判定结果。本条按规格改成：先到平台的四桶照样给；「已进 04 页」这份
  // 水位仍留到整轮收尾再记（它管的是刷新接回时旧结果不许覆盖 02/03，不是能不能看结果）。
  it("仍有活体轨道时 04 照样给出先到平台的四个桶，但「已进 04 页」留到整轮收尾再记", async () => {
    const profileId = "profile-interrupted-round";
    seedInterruptedRoundScene(profileId);
    stubInterruptedRoundFetch("running");

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();
    await flushPromises();

    const vm = recoveryViewModel(wrapper);
    expect(vm.hasLiveTaskState()).toBe(true);

    await enterResultsStep(wrapper);

    expect(vm.resultsPageSeen).toBe(false);
    expect(vm.resultLoaded).toBe(true);
    expect(wrapper.findAll(".vtab-count").map((count) => count.text())).toEqual(["11", "23", "377", "642"]);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // SPEC 046 v2 状态词表：「已中断」没有活体 worker，唯一出路是开新一轮——
  // 本轮范围仍按「本轮未结束」锁住，但「能不能开新一轮」只由那一份清单回答：
  // 中断不在清单里，所以 02 主启动按钮与提交守卫（pipelineBusy）都放行。
  it("已中断轮范围仍锁，但开新一轮出口放行（词表：中断不是活体）", async () => {
    const profileId = "profile-interrupted-round";
    seedInterruptedRoundScene(profileId);
    stubInterruptedRoundFetch("interrupted");

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();
    await flushPromises();

    const vm = recoveryViewModel(wrapper);
    expect(vm.scopeLocked).toBe(true);
    expect(vm.pipelineBusy).toBe(false);
    expect(vm.parallelFlow.canStartNewRound.value).toBe(true);

    await enterResultsStep(wrapper);

    // 结果已接进现场，本轮范围守卫不因此放松；开新轮出口保持放行。
    expect(vm.scopeLocked).toBe(true);
    expect(vm.pipelineBusy).toBe(false);
    expect(vm.parallelFlow.canStartNewRound.value).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // 「已看过结果页」的置位只认问题 A（此刻有活体 worker）。历史上结果层把
  // interruptedRunId OR 进判活，中断轮的「已看过」因此永不置位、199 行那道闸门把
  // 结果永远挡在门外（04 显示 0/0/0/0 而接口里数据完好）。
  // 收口第一单补全验收：闸门拆掉后必须真的把本轮结果读进来——resultLoaded 置位、
  // 四个桶按本轮事实给出具体数字，"已看过"单独置位不算通过。
  it("legacy 中断断点在场时进 04 仍加载本轮结果并给出四个桶", async () => {
    const profileId = "profile-interrupted-round";
    seedInterruptedRoundScene(profileId, { interruptedRunId: "screen-unjudged-lane" });
    stubInterruptedRoundFetch("interrupted");

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();
    await flushPromises();

    const vm = recoveryViewModel(wrapper);
    expect(vm.hasLiveTaskState()).toBe(false);

    await enterResultsStep(wrapper);

    expect(vm.activeStep).toBe("results");
    expect(vm.resultsPageSeen).toBe(true);
    expect(vm.resultLoaded).toBe(true);
    expect(wrapper.findAll(".vtab-count").map((count) => count.text())).toEqual(["11", "23", "377", "642"]);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

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
      if (url.includes("/api/flows/current")) return response({
        ok: true,
        flow: { id: "flow-refresh-zhilian", profile_id: profileId, selection: "zhilian", status: "done", tracks: [] },
      });
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
      if (url.includes("/api/flows/current")) return response({
        ok: true,
        flow: { id: "flow-refresh-boss", profile_id: profileId, selection: "boss", status: "done", tracks: [] },
      });
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
      if (url.includes("/api/flows/current")) return response({
        ok: true,
        flow: { id: "flow-refresh-zhilian", profile_id: profileId, selection: "zhilian", status: "done", tracks: [] },
      });
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
    await wrapper.get('[data-testid="history-platform-tab-boss"]').trigger("click");
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

  // ---------- SPEC 046 V2 SC-005 / US4-1：刷新后已解锁入口仍可进入 ----------
  // 真实路径：挂载 → 现场存档落位 → Flow 归属成立 → Flow 结果投影仍在飞（水合空窗）。
  // 缺陷现场：空窗里可达集合退回 01+02，用户刚刷新的一页与其余已解锁入口一起被锁回去。
  function v2RefreshFlow(profileId: string): Record<string, unknown> {
    return {
      id: "flow-v2-refresh",
      profile_id: profileId,
      selection: "all",
      status: "running",
      tracks: [
        {
          id: "track-boss", platform: "boss",
          scrape_run_id: "scrape-v2-b", screen_run_id: "screen-v2-b", result_run_id: "result-v2-b",
          status: "done", stage: "complete",
        },
        {
          id: "track-zhilian", platform: "zhilian",
          scrape_run_id: "scrape-v2-z", screen_run_id: null, result_run_id: null,
          status: "running", stage: "scrape",
        },
      ],
    };
  }

  /** 挂起 Flow 结果投影，把页面停在「Flow 已成立、水合还没完成」的那段空窗里。 */
  function stubV2RefreshWindow(profileId: string) {
    let release!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return response({ ok: true, flow: v2RefreshFlow(profileId) });
      if (url.includes("/api/flows/flow-v2-refresh/results")) {
        await pending;
        return response({ ok: true, results: {
          flow_id: "flow-v2-refresh", selection: "all", status: "running",
          tracks: [
            { platform: "boss", status: "done", stage: "complete", result_run_id: "result-v2-b", jobs: [{ job_id: "v2-refresh-job", platform: "boss", title: "刷新前已有岗位", verdict: "match" }], dropped: [] },
            { platform: "zhilian", status: "running", stage: "scrape", result_run_id: null, jobs: [], dropped: [] },
          ],
        } });
      }
      if (url.includes("/api/task-state/")) return response({ status: "running", progress: {}, logs: [] });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/flows/flow-v2-refresh/tracks/")) return response({ ok: true, track: {} });
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    return { fetchMock, release };
  }

  /** 步骤导航的禁用读数：true = 这一页此刻进不去。 */
  function stepNavLocked(wrapper: ReturnType<typeof mount>): boolean[] {
    return wrapper.findAll(".step-nav button").map((button) => button.attributes("disabled") !== undefined);
  }

  it.each([
    { landing: "search", label: "02" },
    { landing: "screen", label: "03" },
    { landing: "results", label: "04" },
  ] as Array<{ landing: string; label: string }>)(
    "SC-005：$label 刷新后回到原页，水合空窗内已解锁入口不回锁",
    async ({ landing }) => {
      const profileId = `profile-v2-refresh-${landing}`;
      sessionStorage.setItem(`career-scout-workflow:${profileId}`, JSON.stringify({
        version: 2, unfinished: true, activeStep: landing, analysisReady: true,
        keywords: [], selectedKeywords: [], cityText: "", filterValues: { boss: {}, zhilian: {} },
        profileSummary: "", profileFacts: {},
        scrapeTaskId: "scrape-v2-b", screenTaskId: "screen-v2-b", pausedRunId: "", interruptedRunId: "", recrawlTaskId: "",
        scrapeCompleted: true,
        scrapeSnapshot: { status: "completed", progress: {}, logs: [] },
        screenSnapshot: { status: "running", progress: {}, logs: [] },
        recrawlSnapshot: null,
        pipelineResult: {
          ok: true, platform: "boss", jobs: [{ job_id: "v2-refresh-job", platform: "boss", title: "刷新前已有岗位", verdict: "match" }],
          dropped: [], total_scraped: 1, total_kept: 1, total_matched: 1, total_dropped: 0,
        },
        pipelineResultRunId: "result-v2-b",
        currentRoundStatus: "screened",
        resultLoaded: true, resultsPageSeen: landing === "results",
        activeCategory: "matched", resultPlatformFilter: "all",
        platform: "boss", resultPlatform: "boss",
      }));
      const { release } = stubV2RefreshWindow(profileId);

      const wrapper = mount(DiscoveryView, { props: { profileId } });
      await flushPromises();
      await flushPromises();

      const vm = recoveryViewModel(wrapper);
      // 刷新落回原页，水合本身不自动前进。
      expect(vm.activeStep).toBe(landing);
      // Flow 归属已成立、结果投影仍在飞：刷新前已解锁的入口必须仍进得去。
      const highest = ["upload", "search", "screen", "results"].indexOf(landing);
      expect(stepNavLocked(wrapper).slice(0, highest + 1)).toEqual(Array.from({ length: highest + 1 }, () => false));

      release();
      await flushPromises();
      await flushPromises();

      expect(recoveryViewModel(wrapper).activeStep).toBe(landing);
      expect(stepNavLocked(wrapper)).toEqual([false, false, false, false]);
      wrapper.unmount();
      vi.unstubAllGlobals();
    },
  );
});
