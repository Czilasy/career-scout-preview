import { flushPromises, mount } from "@vue/test-utils";
import DiscoveryView from "../DiscoveryView.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";
import { readFileSync } from "node:fs";
import path from "node:path";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("DiscoveryView", () => {
  beforeEach(() => {
    // 确保当前测试引用的 api 模块实例处于已验证状态（setup.ts 的验证可能落在另一个模块实例上）
    setBuildIdentity(expectedBackendBuildHash);
  });
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
          // T509：所有 has_task=true 响应含 platform（http-api.md L201）
          platform: "boss",
          scrape_task_id: "scrape-1", scrape_completed: true,
          frozen_filters: { salary: ["406"], experience: [] },
          profile_summary: "3年Python后端工程师候选人",
        });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true, has_result: true, source_run_id: "stale-partial", status: "completed_with_pending",
          result: { jobs: [{ job_id: "old-1", verdict: "uncertain", verdict_reason: "旧待确认" }], total_kept: 1, total_dropped: 0 },
        });
      }
      if (url.includes("/api/filter-labels")) {
        // T507/T508：mock 返回新 PlatformFilterSchema 格式，含 schema_version
        return response({
          ok: true, platform: "boss", schema_version: 3, enabled_for_new_tasks: true,
          fields: [
            { key: "salary", label: "薪资范围", multiple: true, options: [{ value: "0", label: "不限" }, { value: "406", label: "20-50K" }] },
            { key: "experience", label: "经验要求", multiple: true, options: [{ value: "0", label: "不限" }] },
            { key: "stage", label: "融资阶段", multiple: true, options: [{ value: "0", label: "不限" }, { value: "804", label: "B轮" }] },
          ],
        });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      }
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
      profile_summary: "3年Python后端工程师候选人",
      // T508：提交当前平台 schema 版本（不发 platform，父 run 已冻结）
      filter_schema_version: 3,
    });
    // T508：execute-search 不发 platform 也不发 AI filters（已由其它测试覆盖 scope_digest）
    // T506：screening_fields 是 boss 平台草稿（draftPlatform 默认 boss）
    expect(JSON.parse(aiCall![1]!.body as string).screening_fields).not.toHaveProperty("stage");

    vi.unstubAllGlobals();
  });

  it("T509: restores zhilian interrupted AI screen task by loading zhilian schema/city but keeps draft boss", async () => {
    // platform-schema.md L157：恢复任务时先设置任务自身平台，再加载对应 schema/城市/筛选快照；
    // 不变式 2：setTaskPlatform 不改 draft/result。所以草稿仍是 BOSS，但已加载 schema/city 是 zhilian。
    // 不 click start-ai-screen：draft≠task 时会触发已知 UX 问题（zhilian 任务恢复后表单读 boss 草稿），
    // 由 T515 真实联调阶段修复；本会话只验证 schema/city/draft 三身份独立。
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "zhilian-interrupted",
          kind: "ai_screen", status: "interrupted",
          platform: "zhilian",
          scrape_task_id: "scrape-zhilian-1", scrape_completed: true,
          frozen_filters: { company_nature: ["1"] },
          profile_summary: "后端工程师",
        });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url.includes("/api/filter-labels")) {
        const platform = url.includes("platform=boss") ? "boss" : "zhilian";
        return response({
          ok: true, platform, schema_version: 1, enabled_for_new_tasks: true,
          fields: platform === "boss"
            ? [{ key: "stage", label: "融资阶段", multiple: false, options: [{ value: "804", label: "B轮" }] }]
            : [{ key: "company_nature", label: "公司性质", multiple: false, options: [{ value: "0", label: "不限" }, { value: "1", label: "国企" }] }],
        });
      }
      if (url.includes("/api/options")) {
        const platform = url.includes("platform=boss") ? "boss" : "zhilian";
        return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
      }
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

    // 任务平台为 zhilian：恢复时 setTaskPlatform(zhilian) + loadFilterLabels(zhilian) + loadCityCatalog(zhilian)
    const segment = wrapper.find(".platform-segment");
    expect(segment.exists()).toBe(true);
    expect(segment.attributes("data-loaded-schema-platform")).toBe("zhilian");
    expect(segment.attributes("data-loaded-city-platform")).toBe("zhilian");

    // 草稿平台仍是 BOSS（setTaskPlatform 不改 draft — platform-schema.md L142 不变式 2）
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="platform-segment-boss"]').attributes("aria-selected")).toBe("true");
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("aria-selected")).toBe("false");

    // interrupted screen 仍可重开（与 boss 任务对称）
    expect(wrapper.find('[data-testid="finish-interrupted-screen"]').exists()).toBe(true);
    expect(wrapper.find(".task-progress").exists()).toBe(false);

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
        // http-api.md L101-116：execute-search 必须显式携带 platform 字段（合同要求）。
        // T508 只禁止提交 AI filters / screening_fields，不禁止 platform。
        expect(JSON.parse(String(init?.body))).toEqual({
          platform: "boss",
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
    expect(wrapper.get('[data-testid="start-scrape"]').text()).toContain("单独抓取");
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

  it("removes a keyword chip completely with its delete x", async () => {
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
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="keyword-chip"]').exists()).toBe(true);
    const keywordChip = wrapper.get('[data-testid="keyword-chip"]').element.parentElement;
    expect(keywordChip?.classList.contains("keyword-chip")).toBe(true);
    expect(keywordChip?.querySelector('[data-testid="remove-keyword"]')).toBeTruthy();
    await wrapper.get('[data-testid="remove-keyword"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="keyword-chip"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="remove-keyword"]').exists()).toBe(false);
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
      if (url.includes("/api/filter-labels")) {
        const platform = url.includes("platform=boss") ? "boss" : "zhilian";
        return response({
          ok: true, platform, schema_version: 1, enabled_for_new_tasks: true,
          fields: platform === "boss"
            ? [{ key: "stage", label: "融资阶段", multiple: false, options: [{ value: "0", label: "不限" }, { value: "804", label: "B轮" }] }]
            : [{ key: "company_nature", label: "公司性质", multiple: false, options: [{ value: "0", label: "不限" }, { value: "1", label: "国企" }] }],
        });
      }
      if (url.includes("/api/options")) {
        const platform = url.includes("platform=boss") ? "boss" : "zhilian";
        return response({ ok: true, platform, city_mapping_version: 1, cities: [{ label: "上海", value: "上海" }] });
      }
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

    // 切换到智联：T505 起按草稿平台重新加载 schema + 城市（2 个新请求），
    // 但不改 task/result（task 仍为 null）。
    const callsBefore = fetchMock.mock.calls.length;
    await zhilianBtn.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(false);
    expect(zhilianBtn.attributes("aria-selected")).toBe("true");
    expect(bossBtn.attributes("aria-selected")).toBe("false");
    // 切换平台触发 schema + 城市 2 个新请求
    expect(fetchMock.mock.calls.length).toBe(callsBefore + 2);

    // 切回 BOSS
    await bossBtn.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(bossBtn.attributes("aria-selected")).toBe("true");

    vi.unstubAllGlobals();
  });

  it("locks draft platform switching while a task is running", async () => {
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
      if (url.includes("/api/filter-labels")) {
        const platform = url.includes("platform=boss") ? "boss" : "zhilian";
        return response({
          ok: true, platform, schema_version: 1, enabled_for_new_tasks: true,
          fields: platform === "boss"
            ? [{ key: "stage", label: "融资阶段", multiple: false, options: [] }]
            : [{ key: "company_nature", label: "公司性质", multiple: false, options: [] }],
        });
      }
      if (url.includes("/api/options")) {
        const platform = url.includes("platform=boss") ? "boss" : "zhilian";
        return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
      }
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

    // 任务被接回后 activeStep 仍是 upload；草稿平台分段控件在所有步骤都常驻顶部。
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    // 任务运行中平台切换被锁定（平台互切锁定）：按钮禁用、点击不生效，
    // 任务快照与 schema 不被改写。
    const zhilianBtn = wrapper.get('[data-testid="platform-segment-zhilian"]');
    expect(zhilianBtn.attributes("disabled")).toBeDefined();
    expect(zhilianBtn.attributes("title") || "").toContain("任务进行中");
    await zhilianBtn.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    const bossBtn = wrapper.get('[data-testid="platform-segment-boss"]');
    expect(bossBtn.attributes("disabled")).toBeDefined();
    await bossBtn.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    vi.unstubAllGlobals();
  });

  it("T505: drops stale schema response when platform switches quickly", async () => {
    // 节点门禁 B（tasks006.md L35）：首次应用异步响应前，必须有请求序号或取消机制测试，
    // 证明旧平台响应晚到不会覆盖当前平台。discovery.spec.ts 已在 loader 层覆盖 100 次；
    // 这里在组件层端到端验证：boss 旧响应晚到不覆盖 zhilian 当前选择。
    const pendingFetches: Array<{
      url: string;
      resolve: (value: Response) => void;
    }> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/filter-labels") || url.includes("/api/options")) {
        return new Promise<Response>((resolve) => {
          pendingFetches.push({ url, resolve });
        });
      }
      // 其它 endpoint 立即返回
      return Promise.resolve(response({
        ok: true, has_result: false, has_task: false, labels: {},
        selection: "balanced", settings: {}, last_custom: null, mode_version: null,
        manual_ranges: {}, config_schema_version: 1,
      }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    // 让初始 boss 的 advanced-settings 等先解析；filter-labels/options 仍 pending
    await flushPromises();

    // 切到智联：触发新 schema + 城市 请求（boss 的旧请求仍 pending）
    await wrapper.get('[data-testid="platform-segment-zhilian"]').trigger("click");
    await flushPromises();

    // 现在 pendingFetches 里至少有 4 个：boss filter-labels, boss options, zhilian filter-labels, zhilian options
    // 先 resolve zhilian 的响应（最新请求）
    for (const p of pendingFetches) {
      if (p.url.includes("platform=zhilian")) {
        if (p.url.includes("/api/filter-labels")) {
          p.resolve(response({
            ok: true, platform: "zhilian", schema_version: 1, enabled_for_new_tasks: true,
            fields: [{ key: "company_nature", label: "公司性质", multiple: false, options: [{ value: "1", label: "国企" }] }],
          }));
        } else if (p.url.includes("/api/options")) {
          p.resolve(response({ ok: true, platform: "zhilian", city_mapping_version: 1, cities: [] }));
        }
      }
    }
    await flushPromises();

    // 现在 resolve boss 的旧响应（晚到）——应被丢弃，不覆盖 zhilian
    for (const p of pendingFetches) {
      if (p.url.includes("platform=boss")) {
        if (p.url.includes("/api/filter-labels")) {
          p.resolve(response({
            ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true,
            fields: [{ key: "stage", label: "融资阶段", multiple: false, options: [{ value: "804", label: "B轮" }] }],
          }));
        } else if (p.url.includes("/api/options")) {
          p.resolve(response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] }));
        }
      }
    }
    await flushPromises();

    // 当前草稿仍是 zhilian（旧 boss 响应没改写）
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    // 已加载 schema 平台是 zhilian（boss 旧响应被丢弃）
    const segment = wrapper.find(".platform-segment");
    expect(segment.attributes("data-loaded-schema-platform")).toBe("zhilian");
    expect(segment.attributes("data-loaded-city-platform")).toBe("zhilian");

    vi.unstubAllGlobals();
  });

  // ---------- T513：8 类状态覆盖 ----------

  const t513Settings = {
    inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
    detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
    screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
  };

  function bossSchema(enabled = true) {
    return {
      ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: enabled,
      fields: [{ key: "stage", label: "融资阶段", multiple: false, options: [{ value: "804", label: "B轮" }] }],
    };
  }

  it("T513 empty state: no task and no result renders the default BOSS draft platform without task progress", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();

    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(wrapper.find(".task-progress").exists()).toBe(false);

    vi.unstubAllGlobals();
  });

  it("T513 loading state: schema platform is not advanced while the filter-labels fetch is pending", async () => {
    const pending: Array<{ url: string; resolve: (value: Response) => void }> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/filter-labels")) {
        return new Promise<Response>((resolve) => { pending.push({ url, resolve }); });
      }
      return Promise.resolve(response({
        ok: true, has_result: false, has_task: false,
        selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null,
        manual_ranges: {}, config_schema_version: 1,
      }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    // 先放行 boss 的初始 schema 请求
    const bossPending = pending.find((p) => p.url.includes("platform=boss"));
    if (bossPending) bossPending.resolve(response(bossSchema()));
    await flushPromises();

    await wrapper.get('[data-testid="platform-segment-zhilian"]').trigger("click");
    await flushPromises();

    // zhilian 的 filter-labels 仍 pending：loaded-schema-platform 不应前进到 zhilian
    const segment = wrapper.find(".platform-segment");
    expect(segment.attributes("data-loaded-schema-platform")).not.toBe("zhilian");

    // resolve zhilian 后才前进
    const zhilianPending = pending.find((p) => p.url.includes("platform=zhilian"));
    zhilianPending!.resolve(response({
      ok: true, platform: "zhilian", schema_version: 1, enabled_for_new_tasks: true, fields: [],
    }));
    await flushPromises();
    expect(segment.attributes("data-loaded-schema-platform")).toBe("zhilian");

    vi.unstubAllGlobals();
  });

  it("T513 success state: a completed historical result renders the completed task status", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true, has_result: true, source_run_id: "completed-run",
          result: { jobs: [], profile_summary: "历史画像", total_kept: 4, total_dropped: 0 },
          started_at: 1_000, finished_at: 2_000,
        });
      }
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("广泛抓取"))!.trigger("click");
    await flushPromises();

    expect(wrapper.find(".task-progress").text()).toContain("已完成");

    vi.unstubAllGlobals();
  });

  it("T513 failed state: a failed scrape start surfaces the failed task status", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [{ label: "上海", value: "上海" }] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:fail" },
          deduplicated: { keywords: ["python"], cities: ["上海"] },
        });
      }
      if (url.endsWith("/api/execute-search")) {
        return response({ ok: false, error_code: "source_unreachable", user_message: "浏览器自动化启动失败" }, 500);
      }
      return response({ init });
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();

    const status = wrapper.get(".task-status");
    expect(status.attributes("data-status")).toBe("failed");
    expect(status.text()).toContain("执行失败");

    vi.unstubAllGlobals();
  });

  it("D7: login-required failure shows an account login guide that opens the accounts panel", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [{ label: "上海", value: "上海" }] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:login" },
          deduplicated: { keywords: ["python"], cities: ["上海"] },
        });
      }
      if (url.endsWith("/api/execute-search")) {
        return response({ ok: false, error_code: "source_login_required", user_message: "请先登录" }, 409);
      }
      if (url.endsWith("/api/browser-accounts")) {
        return response({ accounts: [{ id: "a", name: "账号A" }], active_account: "a" });
      }
      return response({ init });
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();

    const guide = wrapper.get('[data-testid="login-guide"]');
    expect(guide.text()).toContain("BOSS");
    expect(guide.text()).toContain("默认账号");

    await wrapper.get('[data-testid="open-accounts-from-guide"]').trigger("click");
    expect(wrapper.emitted("open-browser-accounts")).toHaveLength(1);

    vi.unstubAllGlobals();
  });

  it("T513 paused state: a paused scrape task shows pause reason and cancel/finish actions", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "paused-1", kind: "scrape", status: "paused",
          platform: "boss",
          pause_info: { error_code: "captcha_required", error_reason: "触发验证码" },
        });
      }
      if (url.includes("/api/task-state/paused-1")) {
        return response({ status: "paused", success_count: 2, fail_count: 0, unstarted_count: 3, total: 5, pause_info: { error_code: "captcha_required", error_reason: "触发验证码" } });
      }
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();

    const status = wrapper.get(".task-status");
    expect(status.attributes("data-status")).toBe("paused");
    expect(status.text()).toContain("已暂停");
    expect(wrapper.get('[data-testid="pause-reason"]').text()).toContain("触发验证码");
    expect(wrapper.find('[data-testid="cancel-paused-scrape"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="finish-paused-scrape"]').exists()).toBe(true);

    vi.unstubAllGlobals();
  });

  it("T513 partial state: a completed_with_pending result renders the partial status tone", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true, has_result: true, source_run_id: "partial-run", status: "completed_with_pending",
          result: {
            jobs: [{ job_id: "j1", title: "前端", verdict: "uncertain", verdict_reason: "详情超时" }],
            total_kept: 1, total_dropped: 0,
          },
        });
      }
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("广泛抓取"))!.trigger("click");
    await flushPromises();

    // completed_with_pending 历史结果：task-progress 显示 partial 文案，scope 仍可编辑
    expect(wrapper.find(".task-progress").text()).toContain("完成，但有待确认");

    vi.unstubAllGlobals();
  });

  it("T513 no source evidence: recrawl carries empty source_run_id instead of fabricating one", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        // 合并载入按平台分别查询；智联无结果，只有 BOSS 这一份。
        if (url.includes("platform=zhilian")) {
          return response({ ok: true, has_result: false });
        }
        return response({
          ok: true, has_result: true,
          // 故意不带 source_run_id：前端不得伪造来源证据
          result: {
            jobs: [{ job_id: "pending-1", title: "前端", verdict: "uncertain", verdict_reason: "详情超时" }],
            total_kept: 1, total_dropped: 0,
          },
        });
      }
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/pipeline/recrawl")) {
        return response({ ok: true, task_id: "recrawl-nosrc" }, 202);
      }
      if (url.includes("/api/task-state/recrawl-nosrc")) {
        return response({ status: "paused", progress: {}, logs: [], error: "验证码" });
      }
      return response({ init });
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    const resultsStep = wrapper.findAll("button").find((b) => b.text().includes("查看结果"));
    await resultsStep?.trigger("click");
    await flushPromises();
    // “全部重抓”只在单平台视图可见：先切到 BOSS 视图再触发。
    await wrapper.get('[data-testid="result-platform-filter-boss"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="recrawl-uncertain"]').trigger("click");
    await flushPromises();

    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/pipeline/recrawl"));
    expect(call).toBeDefined();
    // 无 source 证据时 source_run_id 为空串，不是伪造的 id（platform-schema.md 不变式：前端不猜来源）
    expect(JSON.parse(String(call?.[1]?.body)).source_run_id).toBe("");

    vi.unstubAllGlobals();
  });

  it("T513 platform disabled: zhilian schema with enabled_for_new_tasks=false disables new task entry", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) {
        const platform = url.includes("platform=boss") ? "boss" : "zhilian";
        return response(platform === "boss"
          ? bossSchema()
          : { ok: true, platform: "zhilian", schema_version: 1, enabled_for_new_tasks: false, fields: [] });
      }
      if (url.includes("/api/options")) {
        const platform = url.includes("platform=boss") ? "boss" : "zhilian";
        return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    // 进入 search 步骤后 start-scrape / 禁用提示才会渲染
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await flushPromises();
    // 切到智联：schema 标记 enabled_for_new_tasks=false
    await wrapper.get('[data-testid="platform-segment-zhilian"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="platform-disabled-notice"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="start-scrape"]').attributes("disabled")).toBeDefined();

    // 切回 BOSS：恢复可用
    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-disabled-notice"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="start-scrape"]').attributes("disabled")).toBeUndefined();

    vi.unstubAllGlobals();
  });

  // ---------- Task 009：详情 slot 接入 JobLifecycleActions ----------

  function lifecycleJobFixture() {
    return {
      // 故意给一个平台原始 ID：完整三元组存在时不得被当作内部 job_id 发送。
      job_id: "raw-platform-id",
      platform: "zhilian",
      platform_job_id: "z-1",
      canonical_url: "https://www.zhaopin.com/jobdetail/z-1.htm",
      title: "Python 后端工程师",
      company: "示例公司",
      salary: "20-30K",
      location: "上海",
      verdict: "match",
    };
  }

  function lifecycleStateFixture(overrides: Record<string, unknown> = {}) {
    return {
      profile_id: "profile-1",
      job_id: "internal-uuid-1",
      status: "applied",
      applied_at: "2026-05-01T02:00:00+00:00",
      last_follow_up_at: null,
      revision: 1,
      reminder: { eligible: true, baseline_at: "2026-05-01T02:00:00+00:00", elapsed_seconds: 8294400, elapsed_days: 96 },
      ...overrides,
    };
  }

  function lifecycleFetchMock(options: {
    state?: unknown;
    action?: (url: string, init?: RequestInit) => Response | Promise<Response>;
    exportCsv?: (url: string) => Response | Promise<Response>;
  } = {}) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/pipeline-result/export.csv")) {
        if (options.exportCsv) return options.exportCsv(url);
        return new Response("title,job_link\n", {
          status: 200,
          headers: { "Content-Type": "text/csv" },
        });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true, has_result: true, source_run_id: "run-lifecycle",
          result: { jobs: [lifecycleJobFixture()], total_kept: 1, total_dropped: 0 },
        });
      }
      if (url.startsWith("/api/profile-jobs/state")) {
        return response(options.state ?? { ok: true, exists: true, state: lifecycleStateFixture() });
      }
      if (url.endsWith("/api/profile-jobs/actions")) {
        if (options.action) return options.action(url, init ?? {});
        return response({
          ok: true, replayed: false, changed: true, event_id: "ev-1", event_sequence: 1,
          state: lifecycleStateFixture({ revision: 2 }),
        });
      }
      if (url.includes("/events")) {
        // 轨迹浮窗自动加载事件：默认返回空轨迹。
        return response({ ok: true, events: [], next_after_sequence: 0 });
      }
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "custom", settings: {}, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.includes("/api/filter-labels")) {
        return response({ ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      }
      return response({});
    });
  }

  it("B031: one-click button leads scrape and auto AI screening with consumed marker", async () => {
    const fetchMock = oneClickBase({
      "/api/execute-search": (url, init) => {
        expect(JSON.parse(String(init?.body))).toMatchObject({
          platform: "boss",
          auto_screen: true,
          auto_screen_fields: { salary: ["406"], stage: ["804"] },
          auto_screen_profile: "3年Python后端候选人",
        });
        return response({ ok: true, task_id: "one-scrape" });
      },
      "/api/task-state/one-scrape": () => response({ status: "completed", progress: {}, logs: [], platform: "boss", scraped_count: 1 }),
      "/api/ai-screen": (url, init) => {
        expect(JSON.parse(String(init?.body))).toMatchObject({
          consume_auto_screen: true,
          screening_fields: { salary: ["406"], stage: ["804"] },
          profile_summary: "3年Python后端候选人",
        });
        return response({ ok: true, task_id: "one-screen" });
      },
      "/api/task-state/one-screen": () => response({ status: "completed", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
    const buttons = wrapper.findAll('.one-click-filter-groups button');
    await buttons.find((b) => b.text() === "20-50K")!.trigger("click");
    await buttons.find((b) => b.text() === "B轮")!.trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();

    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(1);
    expect(wrapper.find(".results-stage").exists()).toBe(true);
    const notices = wrapper.emitted("notify")?.flat() as Array<{ message: string }>;
    expect(notices.some((n) => n.message.includes("正在自动开始 AI 筛选"))).toBe(true);
    expect(notices.some((n) => n.message.includes("请继续确认"))).toBe(false);
    vi.unstubAllGlobals();
  });

  async function mountAtResults(fetchMock: ReturnType<typeof vi.fn>) {
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("查看结果"))!.trigger("click");
    await flushPromises();
    return wrapper;
  }

  /** 轨迹已收敛为浮窗：点“查看轨迹”打开居中弹窗后才渲染生命周期组件。 */
  async function openLifecycleDialog(wrapper: ReturnType<typeof mount>) {
    await wrapper.get('[data-testid="open-lifecycle-dialog"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="lifecycle-dialog"]').exists()).toBe(true);
  }

  it("loads detail lifecycle state read-only with the authoritative triple and never auto mark_read", async () => {
    const fetchMock = lifecycleFetchMock();
    const wrapper = await mountAtResults(fetchMock);

    // 详情区不再内嵌大卡片，只有一个与收藏/不感兴趣同排的“查看轨迹”按钮。
    expect(wrapper.find('[data-testid="job-lifecycle-actions"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="open-lifecycle-dialog"]').exists()).toBe(true);
    await openLifecycleDialog(wrapper);
    expect(wrapper.find('[data-testid="job-lifecycle-actions"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已投递");

    // 打开浮窗时的初始加载只 GET state：用权威三元组解析，不把平台原始 ID 当内部 job_id。
    const stateCall = fetchMock.mock.calls.find(([u]) => String(u).startsWith("/api/profile-jobs/state"));
    expect(stateCall).toBeTruthy();
    const stateUrl = String(stateCall![0]);
    expect(stateUrl).toContain("profile_id=profile-1");
    expect(stateUrl).toContain("platform=zhilian");
    expect(stateUrl).toContain("platform_job_id=z-1");
    expect(stateUrl).toContain(`canonical_url=${encodeURIComponent("https://www.zhaopin.com/jobdetail/z-1.htm")}`);
    // 不带内部 job_id 参数（注意 platform_job_id= 子串包含 job_id=，需按参数边界判断）。
    expect(`${stateUrl}&`).not.toContain("&job_id=");
    // 只查看不得发送任何生命周期写命令（FR-002）。
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/api/profile-jobs/actions"))).toBe(false);

    vi.unstubAllGlobals();
  });

  it("adopts the server job_id after a successful action and emits job-feedback-changed", async () => {
    let capturedBody: Record<string, unknown> | null = null;
    const fetchMock = lifecycleFetchMock({
      action: (_url, init) => {
        capturedBody = JSON.parse(String((init ?? {}).body));
        return response({
          ok: true, replayed: false, changed: true, event_id: "ev-2", event_sequence: 2,
          state: lifecycleStateFixture({ revision: 2, last_follow_up_at: "2026-08-05T02:00:00+00:00" }),
        });
      },
    });
    const wrapper = await mountAtResults(fetchMock);
    await openLifecycleDialog(wrapper);

    await wrapper.get('[data-testid="lca-action-follow_up"]').trigger("click");
    await flushPromises();

    // 写命令携带内部 job_id（初始只读加载的服务端返回值），不是平台原始 ID。
    expect(capturedBody).toMatchObject({
      profile_id: "profile-1",
      action: "follow_up",
      job: { job_id: "internal-uuid-1" },
    });
    expect(capturedBody!.request_id).toBeTruthy();
    // 成功后通知 App 刷新当前 profile 的 count/list。
    expect(wrapper.emitted("job-feedback-changed")).toBeTruthy();
    expect(wrapper.emitted("job-feedback-changed")![0]).toEqual([{ profileId: "profile-1", jobId: "internal-uuid-1" }]);

    vi.unstubAllGlobals();
  });

  it("keeps the original state and shows the API message when an action fails", async () => {
    const fetchMock = lifecycleFetchMock({
      action: () => response(
        { ok: false, error_code: "state_precondition_failed", user_message: "当前状态不支持该操作" },
        409,
      ),
    });
    const wrapper = await mountAtResults(fetchMock);
    await openLifecycleDialog(wrapper);

    await wrapper.get('[data-testid="lca-action-follow_up"]').trigger("click");
    await flushPromises();

    // 失败保留原状态（FR-037），不乐观更新，也不发出刷新事件。
    expect(wrapper.get('[data-testid="lca-action-error"]').text()).toContain("当前状态不支持该操作");
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已投递");
    expect(wrapper.emitted("job-feedback-changed")).toBeFalsy();

    vi.unstubAllGlobals();
  });

  it("drops a late action response after the profile prop switches", async () => {
    let resolveAction!: (value: Response) => void;
    const pendingAction = new Promise<Response>((resolve) => { resolveAction = resolve; });
    const fetchMock = lifecycleFetchMock({ action: () => pendingAction });
    const wrapper = await mountAtResults(fetchMock);
    await openLifecycleDialog(wrapper);

    await wrapper.get('[data-testid="lca-action-follow_up"]').trigger("click");
    await flushPromises();

    // 切换到新 profile：旧 action 的响应晚到后不得覆盖新 state、不得发事件。
    await wrapper.setProps({ profileId: "profile-2" });
    await flushPromises();
    resolveAction(response({
      ok: true, replayed: false, changed: true, event_id: "ev-3", event_sequence: 3,
      state: { ...lifecycleStateFixture(), profile_id: "profile-1", revision: 9 },
    }));
    await flushPromises();

    expect(wrapper.emitted("job-feedback-changed")).toBeFalsy();
    expect(wrapper.find('[data-testid="lca-action-error"]').exists()).toBe(false);

    vi.unstubAllGlobals();
  });

  it("exports grouped CSV from the results header using the current run id", async () => {
    let exportUrl = "";
    const fetchMock = lifecycleFetchMock({
      exportCsv: (url) => {
        exportUrl = url;
        return new Response("title,job_link\n匹配：,\n", {
          status: 200,
          headers: {
            "Content-Type": "text/csv",
            "Content-Disposition": "attachment; filename=career_scout_jobs_zhilian.csv",
          },
        });
      },
    });
    const createObjectURL = vi.fn(() => "blob:export-csv");
    const revokeObjectURL = vi.fn();
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    try {
      const wrapper = await mountAtResults(fetchMock);

      await wrapper.get('[data-testid="export-result-csv"]').trigger("click");
      await flushPromises();

      // 导出必须按当前结果的 run_id 请求分组 CSV，并触发浏览器下载
      expect(exportUrl).toBe("/api/pipeline-result/export.csv?run_id=run-lifecycle");
      expect(createObjectURL).toHaveBeenCalled();
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:export-csv");
    } finally {
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
      vi.unstubAllGlobals();
    }
  });

  it("search panels are expanded by default, toggle together, and collapse on start", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:panels" },
          deduplicated: { keywords: ["python"], cities: ["上海"] },
        });
      }
      if (url.endsWith("/api/execute-search")) {
        // 保持 pending：抓取任务停留在运行态，验证「开始抓取后自动收拢」。
        return new Promise<Response>(() => { /* noop */ });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await flushPromises();

    const keywordCard = ".search-layout > .collapsible-card:first-child";
    const advancedCard = ".advanced-panel";

    // ① 默认展开
    expect(wrapper.find(`${keywordCard} .collapsible-body.open`).exists()).toBe(true);
    expect(wrapper.find(`${advancedCard} .collapsible-body.open`).exists()).toBe(true);

    // ② 点任意卡头：两卡联动收起
    await wrapper.get(`${keywordCard} .collapsible-header`).trigger("click");
    await flushPromises();
    expect(wrapper.find(`${keywordCard} .collapsible-body.open`).exists()).toBe(false);
    expect(wrapper.find(`${advancedCard} .collapsible-body.open`).exists()).toBe(false);

    // ③ 点另一卡头：两卡联动展开
    await wrapper.get(`${advancedCard} .collapsible-header`).trigger("click");
    await flushPromises();
    expect(wrapper.find(`${keywordCard} .collapsible-body.open`).exists()).toBe(true);
    expect(wrapper.find(`${advancedCard} .collapsible-body.open`).exists()).toBe(true);

    // ④ 重新展开、配置关键词城市、开始抓取：自动收拢
    await wrapper.get(`${keywordCard} .collapsible-header`).trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(`${keywordCard} .collapsible-body.open`).exists()).toBe(false);
    expect(wrapper.find(`${advancedCard} .collapsible-body.open`).exists()).toBe(false);

    vi.unstubAllGlobals();
  });
  it("B008: opens screen filter card without a result and collapses after AI screening starts", async () => {
    const settings = {
      pages: 3, inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:b008" }, deduplicated: { keywords: ["python"], cities: ["上海"] } });
      }
      if (url.endsWith("/api/execute-search")) return response({ ok: true, task_id: "scrape-b008" });
      if (url.includes("/api/task-state/scrape-b008")) return response({ status: "completed", progress: {}, logs: [] });
      if (url.endsWith("/api/ai-screen")) return new Promise<Response>(() => { /* noop */ });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    const screenCard = ".workflow-stack > .collapsible-card";
    expect(wrapper.find(`${screenCard} .collapsible-body.open`).exists()).toBe(true);
    await wrapper.get('[data-testid="start-ai-screen"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(`${screenCard} .collapsible-body.open`).exists()).toBe(false);
    vi.unstubAllGlobals();
  });

  it("B008: keeps screen filter card collapsed when returning from an existing result page", async () => {
    const settings = {
      pages: 3, inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true, has_result: true, source_run_id: "run-b008", status: "completed",
          result: { jobs: [{ job_id: "j-1", title: "岗位", company: "公司", verdict: "match" }], total_kept: 1, total_dropped: 0 },
        });
      }
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("广泛抓取"))!.trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("AI 筛选"))!.trigger("click");
    await flushPromises();
    const screenCard = ".workflow-stack > .collapsible-card";
    expect(wrapper.find(`${screenCard} .collapsible-body.open`).exists()).toBe(false);
    vi.unstubAllGlobals();
  });

  it("B009/B011: re-projects resume suggestions to zhilian and renders Chinese chips", async () => {
    const settings = {
      pages: 3, inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const bossSchemaRich = {
      ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true,
      fields: [
        { key: "experience", label: "经验要求", multiple: true, options: [{ value: "105", label: "3-5年" }] },
        { key: "stage", label: "融资阶段", multiple: true, options: [{ value: "804", label: "B轮" }] },
      ],
    };
    const zhilianSchemaRich = {
      ok: true, platform: "zhilian", schema_version: 2, enabled_for_new_tasks: true,
      fields: [
        { key: "experience", label: "经验要求", multiple: true, options: [{ value: "0305", label: "3-5年" }] },
        { key: "company_nature", label: "公司性质", multiple: true, options: [{ value: "1", label: "国企" }] },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) {
        return response(url.includes("platform=zhilian") ? zhilianSchemaRich : bossSchemaRich);
      }
      if (url.includes("/api/options")) {
        const platform = url.includes("platform=zhilian") ? "zhilian" : "boss";
        return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/analyze-resume")) {
        return response({
          ok: true, platform: "boss", filter_schema_version: 1,
          fields: { keyword: [{ word: "Python 后端", recommended: true }], city: ["上海"], experience: ["105"], stage: ["804"], company_nature: ["1"], profile_summary: "3年Python后端" },
          semantic: { experience: ["3-5年"], stage: ["B轮"], company_nature: ["国企"] },
          labels: {},
        });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({ ok: true, scope: { keywords: ["Python 后端"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:zhilian-b009" }, deduplicated: { keywords: ["python 后端"], cities: ["上海"] } });
      }
      if (url.endsWith("/api/execute-search")) {
        const body = JSON.parse(String(init?.body));
        expect(body.platform).toBe("zhilian");
        expect(body.scope_digest).toBe("sha256:zhilian-b009");
        return response({ ok: true, task_id: "scrape-zhilian-b009" });
      }
      if (url.includes("/api/task-state/scrape-zhilian-b009")) return response({ status: "completed", progress: {}, logs: [] });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    const file = new File(["resume"], "resume.txt", { type: "text/plain" });
    Object.defineProperty(wrapper.get('[data-testid="resume-input"]').element, "files", { value: [file], configurable: true });
    await wrapper.get('[data-testid="resume-input"]').trigger("change");
    await wrapper.get('[data-testid="resume-consent"]').setValue(true);
    await wrapper.get('[data-testid="analyze-resume"]').trigger("click");
    await flushPromises();

    await wrapper.get('[data-testid="platform-segment-zhilian"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    const chipsText = wrapper.get(".summary-chips").text();
    expect(wrapper.get('[data-testid="platform-segment-boss"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("disabled")).toBeDefined();
    expect(chipsText).toContain("经验要求: 3-5年");
    expect(chipsText).toContain("公司性质: 国企");
    expect(chipsText).not.toContain("融资阶段");
    expect(chipsText).not.toContain("105");
    expect(chipsText).not.toContain("0305");
    vi.unstubAllGlobals();
  });

  it("B007: confirms before discarding a completed scrape that has not entered AI screening", async () => {
    const settings = {
      pages: 3, inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    let scrapeCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        const body = JSON.parse(String(init?.body));
        const digest = body.platform === "zhilian" ? "sha256:zhilian-fresh" : "sha256:boss-round";
        return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: digest }, deduplicated: { keywords: ["python"], cities: ["上海"] } });
      }
      if (url.endsWith("/api/execute-search")) {
        scrapeCount += 1;
        const body = JSON.parse(String(init?.body));
        if (scrapeCount === 1) {
          expect(body.platform).toBe("boss");
          expect(body.scope_digest).toBe("sha256:boss-round");
          return response({ ok: true, task_id: "scrape-boss-round" });
        }
        expect(body.platform).toBe("zhilian");
        expect(body.scope_digest).toBe("sha256:zhilian-fresh");
        return response({ ok: true, task_id: "scrape-zhilian-fresh" });
      }
      if (url.includes("/api/task-state/scrape-boss-round")) return response({ status: "completed", progress: {}, logs: [] });
      if (url.includes("/api/task-state/scrape-zhilian-fresh")) return response({ status: "completed", progress: {}, logs: [] });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(true);

    await wrapper.get('[data-testid="platform-segment-zhilian"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-switch-confirm"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    await wrapper.get('[data-testid="cancel-platform-switch"]').trigger("click");
    expect(wrapper.find('[data-testid="platform-switch-confirm"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    await wrapper.get('[data-testid="platform-segment-zhilian"]').trigger("click");
    await wrapper.get('[data-testid="confirm-platform-switch"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);

    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(true);
    expect(scrapeCount).toBe(2);
    vi.unstubAllGlobals();
  });

  it("B007: locks platform switching on the results page", async () => {
    const settings = {
      pages: 3, inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: true, source_run_id: "run-results", status: "completed", result: { jobs: [], total_kept: 0, total_dropped: 0 } });
      }
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("查看结果"))!.trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="platform-segment-boss"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("disabled")).toBeDefined();
    vi.unstubAllGlobals();
  });

  it("R2: a completed live screen task merges both platform results instead of showing only one", async () => {
    const settings = {
      pages: 3,
      inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    // 实时任务完成前没有历史结果；完成后 latest-pipeline-result 才返回数据。
    let screenDone = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) {
        if (!screenDone) return response({ ok: true, has_result: false });
        if (url.includes("platform=zhilian")) {
          return response({
            ok: true, has_result: true, source_run_id: "run-zhilian", status: "completed",
            started_at: 2_000, finished_at: 3_000,
            result: {
              jobs: [{ job_id: "z-1", title: "智联岗位", company: "智联公司", verdict: "match" }],
              total_scraped: 1, total_matched: 1, total_kept: 1, total_dropped: 0,
            },
          });
        }
        return response({
          ok: true, has_result: true, source_run_id: "run-boss", status: "completed",
          started_at: 1_000, finished_at: 2_000,
          result: {
            jobs: [{ job_id: "b-1", title: "BOSS岗位", company: "BOSS公司", verdict: "match" }],
            total_scraped: 1, total_matched: 1, total_kept: 1, total_dropped: 0,
          },
        });
      }
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:r2" },
          deduplicated: {},
        });
      }
      if (url.endsWith("/api/execute-search")) return response({ ok: true, task_id: "scrape-r2" });
      if (url.endsWith("/api/task-state/scrape-r2")) {
        return response({ ok: true, status: "completed", progress: {}, logs: [], platform: "boss" });
      }
      if (url.endsWith("/api/ai-screen")) return response({ ok: true, task_id: "screen-r2" });
      if (url.endsWith("/api/task-state/screen-r2")) {
        screenDone = true;
        return response({
          ok: true, status: "completed", progress: {}, logs: [], platform: "boss",
          result: { jobs: [{ job_id: "b-1", title: "BOSS岗位" }], total_scraped: 1, total_matched: 1, total_kept: 1, total_dropped: 0 },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-ai-screen"]').trigger("click");
    await flushPromises();

    // 完成路径必须拉两个平台的最近结果做合并（刷新路径的行为）
    const mergeCalls = fetchMock.mock.calls
      .filter(([u]) => String(u).includes("/api/latest-pipeline-result"))
      .map(([u]) => String(u));
    expect(mergeCalls.some((u) => u.includes("platform=boss"))).toBe(true);
    expect(mergeCalls.some((u) => u.includes("platform=zhilian"))).toBe(true);
    // 合并后两个平台的岗位同时在结果页可见（切平台筛选不再全 0）
    const rows = wrapper.findAll('[data-testid="job-row"]');
    const rowText = rows.map((row) => row.text()).join(" | ");
    expect(rowText).toContain("BOSS岗位");
    expect(rowText).toContain("智联岗位");

    vi.unstubAllGlobals();
  });

  it("R4: saving advanced settings submits pages together with the speed fields", async () => {
    const settings = {
      pages: 3,
      inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings/custom")) {
        return response({ ok: true, selection: "custom", config_digest: "sha256:r4", settings });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="pages-per-combination"]').setValue(1);
    await wrapper.findAll("button").find((b) => b.text().includes("保存高级设置"))!.trigger("click");
    await flushPromises();

    const putCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/advanced-settings/custom"));
    expect(putCall).toBeTruthy();
    const body = JSON.parse(String(putCall![1]!.body));
    expect(body.settings.pages).toBe(1);
    expect(body.settings.detail_batch_size).toBe(15);
    expect(body.settings.screen_batch_size).toBe(50);

    vi.unstubAllGlobals();
  });

  it("B027: failed scrape restores real count and can finish without jumping to results", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "scrape-failed-1", kind: "scrape",
          status: "failed", platform: "boss", scraped_count: 1280, source_total: 3000,
          progress: { message: "抓取失败" }, logs: [], error: "列表抓取失败",
          pause_info: { error_code: "scrape_failed", error_reason: "列表抓取失败" },
        });
      }
      if (url.includes("/api/task/finish/scrape-failed-1")) {
        return response({
          ok: true, run_id: "scrape-failed-1", snapshot_run_id: "snap-1",
          platform: "boss", status: "completed_with_pending", scrape_task_id: "scrape-failed-1",
          result: {
            jobs: [{ job_id: "j1", platform: "boss", verdict: "uncertain", verdict_reason: "提前结束" }],
            dropped: [], total_scraped: 1280, total_kept: 1280, total_dropped: 0,
          },
        });
      }
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    expect(wrapper.get('[data-testid="scraped-count"]').text()).toContain("1280");
    await wrapper.get('[data-testid="finish-active-scrape"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(".results-stage").exists()).toBe(false);
    expect(wrapper.find('[data-testid="view-partial-results"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="continue-ai-after-finish"]').exists()).toBe(true);
    vi.unstubAllGlobals();
  });

  it("B027: running AI screen can finish and save without jumping to results", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "screen-running-1", kind: "ai_screen",
          status: "running", platform: "boss", scrape_task_id: "scrape-parent",
          scrape_completed: true, progress: { message: "AI 筛选中" }, logs: [], error: "",
        });
      }
      if (url.includes("/api/task-state/screen-running-1")) {
        return response({ status: "running", progress: { message: "AI 筛选中" }, logs: [] });
      }
      if (url.includes("/api/task/finish/screen-running-1")) {
        return response({
          ok: true, run_id: "screen-running-1", snapshot_run_id: "snap-screen",
          platform: "boss", status: "completed_with_pending", scrape_task_id: "scrape-parent",
          result: {
            jobs: [{ job_id: "s1", platform: "boss", verdict: "uncertain" }],
            dropped: [], total_scraped: 1, total_kept: 1, total_dropped: 0,
          },
        });
      }
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("AI 筛选"))!.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="finish-active-screen"]').exists()).toBe(true);
    await wrapper.get('[data-testid="finish-active-screen"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(".results-stage").exists()).toBe(false);
    expect(wrapper.find('[data-testid="view-partial-results-screen"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="continue-ai-after-finish-screen"]').exists()).toBe(true);
    vi.unstubAllGlobals();
  });

  it("B027: latest result restores scrape task id and AI screen uses it", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) return response({ ok: true, has_result: false });
        return response({
          ok: true, has_result: true, source_run_id: "run-boss", platform: "boss",
          scrape_task_id: "scrape-parent", status: "completed_with_pending",
          started_at: 1000, finished_at: 2000,
          result: {
            jobs: [{ job_id: "j1", platform: "boss", verdict: "uncertain", verdict_reason: "待确认" }],
            total_kept: 1, total_dropped: 0, profile_summary: "3年Python后端候选人",
          },
        });
      }
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/ai-screen")) return response({ ok: true, task_id: "screen-1" });
      if (url.includes("/api/task-state/screen-1")) return response({ status: "completed", progress: {}, logs: [] });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("AI 筛选"))!.trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-ai-screen"]').trigger("click");
    await flushPromises();
    const aiCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/ai-screen"));
    expect(aiCall).toBeTruthy();
    expect(JSON.parse(String(aiCall![1]!.body))).toMatchObject({ scrape_task_id: "scrape-parent" });
    vi.unstubAllGlobals();
  });

  it("B030: all view guides platform selection and single platform recrawls by its own run", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) {
          return response({
            ok: true, has_result: true, source_run_id: "run-zhilian", platform: "zhilian",
            scrape_task_id: "scrape-z", started_at: 1000,
            result: { jobs: [{ job_id: "z1", platform: "zhilian", verdict: "uncertain", verdict_reason: "待确认" }], total_kept: 1, total_dropped: 0 },
          });
        }
        return response({
          ok: true, has_result: true, source_run_id: "run-boss", platform: "boss",
          scrape_task_id: "scrape-b", started_at: 2000,
          result: { jobs: [{ job_id: "b1", platform: "boss", verdict: "uncertain", verdict_reason: "待确认" }], total_kept: 1, total_dropped: 0 },
        });
      }
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/pipeline/recrawl")) return response({ ok: true, task_id: "recrawl-1" }, 202);
      if (url.includes("/api/task-state/recrawl-1")) return response({ status: "completed", progress: {}, logs: [] });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("查看结果"))!.trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("待确认"))!.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="recrawl-uncertain"]').exists()).toBe(true);
    await wrapper.get('[data-testid="recrawl-uncertain"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="recrawl-platform-guide"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="recrawl-platform-guide"]').text()).toContain("BOSS 1 · 智联 1");
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/pipeline/recrawl")).length).toBe(0);
    await wrapper.get('[data-testid="recrawl-choose-boss"]').trigger("click");
    await flushPromises();
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/pipeline/recrawl"));
    expect(call).toBeTruthy();
    expect(JSON.parse(String(call![1]!.body))).toMatchObject({ source_run_id: "run-boss", job_ids: ["b1"] });
    await wrapper.get('[data-testid="result-platform-filter-zhilian"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="recrawl-uncertain"]').exists()).toBe(true);
    vi.unstubAllGlobals();
  });

  it("B030: heading CSS keeps platform slider in flow at all widths", () => {
    const css = readFileSync(path.join(__dirname, "../../styles.css"), "utf8");
    const segmentBlock = css.match(/\.result-platform-segment\s*\{[^}]*\}/s)?.[0] || "";
    const headingBlock = css.match(/\.job-list-heading\s*\{[^}]*\}/s)?.[0] || "";
    expect(segmentBlock).not.toContain("position: absolute");
    expect(headingBlock).toContain("flex-wrap: wrap");
    const recrawlBannerBlock = css.match(/\.recrawl-banner\s*\{[^}]*\}/s)?.[0] || "";
    expect(recrawlBannerBlock).toContain("flex-flow: row wrap");
    const resultsStageBlock = css.match(/\.results-stage\s*\{[^}]*\}/s)?.[0] || "";
    expect(resultsStageBlock).toContain("grid-template-columns: minmax(0, 1fr)");
    const viewportScript = readFileSync(
      path.join(__dirname, "../../../../tests/sc015_viewport_check.py"), "utf8",
    );
    expect(viewportScript).toContain("(390, 844)");
    expect(viewportScript).toContain("recrawl-uncertain");
    expect(viewportScript).toContain("overlap");
  });

  // ---------- B031/B032：一键筛选并 AI 优化 ----------

  function oneClickSchema() {
    return {
      ok: true, platform: "boss", schema_version: 9, enabled_for_new_tasks: true,
      fields: [
        { key: "salary", label: "薪资范围", multiple: true, options: [
          { value: "0", label: "不限" },
          { value: "406", label: "20-50K" },
          { value: "807", label: "50-100K" },
        ] },
        { key: "stage", label: "融资阶段", multiple: false, options: [{ value: "804", label: "B轮" }] },
      ],
    };
  }

  async function oneClickSearch(wrapper: ReturnType<typeof mount>) {
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
  }

  function oneClickBase(overrides: Record<string, (url: string, init?: RequestInit) => Promise<Response> | Response> = {}) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (overrides[url]) return overrides[url](url, init);
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(oneClickSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:one" }, deduplicated: {} });
      }
      return response({});
    });
  }

  it("B031: empty search scope does not open the dialog", async () => {
    const fetchMock = oneClickBase();
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="one-click-confirm"]').exists()).toBe(false);
    const notices = wrapper.emitted("notify")?.flat() as Array<{ message: string }>;
    expect(notices.some((n) => n.message.includes("请先到第二步补齐关键词和城市"))).toBe(true);
    vi.unstubAllGlobals();
  });

  it("B032: profile under 10 blocks one-click and 10 chars with spaces passes", async () => {
    const fetchMock = oneClickBase();
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await wrapper.get('.profile-summary-input').setValue("太短");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="one-click-confirm"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="profile-inline-error"]').text()).toContain("至少 10 个字");
    expect(wrapper.get('.profile-summary-input').attributes("aria-invalid")).toBe("true");

    await wrapper.get('.profile-summary-input').setValue("  3年Python后端  ");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="one-click-confirm"]').exists()).toBe(true);
    vi.unstubAllGlobals();
  });

  it("B032: start AI screen is blocked by a short profile while start scrape is not", async () => {
    const fetchMock = oneClickBase({
      "/api/execute-search": () => response({ ok: true, task_id: "scrape-short" }),
      "/api/task-state/scrape-short": () => response({ status: "completed", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await wrapper.get('.profile-summary-input').setValue("短画像");
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/api/execute-search"))).toBe(true);
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await wrapper.get('[data-testid="start-ai-screen"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(0);
    vi.unstubAllGlobals();
  });

  it("B031: old result shows replacement hint in the dialog", async () => {
    const fetchMock = oneClickBase();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) return response({ ok: true, has_result: false });
        return response({
          ok: true, has_result: true, source_run_id: "old-run", platform: "boss",
          result: { jobs: [{ job_id: "old-1", title: "旧岗位" }], total_kept: 1, total_dropped: 0, profile_summary: "3年Python后端候选人" },
        });
      }
      if (url.endsWith("/api/latest-running-task")) return response({ ok: true, has_task: false });
      if (url.includes("/api/filter-labels")) return response(oneClickSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      if (url.endsWith("/api/search-scope/preview")) return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:one" }, deduplicated: {} });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("广泛抓取"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="one-click-old-result-hint"]').text()).toContain("将开始新一轮");
    vi.unstubAllGlobals();
  });

  it("B031: running or paused tasks disable the one-click button", async () => {
    const fetchMock = oneClickBase({
      "/api/latest-running-task": () => response({
        ok: true, has_task: true, task_id: "running-scrape", kind: "scrape", status: "running",
        platform: "boss", progress: {}, logs: [], error: "",
      }),
      "/api/task-state/running-scrape": () => response({ status: "running", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("广泛抓取"))!.trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="start-one-click"]').attributes("disabled")).toBeDefined();
    vi.unstubAllGlobals();
  });

  it("B031: refresh restores completed scrape and auto-consumes the marker", async () => {
    const fetchMock = oneClickBase({
      "/api/latest-running-task": () => response({
        ok: true, has_task: true, task_id: "scrape-restored", kind: "scrape", status: "completed",
        platform: "boss", auto_screen: true, scrape_task_id: "scrape-restored", scrape_completed: true,
        auto_screen_fields: { salary: ["406"] }, profile_summary: "3年Python后端候选人",
        progress: {}, logs: [], error: "",
      }),
      "/api/ai-screen": (url, init) => {
        expect(JSON.parse(String(init?.body))).toMatchObject({ consume_auto_screen: true, screening_fields: { salary: ["406"] }, profile_summary: "3年Python后端候选人" });
        return response({ ok: true, task_id: "screen-restored" });
      },
      "/api/task-state/screen-restored": () => response({ status: "completed", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    const aiCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/ai-screen"));
    expect(aiCall).toBeTruthy();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/execute-search")).length).toBe(0);
    vi.unstubAllGlobals();
  });

  it("B031: cancelled or failed scrape does not auto-continue", async () => {
    let mode = "cancelled";
    const fetchMock = oneClickBase({
      "/api/execute-search": () => response({ ok: true, task_id: "one-scrape" }),
      "/api/task-state/one-scrape": () => response({ status: mode, progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(0);

    mode = "failed";
    const wrapper2 = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper2);
    await wrapper2.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper2.get('[data-testid="start-one-click"]').trigger("click");
    await wrapper2.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(0);
    vi.unstubAllGlobals();
  });

  it("B031: AI screen failure consumes frontend intent without retry", async () => {
    let aiCalls = 0;
    const fetchMock = oneClickBase({
      "/api/execute-search": () => response({ ok: true, task_id: "one-scrape" }),
      "/api/task-state/one-scrape": () => response({ status: "completed", progress: {}, logs: [] }),
      "/api/ai-screen": () => { aiCalls += 1; return response({ ok: false, error: "ai_screen_failed" }, 500); },
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    expect(aiCalls).toBe(1);
    await flushPromises();
    expect(aiCalls).toBe(1);
    vi.unstubAllGlobals();
  });

  it("B031: pause keeps the marker and resume continues automatically", async () => {
    const fetchMock = oneClickBase({
      "/api/execute-search": () => response({ ok: true, task_id: "one-scrape" }),
      "/api/task-state/one-scrape": () => response({ status: "paused", progress: {}, logs: [], error: "风控暂停" }),
      "/api/task/continue/one-scrape": () => response({ ok: true, task_id: "one-scrape-2" }),
      "/api/task-state/one-scrape-2": () => response({ status: "completed", progress: {}, logs: [] }),
      "/api/ai-screen": () => response({ ok: true, task_id: "one-screen" }),
      "/api/task-state/one-screen": () => response({ status: "completed", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(0);
    await wrapper.get('[data-testid="continue-scrape"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(1);
    vi.unstubAllGlobals();
  });

  it("B031: an in-session pause disables the one-click button", async () => {
    const fetchMock = oneClickBase({
      "/api/execute-search": () => response({ ok: true, task_id: "one-scrape" }),
      "/api/task-state/one-scrape": () => response({ status: "paused", progress: {}, logs: [], error: "风控暂停" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="start-one-click"]').attributes("disabled")).toBeDefined();
    vi.unstubAllGlobals();
  });

  it("B031: refresh during a running one-click scrape still auto-continues", async () => {
    const fetchMock = oneClickBase({
      "/api/latest-running-task": () => response({
        ok: true, has_task: true, task_id: "running-scrape", kind: "scrape", status: "running",
        platform: "boss", auto_screen: true, auto_screen_fields: { salary: ["406"] },
        profile_summary: "3年Python后端候选人", progress: {}, logs: [], error: "",
      }),
      "/api/task-state/running-scrape": () => response({ status: "completed", progress: {}, logs: [], scraped_count: 1, platform: "boss" }),
      "/api/ai-screen": (url, init) => {
        expect(JSON.parse(String(init?.body))).toMatchObject({
          consume_auto_screen: true,
          screening_fields: { salary: ["406"] },
          profile_summary: "3年Python后端候选人",
        });
        return response({ ok: true, task_id: "one-screen" });
      },
      "/api/task-state/one-screen": () => response({ status: "completed", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(1);
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/execute-search")).length).toBe(0);
    vi.unstubAllGlobals();
  });

  it("B031: refresh restores paused one-click scrape and resume auto-continues", async () => {
    const fetchMock = oneClickBase({
      "/api/latest-running-task": () => response({
        ok: true, has_task: true, task_id: "paused-scrape", kind: "scrape", status: "paused",
        platform: "boss", auto_screen: true, auto_screen_fields: { salary: ["406"] },
        profile_summary: "3年Python后端候选人", progress: {}, logs: [], error: "风控暂停",
        pause_info: { error_code: "captcha_required", error_reason: "风控暂停" },
      }),
      "/api/task-state/paused-scrape": () => response({ status: "paused", progress: {}, logs: [], error: "风控暂停" }),
      "/api/task/continue/paused-scrape": () => response({ ok: true, task_id: "resumed-scrape" }),
      "/api/task-state/resumed-scrape": () => response({ status: "completed", progress: {}, logs: [], scraped_count: 1 }),
      "/api/ai-screen": (url, init) => {
        expect(JSON.parse(String(init?.body))).toMatchObject({
          consume_auto_screen: true,
          screening_fields: { salary: ["406"] },
          profile_summary: "3年Python后端候选人",
        });
        return response({ ok: true, task_id: "one-screen" });
      },
      "/api/task-state/one-screen": () => response({ status: "completed", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    expect(wrapper.get('[data-testid="start-one-click"]').attributes("disabled")).toBeDefined();
    await wrapper.get('[data-testid="continue-scrape"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(1);
    vi.unstubAllGlobals();
  });

  it("B031: completed scrape with zero jobs does not auto-continue", async () => {
    const fetchMock = oneClickBase({
      "/api/execute-search": () => response({ ok: true, task_id: "one-scrape" }),
      "/api/task-state/one-scrape": () => response({ status: "completed", progress: {}, logs: [], scraped_count: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(0);
    vi.unstubAllGlobals();
  });
});
