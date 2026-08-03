import { flushPromises, mount } from "@vue/test-utils";
import DiscoveryView from "../DiscoveryView.vue";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("DiscoveryView", () => {
  it("keeps scope editable when only a completed historical result is restored", async () => {
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
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "completed-run",
          result: { jobs: [], profile_summary: "历史画像", total_kept: 4, total_dropped: 0 },
          started_at: 1_000,
          finished_at: 2_000,
          execution_config: {
            screen_batch_size: 50,
            screen_concurrency: 10,
            match_batch_size: 10,
            match_concurrency: 10,
          },
        });
      }
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
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
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("广泛抓取"))!.trigger("click");

    expect(wrapper.get('[data-testid="custom-keyword"]').attributes()).not.toHaveProperty("disabled");
    expect(wrapper.get('[data-testid="custom-city"]').attributes()).not.toHaveProperty("disabled");
    expect(wrapper.get('[data-testid="pages-per-combination"]').attributes()).not.toHaveProperty("disabled");
    expect(wrapper.find(".task-progress").exists()).toBe(true);
    expect(wrapper.find(".task-progress").text()).toContain("已完成");
    expect(wrapper.find(".task-progress").text()).toContain("用时");
    await wrapper.findAll("button").find((button) => button.text().includes("AI 筛选"))!.trigger("click");
    expect(wrapper.find(".task-progress").exists()).toBe(true);
    expect(wrapper.find(".task-progress").text()).toContain("已完成");
    const screenProgress = wrapper.findAll(".task-progress").at(-1);
    expect(screenProgress?.text()).not.toContain("精筛每批");

    vi.unstubAllGlobals();
  });

  it("uses canonical scope, preserves pages across modes and locks a started task", async () => {
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
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.endsWith("/api/latest-running-task")) return response({ task: null });
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true,
          selection: "balanced",
          settings,
          last_custom: { config_digest: "sha256:custom", settings: { ...settings, detail_batch_size: 8 } },
          mode_version: { id: "mode-v1", version_digest: "sha256:mode", available_modes: ["stable", "balanced", "extreme"] },
          manual_ranges: {},
          config_schema_version: 1,
        });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: {
            keywords: ["AI应用开发"], scope_kind: "cities", cities: ["东莞"],
            pages_per_combination: 3, combination_count: 1, planned_pages: 3,
            task_size: "small", scope_digest: "sha256:scope",
          },
          deduplicated: { keywords: ["ai应用开发"], cities: ["东莞市"] },
        });
      }
      if (url.endsWith("/api/advanced-settings/select-mode")) {
        expect(JSON.parse(String(init?.body))).toEqual({ mode: "stable", scope_digest: "sha256:scope" });
        return response({
          ok: true, selection: "stable", settings: { ...settings, detail_batch_size: 6 },
          task_size: "small", mode_version_id: "mode-v1", config_digest: "sha256:stable",
        });
      }
      if (url.endsWith("/api/execute-search")) {
        expect(JSON.parse(String(init?.body)).scope_digest).toBe("sha256:scope");
        return response({ ok: true, task_id: "scrape-locked" });
      }
      if (url.endsWith("/api/task-state/scrape-locked")) return response({ status: "running", progress: {}, logs: [] });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("AI应用开发");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("东莞市");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="scope-preview"]').exists()).toBe(false);
    const pages = wrapper.get('[data-testid="pages-per-combination"]');
    expect((pages.element as HTMLInputElement).value).toBe("3");

    await wrapper.get('[data-mode="stable"]').trigger("click");
    await flushPromises();
    expect((pages.element as HTMLInputElement).value).toBe("3");
    expect((wrapper.get('[data-testid="detail-batch-size"]').element as HTMLInputElement).value).toBe("6");

    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="custom-keyword"]').attributes()).toHaveProperty("disabled");
    expect(wrapper.get('[data-testid="custom-city"]').attributes()).toHaveProperty("disabled");
    expect(wrapper.get('[data-testid="pages-per-combination"]').attributes()).toHaveProperty("disabled");

    vi.unstubAllGlobals();
  });

  it("restores interrupted AI screen source ids so start is reachable", async () => {
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
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "interrupted-run",
          kind: "ai_screen", status: "interrupted",
          scrape_task_id: "scrape-1", scrape_completed: true,
          frozen_filters: { salary: ["406"], experience: [] },
          profile_summary: "后端工程师",
        });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true, has_result: true, source_run_id: "stale-partial", status: "completed_with_pending",
          result: { jobs: [{ job_id: "old-1", verdict: "uncertain", verdict_reason: "旧待确认" }], total_kept: 1, total_dropped: 0 },
        });
      }
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings, last_custom: null, mode_version: null,
          manual_ranges: {}, config_schema_version: 1,
        });
      }
      if (url.endsWith("/api/ai-screen")) {
        return response({ ok: true, task_id: "new-run", resuming: true });
      }
      if (url.includes("/api/task-state/")) {
        return response({
          ok: true, status: "done", progress: {}, logs: [], result: { jobs: [], total_kept: 0, total_dropped: 0 },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    const start = wrapper.get('[data-testid="start-ai-screen"]');
    expect(start.attributes("disabled")).toBeUndefined();
    expect(wrapper.find(".task-progress").exists()).toBe(false);
    expect(wrapper.find('[data-testid="finish-interrupted-screen"]').exists()).toBe(true);
    await start.trigger("click");
    await flushPromises();
    const aiCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/ai-screen"));
    expect(aiCall).toBeTruthy();
    expect(JSON.parse(aiCall![1]!.body as string)).toMatchObject({
      scrape_task_id: "scrape-1",
      screening_fields: { salary: ["406"], experience: [] },
      profile_summary: "后端工程师",
    });

    vi.unstubAllGlobals();
  });

  it("keeps gated actions separate and stops on the canonical completed state", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true,
          settings: {
            pages: 3,
            inter_combo_delay: 30,
            detail_batch_size: 5,
            screen_batch_size: 50,
            screen_concurrency: 1,
            match_batch_size: 4,
            match_concurrency: 1,
          },
          defaults: {},
        });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: {
            keywords: ["Python 后端"], scope_kind: "cities", cities: ["上海"],
            pages_per_combination: 3, combination_count: 1, planned_pages: 3,
            task_size: "small", scope_digest: "sha256:scope-existing",
          },
          deduplicated: { keywords: [], cities: [] },
        });
      }
      if (url.endsWith("/api/reset-latest-result")) {
        return response({ ok: true, cleared: true });
      }
      if (url.endsWith("/api/analyze-resume")) {
        return response({
          ok: true,
          fields: {
            keyword: [{ word: "Python 后端", recommended: true }],
            city: ["上海"],
            salary: ["406"],
            experience: [],
            degree: [],
            industry: [],
            scale: [],
            stage: [],
            profile_summary: "Python 后端候选人",
          },
          labels: {
            keyword: ["搜索关键词", [{ word: "Python 后端", recommended: true }], "keyword_chips"],
            city: ["城市", ["上海"], "city"],
            salary: ["薪资范围", ["406"], { "不限": "0", "20-50K": "406" }],
            experience: ["经验要求", [], { "不限": "0", "3-5年": "105" }],
            degree: ["学历", [], { "不限": "0", "本科": "203" }],
            industry: ["行业", [], { "不限": "0", "互联网": "100020" }],
            scale: ["公司规模", [], { "不限": "0", "100-499人": "304" }],
            stage: ["融资阶段", [], { "不限": "0", "B轮": "804" }],
          },
        });
      }
      if (url.endsWith("/api/execute-search")) {
        expect(JSON.parse(String(init?.body))).toEqual({
          script_params: { keyword: "Python 后端", city: ["上海"], filters: {} },
          scope_digest: "sha256:scope-existing",
        });
        return response({ ok: true, task_id: "scrape-1" });
      }
      if (url.endsWith("/api/task-state/scrape-1")) {
        return response({ status: "completed", progress: {}, logs: [], result: { jobs: [] } });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();

    const file = new File(["resume"], "resume.txt", { type: "text/plain" });
    Object.defineProperty(wrapper.get('[data-testid="resume-input"]').element, "files", {
      value: [file],
      configurable: true,
    });
    await wrapper.get('[data-testid="resume-input"]').trigger("change");
    await wrapper.get('[data-testid="resume-consent"]').setValue(true);
    await wrapper.get('[data-testid="analyze-resume"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="keyword-chip"]').attributes("aria-pressed")).toBe("true");
    expect(wrapper.get('[data-testid="start-scrape"]').text()).toContain("开始抓取");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/reset-latest-result",
      expect.objectContaining({ method: "POST" }),
    );
    expect(wrapper.find('[data-testid="start-ai-screen"]').exists()).toBe(false);

    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/execute-search",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/search-progress")))
      .toBe(false);

    vi.unstubAllGlobals();
  });

  it("uses nationwide scope preview when city is empty", async () => {
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
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.endsWith("/api/latest-running-task")) return response({ task: null });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: { keywords: ["AI 应用开发"], scope_kind: "nationwide", cities: [], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:nationwide" },
          deduplicated: { keywords: ["ai 应用开发"], cities: [] },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("AI 应用开发");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await flushPromises();

    const previewCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/search-scope/preview"));
    expect(previewCall).toBeTruthy();
    expect(JSON.parse(String(previewCall?.[1]?.body))).toMatchObject({ scope_kind: "nationwide", cities: [] });
    vi.unstubAllGlobals();
  });

  it("keeps the >10 pages warning inside the ? tooltip instead of inline layout", async () => {
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
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.endsWith("/api/latest-running-task")) return response({ task: null });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("跳过简历"))!.trigger("click");

    const pages = wrapper.get('[data-testid="pages-per-combination"]');
    await pages.setValue(11);
    await pages.trigger("change");
    await flushPromises();

    const pagesLabel = pages.element.parentElement as HTMLElement;
    expect(pagesLabel.querySelector(".hint-warn")).toBeNull();
    expect(pagesLabel.querySelector("i.tip")?.getAttribute("data-tip")).toContain("BOSS 最多返回 10 页，超出可能无新数据");

    await pages.setValue(3);
    await pages.trigger("change");
    await flushPromises();
    expect(pagesLabel.querySelector("i.tip")?.getAttribute("data-tip")).not.toContain("BOSS 最多返回 10 页");

    vi.unstubAllGlobals();
  });

  it("renders BOSS as default draft platform and switches draft to zhilian without touching task/result", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings: {
            inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
            detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
            screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
          }, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();

    // 默认草稿平台为 BOSS（DEFAULT_PLATFORM）
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(false);
    const bossBtn = wrapper.get('[data-testid="platform-segment-boss"]');
    const zhilianBtn = wrapper.get('[data-testid="platform-segment-zhilian"]');
    expect(bossBtn.attributes("aria-selected")).toBe("true");
    expect(zhilianBtn.attributes("aria-selected")).toBe("false");

    // 切换到智联：只作用于草稿，不应触发任何后端调用
    const callsBefore = fetchMock.mock.calls.length;
    await zhilianBtn.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(false);
    expect(zhilianBtn.attributes("aria-selected")).toBe("true");
    expect(bossBtn.attributes("aria-selected")).toBe("false");
    // 切换草稿平台不应触发额外的网络请求（T503 阶段尚未接入按平台加载 schema）
    expect(fetchMock.mock.calls.length).toBe(callsBefore);

    // 切回 BOSS
    await bossBtn.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(bossBtn.attributes("aria-selected")).toBe("true");

    vi.unstubAllGlobals();
  });

  it("keeps draft platform switch independent of a running task platform", async () => {
    // 不变式 1（platform-schema.md L147）：切换草稿平台不改 task/result。
    // 这里 mock 一个运行中的 BOSS 抓取任务，再切草稿到智联，验证 BOSS 任务状态不被改写。
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "boss-run-1",
          kind: "scrape", status: "running",
        });
      }
      if (url.includes("/api/task-state/boss-run-1")) {
        return response({ status: "running", progress: { message: "BOSS 抓取中" }, logs: [] });
      }
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings: {
            inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
            detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
            screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
          }, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();

    // 任务被接回后 activeStep 仍是 upload；切到 search 步骤才会渲染 TaskProgress。
    // 草稿平台分段控件在所有步骤都常驻顶部，不受 activeStep 影响。
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    // 草稿切到智联：仅改 draftPlatform，不触发后端调用，也不改 task snapshot
    const callsBefore = fetchMock.mock.calls.length;
    await wrapper.get('[data-testid="platform-segment-zhilian"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    expect(fetchMock.mock.calls.length).toBe(callsBefore);

    // 切回 BOSS 草稿
    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    vi.unstubAllGlobals();
  });
});
