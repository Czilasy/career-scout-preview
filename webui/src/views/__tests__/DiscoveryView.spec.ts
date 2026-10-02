import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import DiscoveryView from "../DiscoveryView.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";
import { readFileSync } from "node:fs";
import path from "node:path";

// 批四 T078：用例结束统一卸载（此前 117 例仅 21 次显式 unmount，其余挂载的
// 组件、轮询定时器与全局监听会跨用例残留）。
enableAutoUnmount(afterEach);

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// 无进行中任务的统一桩响应（多处用例共用同一载荷，避免逐处字面量漂移）。
const NO_TASK_PAYLOAD = { ok: true, has_task: false };

function singleFlowCurrentResponse(profileId = "profile-1") {
  return response({
    ok: true,
    flow: {
      id: `legacy-test-flow-${profileId}`,
      profile_id: profileId,
      selection: "boss",
      status: "done",
      tracks: [],
    },
  });
}

// 窄屏样式断言用：取出所有同名 @media 规则整块的内容（按花括号配对扫描），
// 依源码顺序拼接。styles.css 里同一个查询在不同章节各有一块（如 max-width: 760px
// 既管筛选栅格、也管命令条与按钮），只取第一块会漏掉后面的真实规则，
// 让「窄屏隐藏了什么」的断言拿到错误的样本。
function mediaBlocks(css: string, query: string): string {
  const marker = `@media (${query})`;
  const blocks: string[] = [];
  let from = 0;
  for (;;) {
    const start = css.indexOf(marker, from);
    if (start < 0) break;
    const open = css.indexOf("{", start);
    if (open < 0) break;
    let depth = 0;
    let close = -1;
    for (let index = open; index < css.length; index += 1) {
      if (css[index] === "{") depth += 1;
      else if (css[index] === "}") {
        depth -= 1;
        if (depth === 0) {
          close = index;
          break;
        }
      }
    }
    if (close < 0) break;
    blocks.push(css.slice(open + 1, close));
    from = close + 1;
  }
  return blocks.join("\n");
}

describe("DiscoveryView", () => {
  beforeEach(() => {
    // 确保当前测试引用的 api 模块实例处于已验证状态（setup.ts 的验证可能落在另一个模块实例上）
    setBuildIdentity(expectedBackendBuildHash);
    sessionStorage.clear();
    // 026 B078：已结束事实持久化在 localStorage，须随测试隔离清空
    localStorage.clear();
  });

  it("B068: refresh keeps the unfinished 02 page state and does not load an old result over a live task", async () => {
    sessionStorage.clear();
    sessionStorage.setItem("career-scout-workflow:profile-b068", JSON.stringify({
      version: 1, unfinished: true, activeStep: "search", analysisReady: true,
      keywords: [{ word: "恢复关键词", recommended: true }], selectedKeywords: ["恢复关键词"], cityText: "恢复城市",
      filterValues: { boss: { salary: ["406"] }, zhilian: {} }, profileSummary: "恢复的求职画像", profileFacts: { experience: "3年" },
      scrapeTaskId: "scrape-b068", screenTaskId: "screen-b068", scrapeCompleted: true,
      scrapeSnapshot: { status: "completed", progress: {}, logs: [] },
      screenSnapshot: { status: "running", progress: { message: "AI 筛选中" }, logs: [] },
      resultLoaded: false, resultsPageSeen: false,
    }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response({
        ok: true, has_task: true, task_id: "screen-b068", kind: "ai_screen", status: "running",
        platform: "boss", scrape_task_id: "scrape-b068", scrape_completed: true,
        frozen_filters: { salary: ["999"] }, profile_summary: "接口返回的画像不应覆盖 02",
        progress: { message: "AI 筛选中" }, logs: [],
      });
      if (url.includes("/api/task-state/screen-b068")) return response({ status: "running", progress: { message: "AI 筛选中" }, logs: [] });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: true, source_run_id: "old-result", result: { jobs: [{ job_id: "old", title: "旧结果" }], dropped: [], total_kept: 1, total_dropped: 0 } });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b068" } });
    await flushPromises();

    expect(wrapper.find(`[data-testid="custom-keyword"]`).exists()).toBe(true);
    expect(wrapper.text()).toContain("恢复关键词");
    expect(wrapper.text()).toContain("恢复城市");
    expect((wrapper.get(".profile-summary-input").element as HTMLTextAreaElement).value).toBe("恢复的求职画像");
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/latest-pipeline-result"))).toBe(false);

    wrapper.unmount();
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("keeps a restored result projection visible while completion flags catch up", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return response({ ok: true, flow: null });
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: {}, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-result-projection" } });
    await flushPromises();
    const setupState = (wrapper.vm as unknown as {
      $: { setupState: Record<string, unknown> };
    }).$.setupState;
    setupState.pipelineResult = {
      platform: "boss",
      jobs: [{ job_id: "restored-job", platform: "boss", title: "恢复岗位", verdict: "match" }],
      dropped: [],
      total_scraped: 1,
      total_kept: 1,
      total_matched: 1,
    };
    setupState.resultLoaded = false;
    setupState.resultsBootstrapPending = true;
    setupState.activeStep = "results";
    await flushPromises();

    expect(wrapper.find(".results-stage").isVisible()).toBe(true);
    expect(wrapper.find('[data-testid="latest-result-empty"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="latest-result-loading"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="job-row"]').text()).toContain("恢复岗位");

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("auto-grows the profile summary and hides the textarea scrollbar", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true,
          selection: "balanced",
          settings: {
            inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
            detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
            screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
          },
          last_custom: null,
          mode_version: null,
          manual_ranges: {},
          config_schema_version: 1,
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-auto-grow" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("跳过简历"))!.trigger("click");
    await flushPromises();

    const input = wrapper.get(".profile-summary-input").element as HTMLTextAreaElement;
    Object.defineProperty(input, "scrollHeight", { configurable: true, value: 180 });
    await wrapper.get(".profile-summary-input").setValue("一段足够长的求职画像\n第二段内容");
    await flushPromises();

    expect(input.style.height).toBe("180px");
    const css = readFileSync(path.join(__dirname, "../../styles.css"), "utf8");
    const profileBlock = css.match(/\.profile-summary-input\s*\{[^}]*\}/s)?.[0] || "";
    expect(profileBlock).toContain("overflow-y: hidden");

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("auto-grows an AI-filled five-section profile after the textarea mounts", async () => {
    const settings = {
      pages: 3, inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const summary = [
      "1. 求职方向：AI 应用开发、后端开发",
      "2. 核心能力：Python、FastAPI、Vue 3、TypeScript",
      "3. 工作与项目经历：负责多个后端与自动化项目",
      "4. 学历与基本条件：本科，计算机相关专业",
      "5. 岗位偏好与排除项：期望双休，排除 996",
    ].join("\n");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/latest-running-task")) return response({ task: null });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/analyze-resume")) {
        return response({
          ok: true,
          fields: {
            keyword: [{ word: "Python 后端", recommended: true }],
            city: [],
            profile_summary: summary,
          },
          labels: {},
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const scrollHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "scrollHeight");
    Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
      configurable: true,
      get: () => 180,
    });

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-ai-grow" } });
    await flushPromises();
    const file = new File(["resume"], "resume.txt", { type: "text/plain" });
    Object.defineProperty(wrapper.get('[data-testid="resume-input"]').element, "files", { value: [file], configurable: true });
    await wrapper.get('[data-testid="resume-input"]').trigger("change");
    await wrapper.get('[data-testid="resume-consent"]').setValue(true);
    await wrapper.get('[data-testid="analyze-resume"]').trigger("click");
    await flushPromises();

    const input = wrapper.get(".profile-summary-input").element as HTMLTextAreaElement;
    expect(input.value).toContain("5. 岗位偏好与排除项：期望双休，排除 996");
    expect(input.style.height).toBe("180px");

    if (scrollHeightDescriptor) {
      Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", scrollHeightDescriptor);
    } else {
      delete (HTMLTextAreaElement.prototype as unknown as { scrollHeight?: number }).scrollHeight;
    }
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("re-measures the profile summary when a hidden search step becomes visible", async () => {
    sessionStorage.setItem("career-scout-workflow:profile-grow-visible", JSON.stringify({
      version: 2,
      unfinished: true,
      activeStep: "upload",
      analysisReady: true,
      keywords: [],
      selectedKeywords: [],
      cityText: "",
      filterValues: { boss: {}, zhilian: {} },
      profileSummary: "一段已经存在且需要撑高展示的求职画像内容",
      profileFacts: {},
      scrapeTaskId: "pending-draft",
      screenTaskId: "",
      scrapeCompleted: false,
      resultLoaded: false,
      resultsPageSeen: false,
    }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: {}, defaults: {} });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const descriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "scrollHeight");
    Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
      configurable: true,
      get() {
        const section = (this as HTMLElement).closest("section");
        return section?.getAttribute("style")?.includes("display: none") ? 0 : 180;
      },
    });

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-grow-visible" } });
    await flushPromises();
    const input = wrapper.get(".profile-summary-input").element as HTMLTextAreaElement;
    expect(input.style.height).toBe("96px");
    const searchStep = wrapper.findAll("button").find((button) => button.text().includes("广泛抓取"));
    await searchStep!.trigger("click");
    await flushPromises();
    expect(input.style.height).toBe("180px");

    if (descriptor) Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", descriptor);
    else delete (HTMLTextAreaElement.prototype as unknown as { scrollHeight?: number }).scrollHeight;
    vi.unstubAllGlobals();
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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

  it("039: 恢复上一轮时面板显示真实完成/跳过计数与失败留痕（不再 0 完成）", async () => {
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
          source_run_id: "screen-restored-039",
          scrape_task_id: "scrape-restored-039",
          status: "partial",
          result: {
            jobs: [{ job_id: "j1", verdict: "match" }, { job_id: "j2", verdict: "not_match" }],
            profile_summary: "历史画像",
            total_kept: 2,
            total_dropped: 0,
          },
          started_at: 1_000,
          finished_at: 2_000,
        });
      }
      if (url.includes("/api/task-state/scrape-restored-039")) {
        return response({
          status: "completed_with_pending", stage: "done", total: 16,
          success_count: 14, fail_count: 2, unstarted_count: 0,
          source_total: 16, scraped_count: 1341, pending_count: 0,
          combo_issues: [{
            combo_key: "A|上海", code: "source_timeout",
            code_text: "抓取超时", reason: "第 9 页无响应", ts: "t1",
          }],
        });
      }
      if (url.includes("/api/task-state/screen-restored-039")) {
        return response({
          status: "completed_with_pending", stage: "done", total: 2,
          success_count: 2, fail_count: 0, unstarted_count: 0, pending_count: 0,
        });
      }
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-039-restore" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("广泛抓取"))!.trigger("click");
    await flushPromises();

    const panel = wrapper.find(".task-progress");
    expect(panel.exists()).toBe(true);
    expect(panel.text()).toContain("已完成 14 / 16");
    expect(panel.text()).toContain("未开始 0");
    expect(panel.get('[data-testid="fail-count"]').text()).toBe("2");
    expect(panel.text()).not.toContain("未知");
    // 悬停失败数字：逐条显示该轮真实失败原因
    await panel.get('[data-testid="fail-count-group"]').trigger("mouseenter");
    expect(panel.get('[data-testid="fail-tooltip"]').text()).toContain("A|上海：抓取超时");

    vi.unstubAllGlobals();
  });

  it("039: 灵动岛跳回上一轮时 02 面板沿用真实计数（不再 0 完成）", async () => {
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "screen-restored-039b",
          scrape_task_id: "scrape-restored-039b",
          status: "partial",
          result: {
            jobs: [{ job_id: "j1", verdict: "match" }, { job_id: "j2", verdict: "not_match" }],
            profile_summary: "历史画像",
            total_kept: 16,
            total_dropped: 0,
            total_scraped: 16,
          },
          started_at: 1_000,
          finished_at: 2_000,
        });
      }
      if (url.includes("/api/task-state/scrape-restored-039b")) {
        return response({
          status: "completed_with_pending", stage: "done", total: 16,
          success_count: 14, fail_count: 2, unstarted_count: 0,
          source_total: 16, scraped_count: 16, pending_count: 0,
          combo_issues: [{
            combo_key: "A|上海", code: "source_timeout",
            code_text: "抓取超时", reason: "第 9 页无响应", ts: "t1",
          }],
        });
      }
      if (url.includes("/api/task-state/screen-restored-039b")) {
        return response({
          status: "completed_with_pending", stage: "done", total: 2,
          success_count: 2, fail_count: 0, unstarted_count: 0, pending_count: 0,
        });
      }
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) {
        return response({
          ok: true,
          items: [{
            run_id: "h1", platform: "boss", status: "succeeded",
            created_at: "2026-09-01 10:00:00", total_scraped: 16, total_kept: 2,
            total_matched: 1, mismatch_count: 1, total_dropped: 0, pending_count: 0,
            keyword_summary: "A / 上海", profile_summary_preview: "历史画像",
            archived_at: null, is_latest: false,
          }],
        });
      }
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1"))) {
        return response({
          ok: true, has_result: true, source_run_id: "h1", platform: "boss", status: "succeeded",
          started_at: 1_000, finished_at: 2_000,
          result: { jobs: [{ job_id: "h1", platform: "boss", verdict: "match", title: "历史岗位" }], total_kept: 1, total_dropped: 0, profile_summary: "历史画像" },
        });
      }
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings, last_custom: null,
          mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-039-island" } });
    await flushPromises();
    // 先进历史轮，再回到最新：点灵动岛跳回上一轮走的是同一个快照合成入口
    //（returnToLatest → applyFetchedLatestResult），该入口必须与启动加载同口径。
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="back-to-latest"]').trigger("click");
    await flushPromises();
    const searchStep = wrapper.findAll("button").find((b) => b.text().includes("广泛抓取"));
    await searchStep!.trigger("click");
    await flushPromises();

    const panel = wrapper.findAll(".task-progress").find((item) => item.isVisible())!;
    expect(panel.exists()).toBe(true);
    expect(panel.text()).toContain("已完成 14 / 16");
    expect(panel.text()).toContain("未开始 0");
    expect(panel.get('[data-testid="fail-count"]').text()).toBe("2");

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // 历史列表行属于「一眼扫过」的密度：冻结条件的原始 JSON、内部映射版本号和
  // 英文字段码都不得吐给用户；整坨 JSON 还会成为该轮按钮的可访问名。
  // 用户能感知的只有一件事：这一轮的条件已经冻结、按当时条件跑。
  it("summarizes a Flow history track's frozen conditions in plain Chinese without JSON or version codes", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/result-history/flows")) {
        return response({
          ok: true,
          items: [{
            flow_id: "flow-history-frozen",
            profile_id: "profile-history-frozen",
            selection: "all",
            status: "done",
            tracks: [{
              platform: "boss",
              status: "done",
              result_run_id: "boss-history-result",
              confirmed_filters_snapshot: {
                snapshotVersion: 2,
                mappingVersion: "b096-v2-history",
                unifiedValues: { salary: ["10k-20k"] },
                platformValues: { boss: { salary: ["10-20k"], stage: ["804"] } },
              },
              jobs: [],
            }],
          }],
        });
      }
      if (url.includes("/api/result-history?") || url.endsWith("/api/result-history")) return response({ ok: true, items: [] });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: {}, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-history-frozen" } });
    await flushPromises();
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();

    const track = wrapper.get('[data-testid="history-flow-track"]');
    expect(track.text()).toContain("筛选条件已按当时冻结");
    expect(track.text()).not.toContain("b096-v2-history");
    expect(track.text()).not.toContain("10k-20k");
    expect(track.text()).not.toContain("10-20k");
    expect(track.text()).not.toContain("804");
    expect(track.text()).not.toContain("{");
    expect(track.text()).not.toContain("salary");
    expect(track.text()).not.toContain("mappingVersion");
    // 整坨 JSON 之前还成了这一轮按钮的可访问名
    const openButton = track.find("button.history-flow-track-button");
    expect(openButton.text()).not.toContain("\"");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // V1 老快照只有 {"salary":["406"]} 这种裸字段码，既没有 platformValues 也没有
  // unifiedValues：同样不得整坨回显，也不得因为「读不懂」就什么都不说。
  it("summarizes a legacy V1 frozen snapshot without echoing its raw field codes", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/result-history/flows")) {
        return response({
          ok: true,
          items: [{
            flow_id: "flow-history-v1",
            profile_id: "profile-history-v1",
            selection: "boss",
            status: "done",
            tracks: [{
              platform: "boss",
              status: "done",
              confirmed_filters_snapshot: { salary: ["406"] },
              jobs: [],
            }],
          }],
        });
      }
      if (url.includes("/api/result-history?") || url.endsWith("/api/result-history")) return response({ ok: true, items: [] });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: {}, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-history-v1" } });
    await flushPromises();
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();

    const track = wrapper.get('[data-testid="history-flow-track"]');
    expect(track.text()).toContain("筛选条件已按当时冻结");
    expect(track.text()).not.toContain("406");
    expect(track.text()).not.toContain("salary");
    expect(track.text()).not.toContain("{");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("039: 纯抓取轮从历史返回后 03 面板仍显示真实 0（已完成 0 / N、未开始 N）", async () => {
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "run-039c",
          scrape_task_id: "scrape-039c",
          status: "scraped_only",
          result: {
            jobs: [{ job_id: "j1" }, { job_id: "j2" }, { job_id: "j3" }],
            profile_summary: "画像",
            total_scraped: 3, total_kept: 0, total_dropped: 0,
          },
          started_at: 1_000,
          finished_at: 2_000,
        });
      }
      if (url.includes("/api/task-state/scrape-039c")) {
        // 抓取任务：组合口径（2/2），03 面板不得混用该口径
        return response({
          status: "done", stage: "done", total: 2,
          success_count: 2, fail_count: 0, unstarted_count: 0,
          source_total: 3, scraped_count: 3,
        });
      }
      if (url.includes("/api/task-state/run-039c")) {
        // 未筛选轮真实快照：尚未筛选 → 0 完成、3 未开始（N = 本轮已抓岗位）
        return response({
          status: "scraped_only", stage: "scrape", total: 3,
          success_count: 0, fail_count: 0, unstarted_count: 3,
          source_total: 3, scraped_count: 3,
        });
      }
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) {
        return response({
          ok: true,
          items: [{
            run_id: "h1", platform: "boss", status: "succeeded",
            created_at: "2026-09-01 10:00:00", total_scraped: 3, total_kept: 3,
            total_matched: 3, mismatch_count: 0, total_dropped: 0, pending_count: 0,
            keyword_summary: "A / 上海", profile_summary_preview: "画像",
            archived_at: null, is_latest: false,
          }],
        });
      }
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1"))) {
        return response({
          ok: true, has_result: true, source_run_id: "h1", platform: "boss", status: "succeeded",
          started_at: 1_000, finished_at: 2_000,
          result: { jobs: [{ job_id: "h1", platform: "boss", verdict: "match", title: "历史岗位" }], total_kept: 1, total_dropped: 0, profile_summary: "画像" },
        });
      }
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings, last_custom: null,
          mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-039-scraped-only" } });
    await flushPromises();
    // 历史返回（点灵动岛跳回上一轮走同一入口）后，03 面板不得以“没有筛选单元”为由隐藏
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="back-to-latest"]').trigger("click");
    await flushPromises();
    const screenStep = wrapper.findAll("button").find((b) => b.text().includes("AI 筛选"));
    await screenStep!.trigger("click");
    await flushPromises();

    const panel = wrapper.findAll(".task-progress").find((item) => item.isVisible())!;
    expect(panel.exists()).toBe(true);
    expect(panel.find('[data-testid="task-counts"]').exists()).toBe(true);
    expect(panel.text()).toContain("已完成 0 / 3");
    expect(panel.text()).toContain("未开始 3");

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("uses canonical scope, applies mode default pages and locks a started task", async () => {
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
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/latest-running-task")) return response({ task: null });
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
          ok: true, selection: "stable", settings: { ...settings, detail_batch_size: 6, pages: 2 },
          task_size: "small", mode_version_id: "mode-v1", config_digest: "sha256:stable",
        });
      }
      if (url.endsWith("/api/execute-search")) {
        expect(JSON.parse(String(init?.body)).scope_digest).toBe("sha256:scope");
        return response({ ok: true, task_id: "scrape-locked" });
      }
      if (url.includes("/api/task-state/scrape-locked")) return response({ status: "running", progress: {}, logs: [] });
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
    // 稳定档默认翻页数为 2（跟随档位），其他速度字段由档位配置提供。
    expect((pages.element as HTMLInputElement).value).toBe("2");
    expect((wrapper.get('[data-testid="detail-batch-size"]').element as HTMLInputElement).value).toBe("6");

    await confirmProfile(wrapper, "3年Python后端候选人");
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="custom-keyword"]').attributes()).toHaveProperty("disabled");
    expect(wrapper.get('[data-testid="custom-city"]').attributes()).toHaveProperty("disabled");
    expect(wrapper.get('[data-testid="pages-per-combination"]').attributes()).toHaveProperty("disabled");

    vi.unstubAllGlobals();
  });

  it("keeps the selected mode while editing pages (preset fields are read-only)", async () => {
    const settings = {
      pages: 3,
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: {
            keywords: ["AI应用开发"], scope_kind: "cities", cities: ["东莞市"],
            pages_per_combination: 3, combination_count: 1, planned_pages: 3,
            task_size: "small", scope_digest: "sha256:scope",
          },
          deduplicated: { keywords: ["ai应用开发"], cities: ["东莞市"] },
        });
      }
      if (url.endsWith("/api/advanced-settings/select-mode")) {
        return response({ ok: true, selection: "stable", settings: { ...settings, detail_batch_size: 6 }, task_size: "small", mode_version_id: "mode-v1", config_digest: "sha256:stable" });
      }
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

    await wrapper.get('[data-mode="stable"]').trigger("click");
    await flushPromises();

    // 预设档下：速度字段只读（不可聚焦），仅每组翻页数可编辑；编辑翻页数不切换档位。
    expect((wrapper.get('[data-testid="detail-batch-size"]').element as HTMLInputElement).disabled).toBe(true);
    const pagesInput = wrapper.get('[data-testid="pages-per-combination"]');
    await pagesInput.setValue(7);
    await pagesInput.trigger("change");
    await flushPromises();

    expect(wrapper.get('[data-mode="stable"]').attributes("aria-checked")).toBe("true");
    expect(wrapper.get('[data-mode="custom"]').attributes("aria-checked")).toBe("false");
    expect(wrapper.get('[data-testid="adv-mode-summary"]').text()).toContain("稳定");
    expect(wrapper.get('[data-testid="adv-mode-summary"]').text()).toContain("详情每批 6 个");

    vi.unstubAllGlobals();
  });

  it("024 警示区：大任务任何档位显示，极限档追加极限警告（可同时）", async () => {
    const settings = {
      pages: 36,
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "custom", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: {
            keywords: ["AI应用开发"], scope_kind: "cities", cities: ["东莞市"],
            pages_per_combination: 36, combination_count: 1, planned_pages: 36,
            task_size: "large", scope_digest: "sha256:scope-large",
          },
          deduplicated: { keywords: [], cities: [] },
        });
      }
      if (url.endsWith("/api/advanced-settings/select-mode")) {
        return response({ ok: true, selection: "extreme", settings, task_size: "large", mode_version_id: null, config_digest: "sha256:extreme" });
      }
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

    // custom 档 + 大任务（>30 页）：仅大任务警告（已并入模式说明行内）
    const summaryText = () => wrapper.get('[data-testid="adv-mode-summary"]').text();
    expect(wrapper.find('[data-testid="mode-warning-inline"]').exists()).toBe(true);
    expect(summaryText()).toContain("任务规模过大可能封号");
    expect(summaryText()).not.toContain("有概率限流");

    // 切极限档：两条警告同时显示
    await wrapper.get('[data-mode="extreme"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-mode="extreme"]').attributes("aria-checked")).toBe("true");
    expect(summaryText()).toContain("有概率限流");
    expect(summaryText()).toContain("任务规模过大可能封号");

    vi.unstubAllGlobals();
  });

  it("restores interrupted AI screen source ids without occupying the new-task slot", async () => {
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
      if (url.includes("/api/latest-running-task")) {
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
      if (url.endsWith("/api/task/finish/interrupted-run")) {
        return response({
          ok: true, run_id: "interrupted-run", snapshot_run_id: "snapshot-interrupted", platform: "boss",
          status: "completed_with_pending", scrape_task_id: "scrape-1",
          result: { jobs: [], total_scraped: 0, total_kept: 0, total_dropped: 0 },
        });
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
    // 失败/中断只保留错误事实并回到 02，不能把页面锁在旧的 AI 任务上。
    expect(wrapper.find('[data-testid="profile-confirm"]').exists()).toBe(true);
    // 状态词表：中断没有活体 worker，后端续跑也只收 paused/可续 failed，
    // 因此这一轮不再给出一个注定失败的「继续 AI 筛选」。
    expect(wrapper.find('[data-testid="continue-ai-screen"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("disabled")).toBeUndefined();
    await confirmProfile(wrapper, "3年Python后端工程师候选人");
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(".task-progress").exists()).toBe(true);
    // 摘掉继续不许顺手砍掉原有出口：这一轮仍按它自己恢复回来的 run 走既有 run 级收尾。
    expect(wrapper.find('[data-testid="continue-ai-screen"]').exists()).toBe(false);
    await wrapper.get('[data-testid="finish-save-results"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.some(
      ([url]) => String(url).endsWith("/api/task/finish/interrupted-run"),
    )).toBe(true);

    vi.unstubAllGlobals();
  });

  it("T509: restores zhilian interrupted AI screen task by loading zhilian schema/city but keeps draft boss", async () => {
    // platform-schema.md L157：恢复任务时先设置任务自身平台，再加载对应 schema/城市/筛选快照；
    // 不变式 2：setTaskPlatform 不改 draft/result。所以草稿仍是 BOSS，但已加载 schema/city 是 zhilian。
    // 不 click start-ai-screen：draft≠task 时会触发已知 UX 问题（zhilian 任务恢复后表单读 boss 草稿），
    // 由 T515 真实联调阶段修复；本会话只验证 schema/city/draft 三身份独立。
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
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

    // 中断任务初始回到 02，旧任务不占用新任务入口；进入 03 才显示其 AI 动作。
    expect(wrapper.find('[data-testid="finish-save-results"]').isVisible()).toBe(false);
    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("disabled")).toBeUndefined();

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
          profile_id: "profile-1",
          script_params: { keyword: "Python 后端", city: ["上海"], filters: {} },
          scope_digest: "sha256:scope-existing",
          profile_summary: "Python 后端候选人",
          profile_facts: {},
        });
        return response({ ok: true, task_id: "scrape-1" });
      }
      if (url.includes("/api/task-state/scrape-1")) {
        return response({ status: "completed", progress: {}, logs: [], result: { jobs: [] } });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
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
    // Legacy result projections do not have an owned Flow.  The archive API
    // requires both flow_id and profile_id, so starting analysis must not send
    // an unscoped archive request in this case.
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/result-history/archive-latest")))
      .toBe(false);
    expect(wrapper.find('[data-testid="start-ai-screen"]').isVisible()).toBe(false);

    await confirmProfile(wrapper);
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
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

  it("allows legacy analyze-resume without an unscoped archive fallback", async () => {
    const analyzeCalls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
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
      if (url.endsWith("/api/analyze-resume")) {
        analyzeCalls.push(url);
        return response({ ok: true, fields: { keyword: [], city: [], profile_summary: "" }, labels: {} });
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

    expect(analyzeCalls).toHaveLength(1);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/result-history/archive-latest")))
      .toBe(false);
    const notices = wrapper.emitted("notify")?.flat() as Array<{ message: string }>;
    expect(notices.some((n) => n.message.includes("归档旧结果失败"))).toBe(false);
    vi.unstubAllGlobals();
  });

  it("keeps analyze-resume failure visible on the button until retried", async () => {
    let analyzeAttempts = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
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
      if (url.endsWith("/api/result-history/archive-latest")) {
        return response({ ok: true, cleared: true });
      }
      if (url.endsWith("/api/analyze-resume")) {
        analyzeAttempts += 1;
        if (analyzeAttempts === 1) {
          return response({ ok: false, error: "AI 响应超时" }, 502);
        }
        return response({
          ok: true,
          fields: {
            keyword: [{ word: "Python 后端", recommended: true }],
            city: [],
            salary: [],
            experience: [],
            degree: [],
            industry: [],
            scale: [],
            stage: [],
            profile_summary: "Python 后端候选人",
          },
          labels: {},
        });
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
    const button = () => wrapper.get('[data-testid="analyze-resume"]');
    await button().trigger("click");
    await flushPromises();
    expect(button().text()).toContain("失败，点击重试");
    expect(button().classes()).toContain("danger");
    await button().trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/api/analyze-resume"))).toHaveLength(2);
    expect(wrapper.find('[data-testid="custom-keyword"]').exists()).toBe(true);
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
      if (url.includes("/api/latest-running-task")) return response({ task: null });
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

  it("resume analysis never prefills a city for the user", async () => {
    const settings = {
      pages: 3, inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/latest-running-task")) return response({ task: null });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/analyze-resume")) {
        return response({
          ok: true,
          fields: {
            keyword: [{ word: "Python 后端", recommended: true }],
            city: ["上海"],
            profile_summary: "3年Python后端",
          },
          labels: { city: ["城市", ["上海"], "city"] },
        });
      }
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

    expect(wrapper.find(".city-chip").exists()).toBe(false);
    expect((wrapper.get('[data-testid="custom-city"]').element as HTMLInputElement).value).toBe("");
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
      if (url.includes("/api/latest-running-task")) return response({ task: null });
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
      if (url.includes("/api/latest-running-task")) return response({ task: null });
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
    const tip = pagesLabel.querySelector("i.tip")?.getAttribute("data-tip") || "";
    expect(tip).toContain("范围 1~10");
    expect(tip).toContain("BOSS 最多返回 10 页");
    expect(tip).toContain("超出可能无新数据");

    await pages.setValue(3);
    await pages.trigger("change");
    await flushPromises();
    expect(pagesLabel.querySelector("i.tip")?.getAttribute("data-tip")).not.toContain("BOSS 最多返回 10 页");
    expect(pagesLabel.querySelector("i.tip")?.getAttribute("data-tip")).toContain("范围 1~10");

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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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

    // 无当前 Flow 时默认为“全部”；显式切到 BOSS 后继续验证单平台草稿切换。
    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");

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
      if (url.includes("/api/latest-running-task")) {
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

    // 任务被接回后直接进入搜索步，进度播报区随搜索步挂载；草稿平台分段控件在所有步骤都常驻顶部。
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="custom-keyword"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="task-progress-announcement"]').exists()).toBe(true);

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

  it("B096 selects 全部 by default when the Flow capability endpoint is available", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: null }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b096-all" } });
    await flushPromises();

    expect(wrapper.get('[data-testid="platform-segment-all"]').attributes("aria-selected")).toBe("true");
    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
    expect(wrapper.get('[data-testid="platform-segment-all"]').attributes("aria-selected")).toBe("false");
    expect(wrapper.get('[data-testid="platform-segment-boss"]').attributes("aria-selected")).toBe("true");

    vi.unstubAllGlobals();
  });

  it("B096 treats a queued Flow envelope with no Tracks as busy", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "queued-envelope-flow", profile_id: "profile-queued-envelope", selection: "all", status: "queued", tracks: [],
      } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-queued-envelope" } });
    await flushPromises();

    const exposedFlow = (wrapper.vm as unknown as {
      parallelFlow?: { hasUnfinishedRound?: { value?: boolean }; canStartNewRound?: { value?: boolean } };
    }).parallelFlow;
    expect(exposedFlow?.hasUnfinishedRound?.value).toBe(true);
    expect(exposedFlow?.canStartNewRound?.value).toBe(false);
    expect(wrapper.get('[data-testid="start-one-click"]').attributes("disabled")).toBeDefined();
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // 一条轨道已出结果、另一条被重启打断：外壳如实说「已中断」。
  // 本轮范围仍然锁死是对的，但并行模式下「开始新一轮」是唯一出口，必须解锁。
  it("B096 keeps the round locked yet unlocks a new round for an interrupted envelope", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "dead-ended-flow", profile_id: "profile-dead-ended-envelope", selection: "all", status: "interrupted",
        tracks: [
          { id: "b", flow_id: "dead-ended-flow", platform: "boss", status: "done", stage: "complete", scrape_run_id: "scrape-boss", screen_run_id: "screen-boss", result_run_id: "result-boss" },
          { id: "z", flow_id: "dead-ended-flow", platform: "zhilian", status: "interrupted", stage: "ai", scrape_run_id: "scrape-zhilian", screen_run_id: "screen-zhilian", result_run_id: null },
        ],
      } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-dead-ended-envelope" } });
    await flushPromises();

    const exposed = (wrapper.vm as unknown as {
      parallelFlow?: { hasUnfinishedRound?: { value?: boolean }; canResetNewRound?: { value?: boolean } };
    });
    expect(exposed.parallelFlow?.hasUnfinishedRound?.value).toBe(true);
    expect(exposed.parallelFlow?.canResetNewRound?.value).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // SPEC 046 收口第一单：「能不能开新一轮」整棵树只允许一份清单（排队中/运行中/已暂停锁，
  // 已中断放行），02 页主启动按钮读它的投影，不再被「本轮未结束」顺带锁死；锁住时原因必须
  // 落在既有的锁定提示位（本轮范围卡片的锁芯片），不许只藏在按钮 title 里等用户去悬停。
  it.each([
    { name: "interrupted", status: "interrupted", locked: false, reason: "" },
    { name: "paused", status: "paused", locked: true, reason: "任务已暂停" },
    { name: "running", status: "running", locked: true, reason: "任务进行中" },
  ])("02 主启动按钮按那一份清单分派：$status", async (scene) => {
    const profileId = `profile-newround-${scene.name}`;
    const flowId = `newround-${scene.name}-flow`;
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: flowId,
        profile_id: profileId,
        selection: "all",
        status: scene.status,
        tracks: [
          { id: "b", flow_id: flowId, platform: "boss", status: scene.status, stage: "ai", scrape_run_id: "scrape-b", screen_run_id: "screen-b", result_run_id: null },
          { id: "z", flow_id: flowId, platform: "zhilian", status: "done", stage: "complete", scrape_run_id: "scrape-z", screen_run_id: "screen-z", result_run_id: "result-z" },
        ],
      } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId } });
    await flushPromises();

    const startButton = wrapper.get('[data-testid="start-one-click"]');
    if (scene.locked) expect(startButton.attributes("disabled")).toBeDefined();
    else expect(startButton.attributes("disabled")).toBeUndefined();
    const chip = wrapper.find(".lock-chip");
    expect(chip.exists()).toBe(true);
    if (scene.reason) expect(chip.text()).toContain(scene.reason);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // 刚提交「全部」流程：两条轨道都还是 queued、run 身份尚未产生，
  // 02/03 进度面板不得整块空白——用户必须看到排队中的运行线。
  it("shows queued all-platform tracks as waiting progress instead of a blank progress area", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "queued-tracks-flow",
        profile_id: "profile-queued-tracks",
        selection: "all",
        status: "queued",
        tracks: [
          { id: "qt-boss", platform: "boss", status: "queued", stage: "pending", scrape_run_id: null, screen_run_id: null },
          { id: "qt-zhilian", platform: "zhilian", status: "queued", stage: "pending", scrape_run_id: null, screen_run_id: null },
        ],
      } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-queued-tracks" } });
    await flushPromises();

    const panels = wrapper.findAll('[data-testid="parallel-platform-progress"]');
    expect(panels.length).toBeGreaterThan(0);
    const scrapePanel = panels[0];
    expect(scrapePanel.findAll('[data-testid="parallel-track-boss"]')).toHaveLength(1);
    expect(scrapePanel.findAll('[data-testid="parallel-track-zhilian"]')).toHaveLength(1);
    expect(scrapePanel.text()).toContain("等待开始");
    expect(scrapePanel.text()).not.toContain("queued");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // D-03 结构收敛：并行轨道行的动作就是单平台那套动作条，出口也走同两条既有路径——
  // 轨道级暂停/终止走 Flow 轨道端点，轨道级「结束并保存」走单平台同一条 run 级收尾端点。
  it("sends each Track row's actions to that Track's own Flow and run endpoints", async () => {
    const trackFlow = {
      id: "track-actions-flow", profile_id: "profile-track-actions", selection: "all", status: "running",
      tracks: [
        { id: "tb", platform: "boss", scrape_run_id: "scrape-b", status: "running", stage: "scrape" },
        { id: "tz", platform: "zhilian", scrape_run_id: "scrape-z", status: "paused", stage: "scrape" },
      ],
    };
    const posts: string[] = [];
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: trackFlow }),
      "/api/flows/track-actions-flow/results": () => response({ ok: true, results: { tracks: [] } }),
      "/api/task-state/scrape-b": () => response({ status: "running", progress: {}, logs: [] }),
      "/api/task-state/scrape-z": () => response({ status: "paused", progress: {}, logs: [] }),
      "/api/flows/track-actions-flow/tracks/boss/pause": () => {
        posts.push("tracks/boss/pause");
        return response({ ok: true, flow: trackFlow });
      },
      "/api/flows/track-actions-flow/tracks/zhilian/stop": () => {
        posts.push("tracks/zhilian/stop");
        return response({ ok: true, flow: trackFlow });
      },
      "/api/task/finish/scrape-z": () => {
        posts.push("task/finish/scrape-z");
        return response({
          ok: true, run_id: "scrape-z", snapshot_run_id: "snapshot-z", platform: "zhilian",
          status: "completed_with_pending", scrape_task_id: "scrape-z",
          result: { jobs: [], total_scraped: 0, total_kept: 0, total_dropped: 0 },
        });
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-track-actions" } });
    await flushPromises();

    const bossRow = wrapper.get('[data-testid="parallel-track-boss"]');
    const zhilianRow = wrapper.get('[data-testid="parallel-track-zhilian"]');
    expect(bossRow.findAllComponents({ name: "ScreenRoundActions" })).toHaveLength(1);
    expect(zhilianRow.findAllComponents({ name: "ScreenRoundActions" })).toHaveLength(1);

    await bossRow.get('[data-testid="pause-scrape"]').trigger("click");
    await flushPromises();
    expect(posts).toContain("tracks/boss/pause");

    const resultEpochBeforeSave = (wrapper.vm as any).$.setupState.resultEpoch;
    const categoryBeforeSave = (wrapper.vm as any).$.setupState.activeCategory;
    await zhilianRow.get('[data-testid="parallel-track-zhilian-finish-save"]').trigger("click");
    await flushPromises();
    expect(posts).toContain("task/finish/scrape-z");
    expect((wrapper.vm as any).$.setupState.resultEpoch).toBe(resultEpochBeforeSave);
    expect((wrapper.vm as any).$.setupState.activeCategory).toBe(categoryBeforeSave);
    expect(bossRow.find('[data-testid="pause-scrape"]').exists()).toBe(true);

    await zhilianRow.get('[data-testid="parallel-track-zhilian-cancel"]').trigger("click");
    await flushPromises();
    expect(posts).toContain("tracks/zhilian/stop");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it.each(["pause", "finish"] as const)("046 A04: %s uses the clicked instance's shared batch dialog", async (action) => {
    const trackFlow = { id: "instance-batch-flow", profile_id: "instance-batch-profile", selection: "all", status: "running", tracks: [
      { id: "ib", platform: "boss", scrape_run_id: "ib-scrape", screen_run_id: "ib-screen", status: "running", stage: "ai" },
      { id: "iz", platform: "zhilian", scrape_run_id: "iz-scrape", status: "running", stage: "scrape" },
    ] };
    const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: trackFlow }),
      "/api/flows/instance-batch-flow/results": () => response({ ok: true, results: { flow_id: trackFlow.id, tracks: [] } }),
      "/api/task-state/ib-scrape": () => response({ status: "completed", progress: {}, logs: [] }),
      "/api/task-state/ib-screen": () => response({ status: "running", progress: { stage: "fetch_jd", jd_batch: { current: 2, total: 4 } }, logs: [] }),
      "/api/task-state/iz-scrape": () => response({ status: "running", progress: {}, logs: [] }),
      "/api/flows/instance-batch-flow/tracks/boss/pause": (url, init) => { requests.push({ url, body: JSON.parse(String(init?.body)) }); return response({ ok: true, flow: trackFlow }); },
      "/api/task/finish/ib-screen": (url, init) => { requests.push({ url, body: JSON.parse(String(init?.body)) }); return response({ ok: true, platform: "boss", result: { jobs: [] } }); },
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "instance-batch-profile" }, attachTo: document.body });
    await flushPromises();
    const screenRows = wrapper.findAll('[data-testid="parallel-track-boss"]');
    const screenRow = screenRows.find((entry) => entry.find('[data-testid="pause-ai-screen"]').exists())!;
    await screenRow.get(action === "pause" ? '[data-testid="pause-ai-screen"]' : '[data-testid="parallel-track-boss-finish-save"]').trigger("click");
    await flushPromises();
    expect(requests).toHaveLength(0);
    const dialog = document.querySelector('[data-testid="pause-batch-dialog"]')!;
    expect(dialog.textContent).toContain("第 2 批 / 共 4 批");
    (dialog.querySelector('[data-testid="pause-graceful"]') as HTMLButtonElement).click();
    await flushPromises();
    expect(requests).toHaveLength(1);
    expect(requests[0]?.body).toEqual(action === "pause"
      ? { profile_id: "instance-batch-profile", expected_run_id: "ib-screen", mode: "graceful" }
      : { wait_for_batch: true });
    expect(wrapper.findAll('[data-testid="parallel-track-zhilian"]').some((entry) => entry.find('[data-testid="pause-scrape"]').exists())).toBe(true);
    wrapper.unmount(); vi.unstubAllGlobals();
  });

  // D-04/D-05 排版：并行轨道的动作行收在该轨道卡边框内部，轨道模块横跨整行、
  // 与底部动作行同宽；同一条用例点名 02/03 页既有现场，防止用删功能的方式让检查变绿。
  it("keeps each Track's actions inside its card and the module across the full row", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "track-layout-flow", profile_id: "profile-track-layout", selection: "all", status: "running",
        tracks: [
          { id: "tl-b", platform: "boss", scrape_run_id: "scrape-tl-b", status: "running", stage: "scrape" },
          { id: "tl-z", platform: "zhilian", scrape_run_id: "scrape-tl-z", status: "paused", stage: "scrape" },
        ],
      } }),
      "/api/flows/track-layout-flow/results": () => response({ ok: true, results: { tracks: [] } }),
      "/api/task-state/scrape-tl-b": () => response({ status: "running", progress: {}, logs: [] }),
      "/api/task-state/scrape-tl-z": () => response({ status: "paused", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-track-layout" } });
    await flushPromises();

    const panel = wrapper.get('[data-testid="parallel-platform-progress"]');
    for (const platform of ["boss", "zhilian"]) {
      const trackRow = panel.get(`[data-testid="parallel-track-${platform}"]`);
      const card = trackRow.get("section.task-progress");
      const bars = card.findAllComponents({ name: "ScreenRoundActions" });
      expect(bars).toHaveLength(1);
      // 包含关系：动作行在卡边框内，不再掉在卡外、贴页面左缘。
      expect(card.element.contains(bars[0]!.element)).toBe(true);
      // 平台标识与状态口径都仍由卡自己说，且各只有一份。
      expect(trackRow.find('[data-testid="task-platform-badge"]').exists()).toBe(true);
      expect(trackRow.findAll(".task-status")).toHaveLength(1);
    }

    // 02 页底部动作行仍在原位（整行）；轨道模块与它同为这一片两列网格的直接子项，
    // 共用同一条横跨整行的网格规则，因此两者同宽。
    expect(wrapper.find('[data-testid="start-one-click"]').exists()).toBe(true);
    const layoutSection = panel.element.closest(".search-layout");
    const rowSiblings = Array.from(layoutSection?.children ?? []);
    const actionRow = wrapper.get(".workflow-actions");
    expect(rowSiblings).toContain(panel.element);
    expect(rowSiblings).toContain(actionRow.element);
    const css = readFileSync(path.join(__dirname, "../../styles.css"), "utf8");
    const fullRowRule = css.match(/\.search-layout > \.task-progress,[\s\S]{0,220}?\}/)?.[0] || "";
    expect(fullRowRule).toContain(".search-layout > .parallel-platform-progress");
    expect(fullRowRule).toContain(".search-layout > .workflow-actions");
    expect(fullRowRule).toContain("grid-column: 1 / -1");
    wrapper.unmount();
    vi.unstubAllGlobals();

    // 点名断言：并行分支没有吃掉这些既有现场——
    // 02 页「进行确认AI筛选条件」出口、03 页 ContinuePlatformGuide、
    // 单平台路径的 TaskProgress、03 页重抓进度 ScreenRecrawlProgress。
    const view = readFileSync(path.join(__dirname, "../DiscoveryView.vue"), "utf8");
    expect(view).toContain('data-testid="continue-to-screen"');
    expect(view).toContain('<TaskProgress v-if="!parallelMode" :snapshot="scrapeSnapshot"');
    expect(view).toContain('<TaskProgress v-if="!parallelMode" :snapshot="screenSnapshot"');
    expect(view).toContain('<ContinuePlatformGuide v-if="!historyMode && roundFlow.continueGuide"');
    expect(view).toContain('<ScreenRecrawlProgress v-if="recrawlSnapshot || recrawlBusy"');
  });

  // 状态词表：中断没有活体 worker，轨道行不给「继续」（服务重启后轨道级继续必然失败），
  // 只留「结束并保存」这条收口出口；开新一轮的出口在页面级，由既有的 canResetNewRound 负责。
  it("keeps an interrupted Track row free of any continuation", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "interrupted-track-flow", profile_id: "profile-interrupted-track", selection: "all", status: "interrupted",
        tracks: [
          { id: "tb", platform: "boss", scrape_run_id: "scrape-b", status: "interrupted", stage: "scrape" },
        ],
      } }),
      "/api/flows/interrupted-track-flow/results": () => response({ ok: true, results: { tracks: [] } }),
      "/api/task-state/scrape-b": () => response({ status: "interrupted", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-interrupted-track" } });
    await flushPromises();

    const row = wrapper.get('[data-testid="parallel-track-boss"]');
    expect(row.text()).toContain("已中断");
    expect(row.find('[data-testid="continue-scrape"]').exists()).toBe(false);
    expect(row.text()).not.toContain("继续");
    expect(row.find('[data-testid="parallel-track-boss-finish-save"]').exists()).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // 全部平台某条线 AI 失败时，结果页顶部有失败通知。它此前复用了装饰性提示的  // .command-note 类，而该类在窄屏（≤760px）被整条 display:none：窗宽一小，
  // 用户面对结果列表以为全都成功。错误/失败类通知任何宽度都必须可见。
  it("keeps the flow failure notice readable at every viewport width", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "flow-fail-notice",
        profile_id: "profile-fail-notice",
        selection: "all",
        status: "done",
        tracks: [
          { id: "fn-boss", platform: "boss", status: "done", stage: "complete", result_run_id: "fn-boss-result" },
          { id: "fn-z", platform: "zhilian", status: "failed", stage: "screen", result_run_id: "fn-z-result" },
        ],
      } }),
      "/api/flows/flow-fail-notice/results": () => response({ ok: true, results: {
        flow_id: "flow-fail-notice",
        selection: "all",
        status: "done",
        tracks: [
          { platform: "boss", status: "done", stage: "complete", result_run_id: "fn-boss-result", jobs: [{ job_id: "b1", platform: "boss", title: "老板岗位", verdict: "match" }], dropped: [] },
          { platform: "zhilian", status: "failed", stage: "screen", result_run_id: "fn-z-result", unfinished_ai_screening: true, message: "AI 服务额度用尽", jobs: [], dropped: [] },
        ],
      } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-fail-notice" } });
    await flushPromises();
    const resultsStep = wrapper.findAll(".step-nav button").find((button) => button.text().includes("查看结果"));
    await resultsStep!.trigger("click");
    await flushPromises();

    const notice = wrapper.get('[data-testid="flow-failure-notice"]');
    expect(notice.text()).toContain("AI 服务额度用尽");
    expect(notice.classes()).not.toContain("command-note");

    const css = readFileSync(path.join(__dirname, "../../styles.css"), "utf8");
    const narrow = mediaBlocks(css, "max-width: 760px");
    for (const className of notice.classes()) {
      expect(narrow).not.toMatch(new RegExp(`\\.${className}\\s*\\{[^}]*display:\\s*none`));
    }
    const noticeBlock = css.match(/\.flow-failure-notice\s*\{[^}]*\}/s)?.[0] || "";
    expect(noticeBlock).not.toBe("");
    expect(noticeBlock).not.toContain("display: none");

    // 装饰性提示仍可窄屏让位（它就是和通知共类才被误伤的那一个）。
    expect(narrow).toMatch(/\.command-note\s*\{[^}]*display:\s*none/);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // 窄屏把 stage-header 按钮压成 44px 纯图标（font-size:0）：「回到最新」和
  // 「开始新一轮」当时用同一个 RotateCcw、也没有可访问名，四个图标无法区分，
  // 用户看不出哪个只是回退、哪个会重开一轮。
  it("distinguishes 回到最新 from 开始新一轮 and states the real platform lock reason", async () => {
    const historyItem = {
      run_id: "h1",
      platform: "boss",
      status: "done",
      created_at: "2026-08-11 10:00:00",
      total_scraped: 10,
      total_kept: 1,
      total_matched: 1,
      mismatch_count: 0,
      total_dropped: 9,
      pending_count: 0,
      keyword_summary: "Python 后端",
      profile_summary_preview: "3年Python后端",
      archived_at: null,
      is_latest: true,
    };
    const fetchMock = oneClickBase({
      "/api/result-history": () => response({ ok: true, items: [historyItem] }),
      "/api/result-history/h1": () => response({
        ok: true,
        has_result: true,
        source_run_id: "h1",
        platform: "boss",
        status: "done",
        result: {
          jobs: [{ job_id: "j1", platform: "boss", verdict: "match", title: "历史岗位" }],
          total_kept: 1,
          total_dropped: 9,
          profile_summary: "完整画像文本",
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(true);

    const back = wrapper.get('[data-testid="back-to-latest"]');
    const newRound = wrapper.get('[data-testid="start-new-round"]');
    expect(back.attributes("aria-label")).toBe("回到最新");
    expect(back.attributes("title")).toBe("回到最新");
    expect(newRound.attributes("aria-label")).toContain("开始新一轮");
    expect(newRound.attributes("title")).toContain("开始新一轮");
    // 只剩图标的宽度下，形状必须能分辨两个动作
    expect(back.find("svg").attributes("class")).not.toBe(newRound.find("svg").attributes("class"));
    expect(wrapper.get('[data-testid="export-result-csv"]').attributes("aria-label")).toBe("导出 CSV");

    // 流程早已终态、没有任何任务在跑：禁用提示不得再说「任务进行中」。
    const platformButton = wrapper.get('[data-testid="platform-segment-boss"]');
    expect(platformButton.attributes("disabled")).toBeDefined();
    expect(platformButton.attributes("title")).toContain("历史轮次");
    expect(platformButton.attributes("title")).not.toContain("任务进行中");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // 结果页 grid 行数必须覆盖所有可能同时出现的子项：引导条与待确认胶囊
  // 各自只声明 3 行，两者同时为真时第 4 个子节点落到隐式 auto 行，
  // height:100% 失效，与 view-shell 的纵向滚动叠成双滚动条。
  it("declares four grid rows when the recrawl guide and the pending capsule are both on", () => {
    const css = readFileSync(path.join(__dirname, "../../styles.css"), "utf8");
    const combined = css.match(
      /\.results-view \.results-stage\.has-recrawl-guide\.has-pending-capsule\s*\{[^}]*\}/s,
    )?.[0] || "";
    expect(combined).toContain("grid-template-rows: auto auto auto minmax(0, 1fr)");
    // 单一 class 的既有口径不得被顺手改坏
    const guide = css.match(/\.results-view \.results-stage\.has-recrawl-guide\s*\{[^}]*\}/s)?.[0] || "";
    expect(guide).toContain("grid-template-rows: auto auto minmax(0, 1fr)");
    const capsule = css.match(/\.results-view \.results-stage\.has-pending-capsule\s*\{[^}]*\}/s)?.[0] || "";
    expect(capsule).toContain("grid-template-rows: auto auto minmax(0, 1fr)");
  });

  // 中等宽度（实测 817px）下命令条不换行，「判定依据」提示压在分类标签上，
  // 待确认数量被遮住。标签所在的那一行必须允许让位，而不是彼此叠印。
  it("lets the command band wrap so the result tabs are never overlapped", () => {
    const css = readFileSync(path.join(__dirname, "../../styles.css"), "utf8");
    const band = css.match(/\.command-band\s*\{[^}]*\}/s)?.[0] || "";
    expect(band).toContain("flex-wrap: wrap");
    const tabs = css.match(/\.result-tabs\s*\{[^}]*\}/s)?.[0] || "";
    expect(tabs).toContain("min-width: 0");
  });

  it("does not legacy-auto-start AI when the restored completed scrape belongs to the current all-platform Flow", async () => {
    const aiScreenCalls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({
        ok: true,
        flow: {
          id: "flow-owned-scrape",
          profile_id: "profile-flow-owned",
          selection: "all",
          status: "running",
          tracks: [
            { id: "boss-track", platform: "boss", status: "done", stage: "scrape", scrape_run_id: "scrape-flow-owned" },
            { id: "zhilian-track", platform: "zhilian", status: "queued", stage: "scrape", scrape_run_id: null },
          ],
        },
      }),
      "/api/flows/flow-owned-scrape/results": () => response({
        ok: true,
        results: { flow_id: "flow-owned-scrape", selection: "all", status: "running", tracks: [], jobs: [] },
      }),
      "/api/task-state/scrape-flow-owned": () => response({ status: "completed", progress: {}, logs: [], scraped_count: 3 }),
      "/api/latest-running-task": async () => {
        // Let current-Flow hydration publish the owning track before legacy restore reads it.
        await new Promise((resolve) => setTimeout(resolve, 0));
        return response({
          ok: true,
          has_task: true,
          task_id: "scrape-flow-owned",
          kind: "scrape",
          status: "completed",
          platform: "boss",
          scrape_task_id: "scrape-flow-owned",
          auto_screen: true,
          auto_screen_fields: { salary: ["406"] },
          profile_summary: "三年后端工程师开发经验",
        });
      },
      "/api/ai-screen": (url, init) => {
        aiScreenCalls.push([url, init]);
        return response({ ok: true, task_id: "legacy-screen-must-not-start" });
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-flow-owned" } });
    await flushPromises();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await flushPromises();
    await new Promise((resolve) => setTimeout(resolve, 20));
    await flushPromises();

    expect(aiScreenCalls).toHaveLength(0);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("legacy completed scrape recovery still starts AI when no Flow owns the task", async () => {
    const aiScreenCalls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: null }),
      "/api/task-state/scrape-legacy": () => response({ status: "completed", progress: {}, logs: [], scraped_count: 3 }),
      "/api/latest-running-task": () => response({
        ok: true,
        has_task: true,
        task_id: "scrape-legacy",
        kind: "scrape",
        status: "completed",
        platform: "boss",
        scrape_task_id: "scrape-legacy",
        auto_screen: true,
        auto_screen_fields: { salary: ["406"] },
        profile_summary: "三年后端工程师开发经验",
      }),
      "/api/ai-screen": (url, init) => {
        aiScreenCalls.push([url, init]);
        return response({ ok: true, task_id: "legacy-screen-started" });
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-legacy-recovery" } });
    await flushPromises();
    await flushPromises();

    expect(aiScreenCalls).toHaveLength(1);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("reads a current single-platform Flow instead of falling back to another result source", async () => {
    const flowResultCalls: string[] = [];
    const latestResultCalls: string[] = [];
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({
        ok: true,
        flow: {
          id: "legacy-single-flow",
          profile_id: "profile-legacy-single",
          selection: "boss",
          status: "done",
          tracks: [],
        },
      }),
      "/api/flows/legacy-single-flow/results": (url) => {
        flowResultCalls.push(url);
        return response({ ok: true, results: {
          flow_id: "legacy-single-flow", status: "done", tracks: [
            { platform: "boss", status: "done", stage: "complete", jobs: [], dropped: [] },
          ], jobs: [],
        } });
      },
      "/api/latest-pipeline-result": (url) => {
        latestResultCalls.push(url);
        return response({
          ok: true, has_result: true, source_run_id: "legacy-latest", platform: "boss", status: "done",
          result: { jobs: [{ job_id: "legacy-job", platform: "boss", title: "旧平台结果", verdict: "match" }], dropped: [], total_kept: 1 },
        });
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-legacy-single" } });
    await flushPromises();
    await flushPromises();

    expect(flowResultCalls.length).toBeGreaterThan(0);
    expect(latestResultCalls).toHaveLength(0);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("does not render a stale global result when a current single-platform Flow owns the round", async () => {
    sessionStorage.setItem("career-scout-workflow:profile-single-current-result", JSON.stringify({
      version: 2, unfinished: true, activeStep: "screen", analysisReady: true,
      scrapeTaskId: "single-scrape", screenTaskId: "single-screen", scrapeCompleted: true,
      scrapeSnapshot: { status: "completed", progress: {}, logs: [] },
      screenSnapshot: { status: "completed", progress: {}, logs: [] },
      resultLoaded: false, resultsPageSeen: false, pipelineResult: null,
    }));
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({
        ok: true,
        flow: {
          id: "flow-single-current-result",
          profile_id: "profile-single-current-result",
          selection: "boss",
          status: "done",
          tracks: [{ id: "boss-track", platform: "boss", status: "done", stage: "complete", result_run_id: "single-result" }],
        },
      }),
      "/api/flows/flow-single-current-result/results": () => response({ ok: true, results: {
        flow_id: "flow-single-current-result", selection: "boss", status: "done", tracks: [{
          id: "boss-track", platform: "boss", status: "done", stage: "complete", result_run_id: "single-result",
          jobs: [{ job_id: "current-single-job", platform: "boss", title: "当前单平台结果", verdict: "match" }], dropped: [],
        }], jobs: [],
      } }),
      "/api/latest-pipeline-result": () => response({
        ok: true, has_result: true, source_run_id: "stale-global-result", platform: "zhilian", status: "done",
        result: { jobs: [{ job_id: "stale-global-job", platform: "zhilian", title: "另一模式旧结果", verdict: "match" }], dropped: [], total_kept: 1 },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-single-current-result" } });
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).not.toContain("另一模式旧结果");
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/flows/flow-single-current-result/results"))).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/latest-pipeline-result"))).toHaveLength(0);
    const resultsStep = wrapper.findAll(".step-nav button").find((button) => button.text().includes("查看结果"))!;
    expect(resultsStep.attributes("disabled")).toBeUndefined();
    await resultsStep.trigger("click");
    await flushPromises();
    expect(wrapper.find(".results-stage").isVisible()).toBe(true);
    expect(wrapper.text()).toContain("当前单平台结果");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("stops using an all-platform Flow after switching to a legacy platform", async () => {
    const flowResultCalls: string[] = [];
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "active-all-flow", profile_id: "profile-switch-flow", selection: "all", status: "running",
        tracks: [{ id: "boss-track", platform: "boss", status: "running", stage: "scrape" }, { id: "zhilian-track", platform: "zhilian", status: "running", stage: "scrape" }],
      } }),
      "/api/flows/active-all-flow/results": (url) => {
        flowResultCalls.push(url);
        return response({ ok: true, results: { flow_id: "active-all-flow", selection: "all", status: "running", tracks: [], jobs: [] } });
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-switch-flow" } });
    await flushPromises();
    const beforeSwitch = flowResultCalls.length;
    expect(beforeSwitch).toBeGreaterThan(0);
    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
    await flushPromises();
    expect(flowResultCalls).toHaveLength(beforeSwitch);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("reloads the legacy single-platform result after leaving a terminal all-platform Flow", async () => {
    sessionStorage.setItem("career-scout-workflow:profile-terminal-all-switch", JSON.stringify({
      version: 2, unfinished: true, activeStep: "search", analysisReady: true,
      scrapeCompleted: true, resultLoaded: true, resultsPageSeen: true,
    }));
    const flowResultCalls: string[] = [];
    const latestResultCalls: string[] = [];
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "terminal-all-flow", profile_id: "profile-terminal-all-switch", selection: "all", status: "done",
        tracks: [{ id: "boss-track", platform: "boss", status: "done", stage: "complete", result_run_id: "all-result" }],
      } }),
      "/api/flows/terminal-all-flow/results": (url) => {
        flowResultCalls.push(url);
        return response({ ok: true, results: { flow_id: "terminal-all-flow", selection: "all", status: "done", tracks: [{
          id: "boss-track", platform: "boss", status: "done", stage: "complete", result_run_id: "all-result",
          jobs: [{ job_id: "all-job", platform: "boss", title: "全部平台终态结果", verdict: "match" }], dropped: [],
        }], jobs: [] } });
      },
      "/api/latest-pipeline-result": (url) => {
        latestResultCalls.push(url);
        return response({ ok: true, has_result: true, source_run_id: "legacy-boss-result", platform: "boss", status: "done",
          result: { jobs: [{ job_id: "legacy-boss-job", platform: "boss", title: "BOSS 单平台结果", verdict: "match" }], dropped: [], total_kept: 1 } });
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-terminal-all-switch" } });
    await flushPromises();
    await flushPromises();

    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
    await wrapper.findAll(".step-nav button")[3]!.trigger("click");
    await flushPromises();
    await flushPromises();

    expect(latestResultCalls.length).toBeGreaterThan(0);
    expect(wrapper.text()).toContain("BOSS 单平台结果");
    expect(wrapper.text()).not.toContain("全部平台终态结果");
    expect(flowResultCalls.length).toBeGreaterThan(0);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("isolates Flow navigation in a single-platform view and rehydrates it when All returns", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "mode-switch-flow", profile_id: "profile-mode-switch", selection: "all", status: "running",
        tracks: [{
          id: "boss-track", platform: "boss", scrape_run_id: "scrape-mode-switch",
          screen_run_id: "screen-mode-switch", status: "done", stage: "screen",
        }],
      } }),
      "/api/task-state/scrape-mode-switch": () => {
        return response({ status: "running", progress: {}, logs: [] });
      },
      "/api/task-state/screen-mode-switch": () => {
        return response({ status: "running", progress: {}, logs: [] });
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-mode-switch" } });
    await flushPromises();
    await flushPromises();
    const screenButton = () => wrapper.findAll(".step-nav button")[2]!;
    const taskStateCallCount = () => fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/task-state/")).length;
    expect(screenButton().attributes("disabled")).toBeUndefined();
    const beforeSinglePlatform = taskStateCallCount();

    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
    await flushPromises();
    expect(screenButton().attributes("disabled")).toBeDefined();
    expect(taskStateCallCount()).toBe(beforeSinglePlatform);

    await wrapper.get('[data-testid="platform-segment-all"]').trigger("click");
    await flushPromises();
    await flushPromises();
    expect(taskStateCallCount()).toBeGreaterThan(beforeSinglePlatform);
    expect(screenButton().attributes("disabled")).toBeUndefined();

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("refreshes the mounted parallel Flow for the current profile before creating a new round", async () => {
    const currentCalls: string[] = [];
    const createdFlows: Array<Record<string, unknown>> = [];
    const executedSearches: Array<Record<string, unknown>> = [];
    const fetchMock = oneClickBase({
      "/api/flows/current": (url) => {
        currentCalls.push(url);
        if (url.includes("profile_id=profile-old")) {
          return response({ ok: true, flow: {
            id: "old-profile-flow", profile_id: "profile-old", selection: "all", status: "running",
            tracks: [{ id: "old-track", platform: "boss", scrape_run_id: "old-scrape", status: "running", stage: "scrape" }],
          } });
        }
        return response({ ok: true, flow: {
          id: "new-profile-flow", profile_id: "profile-new", selection: "all", status: "done", tracks: [],
        } });
      },
      "/api/flows/old-profile-flow/results": () => response({ ok: true, results: { tracks: [] } }),
      "/api/flows/new-profile-flow/results": () => response({ ok: true, results: { tracks: [] } }),
      "/api/task-state/old-scrape": () => response({ status: "running", progress: { message: "旧画像任务" }, logs: [] }),
      "/api/flows": (_url, init) => {
        const body = JSON.parse(String(init?.body || "{}")) as Record<string, unknown>;
        createdFlows.push(body);
        return response({ ok: true, flow: {
          id: "created-profile-flow", profile_id: body.profile_id, selection: body.selection, status: "queued", tracks: [],
        } });
      },
      "/api/execute-search": (_url, init) => {
        executedSearches.push(JSON.parse(String(init?.body || "{}")) as Record<string, unknown>);
        return response({ ok: true, task_id: `new-scrape-${executedSearches.length}` });
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-old" } });
    await flushPromises();
    await flushPromises();
    expect(wrapper.find('[data-testid="parallel-track-boss"]').exists()).toBe(true);

    await wrapper.setProps({ profileId: "profile-new" });
    await flushPromises();
    await flushPromises();
    expect(currentCalls.some((url) => url.includes("profile_id=profile-new"))).toBe(true);
    expect(wrapper.find('[data-testid="parallel-track-boss"]').exists()).toBe(false);

    await oneClickSearch(wrapper);
    await confirmProfile(wrapper, "新的求职画像用于AI筛选");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();

    expect(createdFlows[0]?.profile_id).toBe("profile-new");
    expect(executedSearches).toHaveLength(2);
    expect(executedSearches.every((payload) => payload.profile_id === "profile-new")).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("clears the previous parallel presentation when a replacement profile has no Flow", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": (url) => {
        if (url.includes("profile_id=profile-new-empty")) return response({ ok: true, flow: null });
        return response({ ok: true, flow: {
          id: "old-profile-flow", profile_id: "profile-old-empty", selection: "all", status: "running",
          tracks: [{ id: "old-track", platform: "boss", scrape_run_id: "old-scrape", status: "running", stage: "scrape" }],
        } });
      },
      "/api/flows/old-profile-flow/results": () => response({ ok: true, results: { tracks: [] } }),
      "/api/task-state/old-scrape": () => response({ status: "running", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-old-empty" } });
    await flushPromises();
    await flushPromises();
    expect(wrapper.find('[data-testid="parallel-track-boss"]').exists()).toBe(true);

    await wrapper.setProps({ profileId: "profile-new-empty" });
    await flushPromises();
    await flushPromises();

    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/flows/current?profile_id=profile-new-empty"))).toBe(true);
    expect(wrapper.find('[data-testid="parallel-track-boss"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="parallel-screen-boss"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("clears the mounted Flow when the replacement profile cannot be read", async () => {
    let profile = "profile-old";
    const fetchMock = oneClickBase({
      "/api/flows/current": (url) => {
        if (url.includes("profile_id=profile-new")) return Promise.reject(new Error("画像不可用"));
        return response({ ok: true, flow: {
          id: "old-profile-flow", profile_id: "profile-old", selection: "all", status: "running",
          tracks: [{ id: "old-track", platform: "boss", scrape_run_id: "old-scrape", status: "running", stage: "scrape" }],
        } });
      },
      "/api/flows/old-profile-flow/results": () => response({ ok: true, results: { tracks: [] } }),
      "/api/task-state/old-scrape": () => response({ status: "running", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: profile } });
    await flushPromises();
    await flushPromises();
    expect(wrapper.find('[data-testid="parallel-track-boss"]').exists()).toBe(true);

    profile = "profile-new";
    await wrapper.setProps({ profileId: profile });
    await flushPromises();
    await flushPromises();

    // 画像切换后不能把旧画像 Flow 继续作为新画像现场展示或交互，避免跨画像串线。
    expect(wrapper.find('[data-testid="parallel-track-boss"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/flows/current?profile_id=profile-new"))).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("starts a profile replacement in 全部 mode and does not leak the old single-platform mode on read failure", async () => {
    let profile = "profile-old-single-mode";
    const fetchMock = oneClickBase({
      "/api/flows/current": (url) => {
        if (url.includes("profile_id=profile-new-single-mode")) return Promise.reject(new Error("画像不可用"));
        return response({ ok: true, flow: {
          id: "old-single-mode-flow", profile_id: "profile-old-single-mode", selection: "boss", status: "done", tracks: [],
        } });
      },
      "/api/latest-running-task": (url) => url.includes("profile_id=profile-new-single-mode")
        ? response({ ok: true, has_task: true, task_id: "new-screen", kind: "ai_screen", status: "running", platform: "boss", progress: {}, logs: [] })
        : response(NO_TASK_PAYLOAD),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: profile } });
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    profile = "profile-new-single-mode";
    await wrapper.setProps({ profileId: profile });
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("switches to the legacy platform view after profile recovery finds only a live legacy task", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": (url) => url.includes("profile_id=profile-live-legacy")
        ? response({ ok: true, flow: null })
        : response({ ok: true, flow: {
          id: "old-all-flow-live-legacy", profile_id: "profile-old-live-legacy", selection: "all", status: "done", tracks: [],
        } }),
      "/api/flows/old-all-flow-live-legacy/results": () => response({ ok: true, results: { tracks: [] } }),
      "/api/latest-running-task": (url) => url.includes("profile_id=profile-live-legacy")
        ? response({
          ok: true, has_task: true, task_id: "legacy-screen-live", kind: "ai_screen", status: "running",
          platform: "boss", scrape_task_id: "legacy-scrape-live", progress: { message: "AI 筛选中" }, logs: [],
        })
        : response(NO_TASK_PAYLOAD),
      "/api/task-state/legacy-screen-live": () => response({ status: "running", progress: { message: "AI 筛选中" }, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-old-live-legacy" } });
    await flushPromises();
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);

    await wrapper.setProps({ profileId: "profile-live-legacy" });
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(false);
    expect(wrapper.find('.task-progress').exists()).toBe(true);
    expect(wrapper.text()).toContain("AI 筛选中");
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/latest-running-task?profile_id=profile-live-legacy"))).not.toHaveLength(0);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("closes the one-click dialog and invalidates delayed preparation on profile switch", async () => {
    let oldCurrentCalls = 0;
    let releaseOldPrepare!: (value: Response) => void;
    const pendingOldPrepare = new Promise<Response>((resolve) => { releaseOldPrepare = resolve; });
    const fetchMock = oneClickBase({
      "/api/flows/current": (url) => {
        if (url.includes("profile_id=profile-dialog-new")) return response({ ok: true, flow: null });
        oldCurrentCalls += 1;
        if (oldCurrentCalls === 1) return response({ ok: true, flow: {
          id: "old-dialog-flow", profile_id: "profile-dialog-old", selection: "all", status: "done", tracks: [],
        } });
        return pendingOldPrepare;
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-dialog-old" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await confirmProfile(wrapper, "旧画像的一键筛选画像");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="one-click-platform-tabs"]').exists()).toBe(true);

    await wrapper.setProps({ profileId: "profile-dialog-new" });
    await flushPromises();
    expect(wrapper.find('[data-testid="one-click-platform-tabs"]').exists()).toBe(false);

    releaseOldPrepare(response({ ok: true, flow: null }));
    await flushPromises();
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("invalidates delayed dialog preparation after close, draft edits, and reopen", async () => {
    let currentCalls = 0;
    let releaseStale!: (value: Response) => void;
    const pendingStale = new Promise<Response>((resolve) => { releaseStale = resolve; });
    const executeBodies: Array<Record<string, unknown>> = [];
    const fetchMock = oneClickBase({
      "/api/flows/current": () => {
        currentCalls += 1;
        if (currentCalls === 1) return response({ ok: true, flow: {
          id: "initial-dialog-flow", profile_id: "profile-dialog-race", selection: "all", status: "done", tracks: [],
        } });
        if (currentCalls === 2) return pendingStale;
        return response({ ok: true, flow: {
          id: "fresh-dialog-flow", profile_id: "profile-dialog-race", selection: "all", status: "done", tracks: [],
        } });
      },
      "/api/flows": () => response({ ok: true, flow: {
        id: "started-dialog-flow", profile_id: "profile-dialog-race", selection: "all", status: "queued", tracks: [],
      } }),
      "/api/execute-search": (_url, init) => {
        executeBodies.push(JSON.parse(String(init?.body || "{}")) as Record<string, unknown>);
        return response({ ok: true, task_id: `dialog-task-${executeBodies.length}` });
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-dialog-race" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await confirmProfile(wrapper, "旧关键词画像用于筛选流程");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="one-click-cancel"]').exists()).toBe(true);

    await wrapper.get('[data-testid="one-click-cancel"]').trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("新关键词");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("新城市");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    await flushPromises();

    releaseStale(response({ ok: true, flow: {
      id: "stale-dialog-flow", profile_id: "profile-dialog-race", selection: "all", status: "done", tracks: [],
    } }));
    await flushPromises();
    await flushPromises();

    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(executeBodies.length).toBe(2);
    expect(executeBodies.every((body) => String((body.script_params as Record<string, unknown>).keyword).includes("新关键词"))).toBe(true);
    expect(executeBodies.every((body) => String((body.script_params as Record<string, unknown>).keyword).includes("旧关键词") === false)).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("surfaces a stale Flow when confirmation returns after switching profiles without applying success side effects", async () => {
    let releaseCreate!: (value: Response) => void;
    const pendingCreate = new Promise<Response>((resolve) => { releaseCreate = resolve; });
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: null }),
      "/api/flows": () => pendingCreate,
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-start-old" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await confirmProfile(wrapper, "旧画像确认启动并用于本轮筛选");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    const confirmation = wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();

    await wrapper.setProps({ profileId: "profile-start-new" });
    await flushPromises();
    releaseCreate(response({ ok: true, flow: {
      id: "orphan-old-profile-flow", profile_id: "profile-start-old", selection: "all", status: "queued", tracks: [],
    } }));
    await confirmation;
    await flushPromises();

    const notices = (wrapper.emitted("notify") || []).flat().map((notice) => String((notice as { message?: unknown }).message || ""));
    expect(notices.some((message) => message.includes("旧画像流程 orphan-old-profile-flow 已创建"))).toBe(true);
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === "/api/execute-search")).toHaveLength(0);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("does not let a late completed single-platform refresh replace a fresh 全部 selection", async () => {
    let releaseNewProfile!: (value: Response) => void;
    const pendingNewProfile = new Promise<Response>((resolve) => { releaseNewProfile = resolve; });
    const fetchMock = oneClickBase({
      "/api/flows/current": (url) => {
        if (url.includes("profile_id=profile-new-single-race")) return pendingNewProfile;
        return response({ ok: true, flow: {
          id: "old-single-race-flow", profile_id: "profile-old-single-race", selection: "boss", status: "done", tracks: [],
        } });
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-old-single-race" } });
    await flushPromises();
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    await wrapper.setProps({ profileId: "profile-new-single-race" });
    await wrapper.get('[data-testid="platform-segment-all"]').trigger("click");
    releaseNewProfile(response({ ok: true, flow: {
      id: "late-completed-single", profile_id: "profile-new-single-race", selection: "boss", status: "done", tracks: [],
    } }));
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    const exposedParallelFlow = (wrapper.vm as unknown as {
      parallelFlow?: { flow?: { value?: unknown } };
    }).parallelFlow;
    expect(exposedParallelFlow?.flow?.value).toBeNull();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/latest-running-task?profile_id=profile-new-single-race"))).toHaveLength(0);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("blocks all-platform start with a visible mapping error instead of sending an empty snapshot", async () => {
    const schema = (platform: string) => ({
      ok: true, platform, schema_version: 1, enabled_for_new_tasks: true,
      fields: [{ key: "salary", label: "薪资范围", multiple: true, options: [{ value: "406", label: "20-50K" }] }],
    });
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: null }),
      "/api/filter-labels": (url) => response(schema(url.includes("platform=zhilian") ? "zhilian" : "boss")),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-mapping-error" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await confirmProfile(wrapper, "3年Python后端候选人");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();

    const salary = wrapper.get('[data-testid="one-click-unified-fields"]')
      .findAll("button").find((button) => button.text() === "20K-50K");
    await salary!.trigger("click");

    expect(wrapper.get('[data-testid="one-click-mapping-error"]').text()).toContain("筛选条件已变化");
    expect(wrapper.get('[data-testid="one-click-confirm"]').attributes("disabled")).toBeDefined();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/flows/flow-"))).toBe(false);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("resets parallel condition source when switching profiles before the next resume", async () => {
    let analysisCount = 0;
    const schema = {
      ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true,
      fields: [{ key: "experience", label: "经验要求", multiple: true, options: [
        { value: "106", label: "3-5年" }, { value: "107", label: "5-10年" },
      ] }],
    };
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: null }),
      "/api/filter-labels": (url) => response({ ...schema, platform: url.includes("platform=zhilian") ? "zhilian" : "boss" }),
      "/api/analyze-resume": () => {
        const semantic = analysisCount++ === 0 ? { experience: ["3-5年"] } : { experience: ["5-10年"] };
        return response({ ok: true, fields: { keyword: [{ word: "Python", recommended: true }], city: ["上海"], profile_summary: "画像" }, semantic, labels: {} });
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-switch-conditions-1" } });
    await flushPromises();

    const firstFile = new File(["first"], "first.txt", { type: "text/plain" });
    Object.defineProperty(wrapper.get('[data-testid="resume-input"]').element, "files", { value: [firstFile], configurable: true });
    await wrapper.get('[data-testid="resume-input"]').trigger("change");
    await wrapper.get('[data-testid="resume-consent"]').setValue(true);
    await wrapper.get('[data-testid="analyze-resume"]').trigger("click");
    await flushPromises();
    await confirmProfile(wrapper, "第一份求职画像以及更多信息");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    await flushPromises();
    if (wrapper.find('[data-testid="confirm-national-scope"]').exists()) {
      await wrapper.get('[data-testid="confirm-national-scope"]').trigger("click");
      await flushPromises();
    }
    expect(wrapper.findAll("button").find((button) => button.text() === "3-5年")?.classes()).toContain("selected");
    const exposedParallelFlow = (wrapper.vm as unknown as { parallelFlow?: { unifiedValues?: { experience?: string[] } } }).parallelFlow;
    expect(exposedParallelFlow?.unifiedValues?.experience).toEqual(["3-5年"]);
    await wrapper.get('[data-testid="one-click-cancel"]').trigger("click");

    await wrapper.setProps({ profileId: "profile-switch-conditions-2" });
    await flushPromises();
    expect(exposedParallelFlow?.unifiedValues?.experience).toEqual([]);
    if (wrapper.find('[data-testid="platform-segment-all"]').exists()) {
      await wrapper.get('[data-testid="platform-segment-all"]').trigger("click");
    }
    await wrapper.findAll("button").find((button) => button.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await wrapper.get('.profile-summary-input').setValue("切换画像后用于条件复核的求职画像");
    await wrapper.get('[data-testid="profile-confirm"]').trigger("click");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    if (wrapper.find('[data-testid="confirm-national-scope"]').exists()) {
      await wrapper.get('[data-testid="confirm-national-scope"]').trigger("click");
      await flushPromises();
    }
    expect(wrapper.findAll("button").find((button) => button.text() === "3-5年")?.classes()).not.toContain("selected");
    await wrapper.get('[data-testid="one-click-cancel"]').trigger("click");

    const secondFile = new File(["second"], "second.txt", { type: "text/plain" });
    Object.defineProperty(wrapper.get('[data-testid="resume-input"]').element, "files", { value: [secondFile], configurable: true });
    await wrapper.get('[data-testid="resume-input"]').trigger("change");
    await wrapper.get('[data-testid="resume-consent"]').setValue(true);
    await wrapper.get('[data-testid="analyze-resume"]').trigger("click");
    await flushPromises();
    await confirmProfile(wrapper, "第二份求职画像以及更多信息");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    if (wrapper.find('[data-testid="confirm-national-scope"]').exists()) {
      await wrapper.get('[data-testid="confirm-national-scope"]').trigger("click");
      await flushPromises();
    }

    expect(wrapper.findAll("button").find((button) => button.text() === "5-10年")?.classes()).toContain("selected");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("B096 keeps 全部 on current-Flow failure or empty response, while restoring a declared single selection", async () => {
    const failedCurrent = oneClickBase({
      "/api/flows/current": () => Promise.reject(new Error("current unavailable")),
    });
    vi.stubGlobal("fetch", failedCurrent);
    const failedWrapper = mount(DiscoveryView, { props: { profileId: "profile-b096-failure" } });
    await flushPromises();
    expect(failedWrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    failedWrapper.unmount();
    vi.unstubAllGlobals();

    const emptyCurrent = oneClickBase({
      "/api/flows/current": () => response({}),
    });
    vi.stubGlobal("fetch", emptyCurrent);
    const emptyWrapper = mount(DiscoveryView, { props: { profileId: "profile-b096-empty" } });
    await flushPromises();
    expect(emptyWrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    emptyWrapper.unmount();
    vi.unstubAllGlobals();

    const restoredSingle = oneClickBase({
      "/api/flows/current": () => response({
        ok: true,
        flow: {
          id: "flow-b096-zhilian",
          profile_id: "profile-b096-single",
          selection: "zhilian",
          status: "done",
          tracks: [],
        },
      }),
    });
    vi.stubGlobal("fetch", restoredSingle);
    const singleWrapper = mount(DiscoveryView, { props: { profileId: "profile-b096-single" } });
    await flushPromises();
    expect(singleWrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    expect(singleWrapper.find('[data-testid="platform-current-all"]').exists()).toBe(false);
    singleWrapper.unmount();
    vi.unstubAllGlobals();
  });

  it.each([
    ["reject", () => Promise.reject(new Error("current unavailable"))],
    ["empty response", () => response({})],
  ])("B096 keeps 全部 through one-click preparation when current Flow has %s", async (_label, currentResponse) => {
    const fetchMock = oneClickBase({ "/api/flows/current": currentResponse });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b096-prepare" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await confirmProfile(wrapper, "3年Python后端候选人");

    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="one-click-platform-tabs"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="one-click-platform-tab-boss"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="one-click-platform-tab-zhilian"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="one-click-confirm"]').exists()).toBe(true);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("does not let a completed single-platform Flow from an async preparation replace a fresh 全部 selection", async () => {
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "completed-single-before-all", profile_id: "profile-prepare-race", selection: "boss", status: "done", tracks: [],
      } }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-prepare-race" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await confirmProfile(wrapper, "3年Python后端候选人");

    await wrapper.get('[data-testid="platform-segment-all"]').trigger("click");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="one-click-platform-tabs"]').exists()).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("keeps the all-platform confirmation disabled until Flow preparation resolves", async () => {
    let currentCalls = 0;
    let releasePreparation!: (value: Response) => void;
    const pendingPreparation = new Promise<Response>((resolve) => { releasePreparation = resolve; });
    const fetchMock = oneClickBase({
      "/api/flows/current": () => {
        currentCalls += 1;
        if (currentCalls === 1) return response({ ok: true, flow: null });
        return pendingPreparation;
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-prepare-delay" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await confirmProfile(wrapper, "3年Python后端候选人");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="one-click-confirm"]').attributes("disabled")).toBeDefined();
    releasePreparation(response({ ok: true, flow: null }));
    await flushPromises();
    await flushPromises();
    expect(wrapper.get('[data-testid="one-click-confirm"]').attributes("disabled")).toBeUndefined();

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("B096 V2 T032: second platform Flow results load in place without resetting scene", async () => {
    const flowResponses = 0;
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({
        ok: true,
        flow: {
          id: "flow-v2-merge", profile_id: "profile-v2-merge", selection: "all", status: "running",
          tracks: [
            { id: "boss-track", platform: "boss", scrape_run_id: "scrape-b", status: "done", stage: "scrape" },
          ],
        },
      }),
      "/api/flows/flow-v2-merge/results": () => response({
        ok: true,
        results: {
          flow_id: "flow-v2-merge", profile_id: "profile-v2-merge", selection: "all", status: "running",
          tracks: [
            {
              platform: "boss", status: "done", stage: "complete", result_run_id: "result-b",
              ai_screened: true, screened_count: 1,
              jobs: [{ job_id: "second", platform: "boss", title: "新岗位", verdict: "uncertain" }], dropped: [],
            },
          ],
          jobs: [], screened_count: 1,
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-v2-merge" } });
    await flushPromises();
    await flushPromises();

    const flowResultCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/flows/flow-v2-merge/results"));
    expect(flowResultCalls).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/latest-pipeline-result"))).toHaveLength(0);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/latest-pipeline-result"))).toHaveLength(0);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("drains a retried terminal Flow result into exactly one island notice", async () => {
    vi.useFakeTimers();
    try {
      let resultCalls = 0;
      const resultPayload = {
        ok: true,
        results: {
          flow_id: "flow-retry-notice", profile_id: "profile-retry-notice", selection: "all", status: "done",
          tracks: [{
            id: "boss-track", platform: "boss", status: "done", stage: "complete", result_run_id: "result-a",
            jobs: [{ job_id: "retry-job", platform: "boss", title: "重试岗位", verdict: "match" }], dropped: [],
          }],
          jobs: [],
        },
      };
      const fetchMock = oneClickBase({
        "/api/flows/current": () => response({
          ok: true,
          flow: {
            id: "flow-retry-notice", profile_id: "profile-retry-notice", selection: "all", status: "done",
            tracks: [{ id: "boss-track", platform: "boss", status: "done", stage: "complete", result_run_id: null }],
          },
        }),
        "/api/flows/flow-retry-notice/results": () => {
          resultCalls += 1;
          return resultCalls < 3
            ? Promise.reject(new Error("temporary results failure"))
            : response(resultPayload);
        },
      });
      vi.stubGlobal("fetch", fetchMock);

      const wrapper = mount(DiscoveryView, { props: { profileId: "profile-retry-notice" } });
      await flushPromises();
      await flushPromises();
      expect(resultCalls).toBe(2);
      expect(wrapper.emitted("island-notice") || []).toHaveLength(0);

      await vi.advanceTimersByTimeAsync(1000);
      await flushPromises();
      await flushPromises();
      expect(resultCalls).toBe(3);
      expect(wrapper.emitted("island-notice")).toHaveLength(1);
      expect(wrapper.emitted("island-notice")?.[0]?.[0]).toMatchObject({
        id: "flow-retry-notice:boss:result-a",
        target: "results",
      });
      wrapper.unmount();
      vi.unstubAllGlobals();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not emit a ghost island notice when an existing Flow result hydrates on first mount", async () => {
    sessionStorage.setItem("career-scout-workflow:profile-flow-first-hydration", JSON.stringify({
      version: 2,
      unfinished: false,
      activeStep: "results",
      analysisReady: true,
      scrapeCompleted: true,
      resultLoaded: true,
      resultsPageSeen: true,
    }));
    const fetchMock = oneClickBase({
      "/api/flows/current": () => response({ ok: true, flow: {
        id: "flow-first-hydration", profile_id: "profile-flow-first-hydration", selection: "all", status: "done",
        tracks: [{ id: "boss-track", platform: "boss", status: "done", stage: "complete", result_run_id: "first-result" }],
      } }),
      "/api/flows/flow-first-hydration/results": () => response({ ok: true, results: {
        flow_id: "flow-first-hydration", selection: "all", status: "done", tracks: [{
          id: "boss-track", platform: "boss", status: "done", stage: "complete", result_run_id: "first-result",
          jobs: [{ job_id: "first-hydration-job", platform: "boss", title: "已存在结果", verdict: "match" }], dropped: [],
        }], jobs: [],
      } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-flow-first-hydration" } });
    await flushPromises();
    await flushPromises();

    expect(wrapper.emitted("island-notice") || []).toHaveLength(0);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("stops a pending Flow result retry when DiscoveryView unmounts", async () => {
    vi.useFakeTimers();
    try {
      let resultCalls = 0;
      const fetchMock = oneClickBase({
        "/api/flows/current": () => response({
          ok: true,
          flow: {
            id: "flow-retry-dispose", profile_id: "profile-retry-dispose", selection: "all", status: "done",
            tracks: [{ id: "boss-track", platform: "boss", status: "done", stage: "complete", result_run_id: "result-a" }],
          },
        }),
        "/api/flows/flow-retry-dispose/results": () => {
          resultCalls += 1;
          return Promise.reject(new Error("temporary results failure"));
        },
      });
      vi.stubGlobal("fetch", fetchMock);

      const wrapper = mount(DiscoveryView, { props: { profileId: "profile-retry-dispose" } });
      await flushPromises();
      await flushPromises();
      expect(resultCalls).toBe(2);

      wrapper.unmount();
      await vi.advanceTimersByTimeAsync(2000);
      await flushPromises();
      expect(resultCalls).toBe(2);
      expect(wrapper.emitted("island-notice") || []).toHaveLength(0);
      vi.unstubAllGlobals();
    } finally {
      vi.useRealTimers();
    }
  });

  it("T513 empty state: no task and no result renders the default all-platform draft without task progress", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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

    expect(wrapper.find('[data-testid="platform-current-all"]').exists()).toBe(true);
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
    await confirmProfile(wrapper, "3年Python后端候选人");
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
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
    await confirmProfile(wrapper, "3年Python后端候选人");
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
      if (url.includes("/api/latest-running-task")) {
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
    expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(true);

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
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) {
        return response({
          ok: true, items: [{
            run_id: "partial-run", platform: "boss", status: "completed_with_pending",
            created_at: "2026-08-27", total_scraped: 1, total_kept: 1,
            total_matched: 0, mismatch_count: 0, total_dropped: 0, pending_count: 1,
            keyword_summary: "前端", profile_summary_preview: "", is_latest: true,
          }],
        });
      }
      if ((url.includes("/api/result-history/partial-run?") || url.endsWith("/api/result-history/partial-run"))) {
        return response({
          ok: true, has_result: true, source_run_id: "partial-run", platform: "boss",
          status: "completed_with_pending",
          result: {
            jobs: [{ job_id: "j1", title: "前端", verdict: "uncertain", verdict_reason: "详情超时" }],
            total_kept: 1, total_dropped: 0,
          },
        });
      }
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();

    // 025 B078：完成态启动自动新一轮——干净 01 页，不再把结果糊到 04
    expect(wrapper.find('[data-testid="resume-input"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="custom-keyword"]').isVisible()).toBe(false);

    // 上一轮结论通过历史查看：进入 completed_with_pending 历史轮 → 04 页 partial 状态
    (wrapper.vm as any).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-run-id="partial-run"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(true);
    // completed_with_pending 轮渲染 partial 状态标记（"部分结果"）
    expect(wrapper.get('[data-testid="history-round-marker"]').text()).toContain("部分结果");

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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
    await wrapper.get('[data-testid="pending-recrawl"]').trigger("click");
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
          // B033：一键任务冻结画像事实快照（来自简历分析）
          auto_screen_facts: { core_skills: ["Python"], job_type: "全职" },
        });
        return response({ ok: true, task_id: "one-scrape" });
      },
      "/api/task-state/one-scrape": () => response({ status: "completed", progress: {}, logs: [], platform: "boss", scraped_count: 1 }),
      "/api/ai-screen": (url, init) => {
        expect(JSON.parse(String(init?.body))).toMatchObject({
          consume_auto_screen: true,
          screening_fields: { salary: ["406"], stage: ["804"] },
          profile_summary: "3年Python后端候选人",
          // B033：筛选请求透传画像事实（来自简历分析）
          profile_facts: { core_skills: ["Python"], job_type: "全职" },
        });
        return response({ ok: true, task_id: "one-screen" });
      },
      "/api/task-state/one-screen": () => response({ status: "completed", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    // 先上传简历分析（B033：画像事实随分析进入前端状态，一键链路带值透传）
    const file = new File(["resume"], "resume.txt", { type: "text/plain" });
    Object.defineProperty(wrapper.get('[data-testid="resume-input"]').element, "files", {
      value: [file],
      configurable: true,
    });
    await wrapper.get('[data-testid="resume-input"]').trigger("change");
    await wrapper.get('[data-testid="resume-consent"]').setValue(true);
    await wrapper.get('[data-testid="analyze-resume"]').trigger("click");
    await flushPromises();
    await confirmProfile(wrapper);
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
    const buttons = wrapper.findAll('.one-click-filter-groups button');
    // 简历分析已投影 salary=20-50K（选中态），只补选 B轮
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
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
  it("方案2: 窄屏（单列）下两个抽屉独立开关，互不影响", async () => {
    (globalThis as any).__setNarrowMatchMedia(true);
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:panels-narrow" }, deduplicated: { keywords: ["python"], cities: ["上海"] } });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, {
      props: { profileId: "p1" },
      global: { stubs: { LocationPicker: true, TaskProgress: true } },
    });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await flushPromises();

    const keywordCard = ".search-layout > .collapsible-card:first-child";
    const advancedCard = ".advanced-panel";

    // ① 窄屏默认：两卡都展开（与宽屏一致）
    expect(wrapper.find(`${keywordCard} .collapsible-body.open`).exists()).toBe(true);
    expect(wrapper.find(`${advancedCard} .collapsible-body.open`).exists()).toBe(true);

    // ② 点「高级执行设置」头部：只收起自己，广泛抓取保持展开
    await wrapper.get(`${advancedCard} .collapsible-header`).trigger("click");
    await flushPromises();
    expect(wrapper.find(`${keywordCard} .collapsible-body.open`).exists()).toBe(true);
    expect(wrapper.find(`${advancedCard} .collapsible-body.open`).exists()).toBe(false);

    // ③ 再点「广泛抓取」头部：只收起自己，高级执行保持收起
    await wrapper.get(`${keywordCard} .collapsible-header`).trigger("click");
    await flushPromises();
    expect(wrapper.find(`${keywordCard} .collapsible-body.open`).exists()).toBe(false);
    expect(wrapper.find(`${advancedCard} .collapsible-body.open`).exists()).toBe(false);

    vi.unstubAllGlobals();
    (globalThis as any).__setNarrowMatchMedia(false);
  });
  it("B040: search drawers stay collapsed when returning to step 2 while scraping", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:b040-search" }, deduplicated: { keywords: ["python"], cities: ["上海"] } });
      }
      if (url.endsWith("/api/execute-search")) return new Promise<Response>(() => { /* keep running */ });
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
    await confirmProfile(wrapper, "3年Python后端候选人");
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    const keywordCard = ".search-layout > .collapsible-card:first-child";
    expect(wrapper.find(`${keywordCard} .collapsible-body.open`).exists()).toBe(false);

    await wrapper.findAll("button").find((b) => b.text().includes("上传简历"))!.trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("广泛抓取"))!.trigger("click");
    await flushPromises();
    expect(wrapper.find(`${keywordCard} .collapsible-body.open`).exists()).toBe(false);
    expect(wrapper.find(".advanced-panel .collapsible-body.open").exists()).toBe(false);
    vi.unstubAllGlobals();
  });

  it("B040: screen card stays collapsed when returning to step 3 while AI screening", async () => {
    const settings = {
      pages: 3, inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:b040-screen" }, deduplicated: { keywords: ["python"], cities: ["上海"] } });
      }
      if (url.endsWith("/api/execute-search")) return response({ ok: true, task_id: "scrape-b040-screen" });
      if (url.includes("/api/task-state/scrape-b040-screen")) return response({ status: "completed", progress: {}, logs: [] });
      if (url.endsWith("/api/ai-screen")) return new Promise<Response>(() => { /* keep running */ });
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
    await confirmProfile(wrapper);
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    const screenCard = ".workflow-stack > .collapsible-card";
    expect(wrapper.find(`${screenCard} .collapsible-body.open`).exists()).toBe(true);
    await wrapper.get('[data-testid="start-ai-screen"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(`${screenCard} .collapsible-body.open`).exists()).toBe(false);

    await wrapper.findAll("button").find((b) => b.text().includes("上传简历"))!.trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("AI 筛选"))!.trigger("click");
    await flushPromises();
    expect(wrapper.find(`${screenCard} .collapsible-body.open`).exists()).toBe(false);
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
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
    await confirmProfile(wrapper);
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

  it("B008: screen filter card expands when returning from an existing result page with no running task", async () => {
    const settings = {
      pages: 3, inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        // 025 B078：完成态启动已自动新一轮，结果页只通过未完成态恢复——
        // 暂停任务恢复是"已有结果、无任务在跑"的可达场景。
        return response({
          ok: true, has_task: true, task_id: "screen-paused", kind: "ai_screen",
          status: "paused", platform: "boss", scrape_task_id: "scrape-1",
          scrape_completed: true, frozen_filters: { salary: ["20-30K"] },
          profile_summary: "画像",
          pause_info: { error_code: "user_paused", error_reason: "用户已暂停" },
          progress: { stage: "ai_fine", message: "已暂停" }, logs: [],
        });
      }
      if (url.includes("/api/task-state/screen-paused")) {
        return response({ status: "paused", progress: { stage: "ai_fine" }, logs: [], error: "用户已暂停" });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({
          ok: true, has_result: true, source_run_id: "run-paused", status: "paused",
          result: { jobs: [], total_kept: 0, total_dropped: 0 },
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
    const screenCard = ".workflow-stack > .collapsible-card";
    // 无任务在跑（暂停恢复）时筛选卡默认展开（025 起完成态启动走自动新一轮，
    // 03 页筛选卡展开策略由未完成态恢复场景覆盖）。
    expect(wrapper.find(`${screenCard} .collapsible-body.open`).exists()).toBe(true);
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
        { key: "recruiter_activity", label: "招聘者上次活跃", multiple: false, options: [{ value: "month", label: "近一个月" }] },
      ],
    };
    const zhilianSchemaRich = {
      ok: true, platform: "zhilian", schema_version: 2, enabled_for_new_tasks: true,
      fields: [
        { key: "experience", label: "经验要求", multiple: true, options: [{ value: "0305", label: "3-5年" }] },
        { key: "company_nature", label: "公司性质", multiple: true, options: [{ value: "1", label: "国企" }] },
        { key: "recruiter_activity", label: "招聘者上次活跃", multiple: false, options: [{ value: "month", label: "近一个月" }] },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
          semantic: { experience: ["3-5年"], stage: ["B轮"], company_nature: ["国企"], "招聘者活跃时间": ["近一个月"] },
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
    await confirmProfile(wrapper);
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
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
    expect(chipsText).toContain("招聘者上次活跃: 近一个月");
    expect(chipsText).not.toContain("融资阶段");
    expect(chipsText).not.toContain("105");
    expect(chipsText).not.toContain("0305");
    vi.unstubAllGlobals();
  });

  it("B011: summary chips keep full text without hard width or ellipsis", () => {
    const css = readFileSync(path.join(__dirname, "../../styles.css"), "utf8");
    const chipBlock = css.match(/\.summary-chip\s*\{[^}]*\}/s)?.[0] || "";
    expect(chipBlock).toContain("white-space: nowrap");
    expect(chipBlock).not.toContain("max-width: 180px");
    expect(chipBlock).not.toContain("text-overflow: ellipsis");
    expect(chipBlock).not.toContain("overflow: hidden");
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
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
    await confirmProfile(wrapper, "3年Python后端候选人");
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
    // Spec041 后续（用户拍板）：第 2 页输入两个平台共用——智联直接沿用 BOSS 的关键词/城市。
    expect(wrapper.find('[data-testid="keyword-chip"]').exists()).toBe(true);

    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: true, source_run_id: "run-results", status: "completed", result: { jobs: [], total_kept: 0, total_dropped: 0 } });
      }
      // 025 B078：完成态启动自动新一轮，结果页平台锁定经历史轮验证
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) {
        return response({
          ok: true, items: [{
            run_id: "run-results", platform: "boss", status: "completed",
            created_at: "2026-08-27", total_scraped: 0, total_kept: 0,
            total_matched: 0, mismatch_count: 0, total_dropped: 0, pending_count: 0,
            keyword_summary: "", profile_summary_preview: "", is_latest: true,
          }],
        });
      }
      if ((url.includes("/api/result-history/run-results?") || url.endsWith("/api/result-history/run-results"))) {
        return response({ ok: true, has_result: true, source_run_id: "run-results", platform: "boss", status: "completed", result: { jobs: [], total_kept: 0, total_dropped: 0 } });
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
    // 完成态启动自动新一轮（干净 01 页）；经历史轮进入结果页
    (wrapper.vm as any).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-run-id="run-results"]').trigger("click");
    await flushPromises();
    // 结果页（含历史轮）平台切换锁定
    expect(wrapper.get('[data-testid="platform-segment-boss"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("disabled")).toBeDefined();
    vi.unstubAllGlobals();
  });

  it("R2: a completed single-platform task shows only the newest result round", async () => {
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) {
        if (!screenDone) return response({ ok: true, has_result: false });
        if (!url.includes("platform=")) {
          return response({
            ok: true, has_result: true, source_run_id: "run-zhilian", platform: "zhilian",
            status: "completed", started_at: 2_000, finished_at: 3_000,
            result: {
              jobs: [{ job_id: "z-1", platform: "zhilian", title: "智联岗位", company: "智联公司", verdict: "match" }],
              total_scraped: 1, total_matched: 1, total_kept: 1, total_dropped: 0,
            },
          });
        }
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
      if (url.includes("/api/task-state/scrape-r2")) {
        return response({ ok: true, status: "completed", progress: {}, logs: [], platform: "boss" });
      }
      if (url.endsWith("/api/ai-screen")) return response({ ok: true, task_id: "screen-r2" });
      if (url.includes("/api/task-state/screen-r2")) {
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
    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await flushPromises();
    await confirmProfile(wrapper);
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="confirm-national-scope"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-ai-screen"]').trigger("click");
    await flushPromises();

    // 完成路径只读取当前画像的全局最新轮，不再分别读取两个平台再拼接。
    const mergeCalls = fetchMock.mock.calls
      .filter(([u]) => String(u).includes("/api/latest-pipeline-result"))
      .map(([u]) => String(u));
    expect(mergeCalls.length).toBeGreaterThanOrEqual(1);
    expect(mergeCalls.every((u) => !u.includes("platform="))).toBe(true);
    expect(mergeCalls.some((u) => u.includes("platform="))).toBe(false);
    // 智联轮更新时间更晚，因此旧 BOSS 轮不得混入。
    const rows = wrapper.findAll('[data-testid="job-row"]');
    const rowText = rows.map((row) => row.text()).join(" | ");
    expect(rowText).not.toContain("BOSS岗位");
    expect(rowText).toContain("智联岗位");

    vi.unstubAllGlobals();
  });

  it("B044: completed live screen task still shows the results page when the merge fetch returns nothing at the completion instant", async () => {
    // 复现：任务完成瞬间 /api/latest-pipeline-result 拉取为空（快照提交前
    // 的瞬时空窗 / 双平台请求任一失败被吞），但 task-state 完成响应自带
    // 内存结果。04 页必须立即展示岗位，且步骤条 04 可点。
    const settings = {
      pages: 3,
      inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
      screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      // 完成瞬间合并拉取恒为空：模拟快照尚未可见 / 请求瞬时失败的窗口
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:b044" },
          deduplicated: {},
        });
      }
      if (url.endsWith("/api/execute-search")) return response({ ok: true, task_id: "scrape-b044" });
      if (url.includes("/api/task-state/scrape-b044")) {
        return response({ ok: true, status: "completed", progress: {}, logs: [], platform: "boss" });
      }
      if (url.endsWith("/api/ai-screen")) return response({ ok: true, task_id: "screen-b044" });
      if (url.includes("/api/task-state/screen-b044")) {
        // 与真实后端一致：内存完成态响应携带完整 result（与 status=done 同锁写入）
        return response({
          ok: true, status: "completed", progress: { message: "筛选完成" }, logs: [], platform: "boss",
          result: {
            ok: true,
            jobs: [{ job_id: "b-1", title: "BOSS岗位", company: "BOSS公司", verdict: "match", platform: "boss" }],
            dropped: [],
            total_scraped: 1, total_matched: 1, total_kept: 1, total_dropped: 0,
            profile_summary: "3年Python后端候选人",
          },
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
    await confirmProfile(wrapper);
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="confirm-national-scope"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-ai-screen"]').trigger("click");
    await flushPromises();

    // 04 自动进入且立即展示岗位（不得出现"暂无结果"空白页）
    expect(wrapper.find('[data-testid="latest-result-empty"]').exists()).toBe(false);
    const rows = wrapper.findAll('[data-testid="job-row"]');
    const rowText = rows.map((row) => row.text()).join(" | ");
    expect(rowText).toContain("BOSS岗位");
    // 步骤条 04 可点（resultLoaded 为 true）
    const resultsStep = wrapper.findAll("button").find((b) => b.text().includes("查看结果"))!;
    expect((resultsStep.element as HTMLButtonElement).disabled).toBe(false);

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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
    await wrapper.get(".adv-save-btn").trigger("click");
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
      if (url.includes("/api/latest-running-task")) {
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
            profile_summary: "3年Python后端候选人",
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
    await wrapper.get('[data-testid="finish-save-results"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(".results-stage").isVisible()).toBe(false);
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="start-ai-screen"]').exists()).toBe(true);
    vi.unstubAllGlobals();
  });

  it("finish save button shows disabled spinner state while the request is pending", async () => {
    let resolveFinish: (() => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "scrape-failed-1", kind: "scrape",
          status: "failed", platform: "boss", scraped_count: 1280, source_total: 3000,
          progress: { message: "抓取失败" }, logs: [], error: "列表抓取失败",
          pause_info: { error_code: "scrape_failed", error_reason: "列表抓取失败" },
        });
      }
      if (url.includes("/api/task/finish/scrape-failed-1")) {
        return new Promise<Response>((resolve) => {
          resolveFinish = () => resolve(response({
            ok: true, run_id: "scrape-failed-1", snapshot_run_id: "snap-1",
            platform: "boss", status: "completed_with_pending", scrape_task_id: "scrape-failed-1",
            result: { jobs: [], dropped: [], total_scraped: 0, total_kept: 0, total_dropped: 0 },
          }));
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
    const button = wrapper.get('[data-testid="finish-save-results"]');
    await button.trigger("click");
    await flushPromises();
    expect(button.attributes("disabled")).toBeDefined();
    expect(button.text()).toContain("正在保存…");
    expect(button.find(".spin").exists()).toBe(true);
    resolveFinish?.();
    await flushPromises();
    vi.unstubAllGlobals();
  });

  it("B027: continue AI after finish actually starts ai-screen", async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/api/latest-running-task")) {
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
            profile_summary: "3年Python后端候选人",
          },
        });
      }
      if (url.endsWith("/api/ai-screen")) {
        return response({ ok: true, task_id: "screen-cont-1" });
      }
      if (url.includes("/api/task-state/screen-cont-1")) {
        return response({ status: "running", progress: { message: "AI 筛选中" }, logs: [] });
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
    await wrapper.get('[data-testid="finish-save-results"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-ai-screen"]').trigger("click");
    await flushPromises();
    expect(calls.some((url) => url.endsWith("/api/ai-screen"))).toBe(true);
    expect(wrapper.find('[data-testid="pause-ai-screen"]').exists()).toBe(true);
    vi.unstubAllGlobals();
  });

  it("B027: running AI screen can finish and save without jumping to results", async () => {
    let stateCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "screen-running-1", kind: "ai_screen",
          status: "running", platform: "boss", scrape_task_id: "scrape-parent",
          scrape_completed: true, progress: { message: "AI 筛选中" }, logs: [], error: "",
        });
      }
      if (url.includes("/api/task-state/screen-running-1")) {
        stateCalls += 1;
        if (stateCalls === 1) return response({ status: "running", progress: { message: "AI 筛选中" }, logs: [] });
        return response({ status: "paused", progress: { message: "任务已暂停" }, logs: [], error: "" });
      }
      if (url.includes("/api/task/pause/screen-running-1")) {
        return response({ ok: true, run_id: "screen-running-1", status: "pausing" });
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
    vi.useFakeTimers();
    expect(wrapper.find('[data-testid="pause-ai-screen"]').exists()).toBe(true);
    await wrapper.get('[data-testid="pause-ai-screen"]').trigger("click");
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();
    vi.useRealTimers();
    expect(wrapper.find(".results-stage").isVisible()).toBe(false);
    expect(wrapper.find('[data-testid="continue-ai-screen"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="view-screen-results"]').exists()).toBe(false);
    vi.unstubAllGlobals();
  });

  it("013: paused screen loads partial results so 04 can be viewed immediately", async () => {
    let stateCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "screen-pause-1", kind: "ai_screen",
          status: "running", platform: "boss", scrape_task_id: "scrape-parent",
          scrape_completed: true, progress: { message: "AI 筛选中" }, logs: [], error: "",
        });
      }
      if (url.includes("/api/task-state/screen-pause-1")) {
        stateCalls += 1;
        if (stateCalls === 1) return response({ status: "running", progress: { message: "AI 筛选中" }, logs: [] });
        return response({ status: "paused", progress: { message: "任务已暂停" }, logs: [], error: "" });
      }
      if (url.includes("/api/task/pause/screen-pause-1")) {
        return response({ ok: true, run_id: "screen-pause-1", status: "pausing" });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) return response({ ok: true, has_result: false });
        return response({
          ok: true, has_result: true, source_run_id: "run-pause", platform: "boss",
          status: "paused", scrape_task_id: "scrape-parent",
          round_context: {
            platform: "boss", keywords: ["Python"], cities: ["上海"],
            screening_fields: { salary: ["20-30K"] }, profile_summary: "3年Python后端候选人",
            profile_facts: {}, scrape_task_id: "scrape-parent", screen_run_id: "run-pause",
            status: "paused", resumable: true, has_frozen_filters: true,
          },
          result: {
            jobs: [{ job_id: "p1", platform: "boss", verdict: "match", title: "已判定部分岗位" }],
            dropped: [], total_scraped: 1, total_kept: 1, total_dropped: 0,
            profile_summary: "3年Python后端候选人",
          },
        });
      }
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
    vi.useFakeTimers();
    expect(wrapper.find('[data-testid="pause-ai-screen"]').exists()).toBe(true);
    await wrapper.get('[data-testid="pause-ai-screen"]').trigger("click");
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();
    await flushPromises();
    vi.useRealTimers();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await flushPromises();
    expect(wrapper.text()).toContain("已暂停");
    expect(wrapper.text()).not.toContain("完成，但有待确认");
    expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="view-screen-results"]').exists()).toBe(false);
    // 暂停任务未结束：04 结果页保持不可进，用户留在 03 处理继续/结束保存
    const viewBtn = wrapper.findAll("button").find((b) => b.text().includes("查看结果"));
    expect(viewBtn).toBeDefined();
    expect((viewBtn!.element as HTMLButtonElement).disabled).toBe(true);
    expect(wrapper.find(".results-stage").isVisible()).toBe(false);
    vi.unstubAllGlobals();
  });
  it("013: refreshed paused task keeps 04 closed until the task ends", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "paused-screen-1", kind: "ai_screen",
          status: "paused", platform: "boss", scrape_task_id: "scrape-parent",
          scrape_completed: true, frozen_filters: { salary: ["406"] },
          profile_summary: "3年Python后端候选人",
          round_context: {
            platform: "boss", keywords: ["Python"], cities: ["上海"],
            screening_fields: { salary: ["406"] }, profile_summary: "3年Python后端候选人", profile_facts: {},
            scrape_task_id: "scrape-parent", screen_run_id: "paused-screen-1",
            status: "paused", resumable: true, has_frozen_filters: true,
          },
        });
      }
      if (url.includes("/api/task-state/paused-screen-1")) {
        return response({ status: "paused", success_count: 1, fail_count: 0, unstarted_count: 1, total: 2 });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) return response({ ok: true, has_result: false });
        return response({
          ok: true, has_result: true, source_run_id: "paused-screen-1", platform: "boss",
          status: "completed_with_pending", scrape_task_id: "scrape-parent",
          round_context: {
            platform: "boss", keywords: ["Python"], cities: ["上海"],
            screening_fields: { salary: ["406"] }, profile_summary: "3年Python后端候选人", profile_facts: {},
            scrape_task_id: "scrape-parent", screen_run_id: "paused-screen-1",
            status: "paused", resumable: true, has_frozen_filters: true,
          },
          result: {
            jobs: [{ job_id: "p1", platform: "boss", verdict: "uncertain", title: "暂停部分岗位" }],
            dropped: [], total_scraped: 2, total_kept: 2, total_dropped: 0,
            profile_summary: "3年Python后端候选人",
          },
        });
      }
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
    await flushPromises();
    // 暂停任务刷新后仍在 03：04 结果页不开放（步骤导航「查看结果」禁用）
    const stepBtn = wrapper.findAll("button").find((b) => b.text().includes("查看结果"));
    expect(stepBtn).toBeDefined();
    expect((stepBtn!.element as HTMLButtonElement).disabled).toBe(true);
    await stepBtn!.trigger("click");
    await flushPromises();
    expect(wrapper.find(".results-stage").isVisible()).toBe(false);
    expect(wrapper.text()).not.toContain("暂停部分岗位");
    expect(wrapper.find('[data-testid="continue-ai-from-results"]').exists()).toBe(false);
    vi.unstubAllGlobals();
  });

  it("013: stale latest result must not hijack continue of a paused screen", async () => {
    let stateCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "screen-pause-stale", kind: "ai_screen",
          status: "running", platform: "boss", scrape_task_id: "scrape-current",
          scrape_completed: true, progress: { message: "AI 筛选中" }, logs: [], error: "",
        });
      }
      if (url.includes("/api/task-state/screen-pause-stale")) {
        stateCalls += 1;
        if (stateCalls === 1) return response({ status: "running", progress: { message: "AI 筛选中" }, logs: [] });
        return response({ status: "paused", progress: { message: "触发验证码" }, logs: [], error: "" });
      }
      if (url.includes("/api/task/pause/screen-pause-stale")) {
        return response({ ok: true, run_id: "screen-pause-stale", status: "pausing" });
      }
      if (url.includes("/api/task/continue/screen-pause-stale")) {
        return response({ ok: true, task_id: "screen-resumed" });
      }
      if (url.includes("/api/task-state/screen-resumed")) {
        return response({ status: "running", progress: { message: "续跑中" }, logs: [] });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) return response({ ok: true, has_result: false });
        return response({
          ok: true, has_result: true, source_run_id: "old-snapshot-run", platform: "boss",
          status: "completed_with_pending", scrape_task_id: "scrape-old-gone",
          round_context: {
            platform: "boss", keywords: ["Python"], cities: ["上海"],
            screening_fields: { salary: ["20-30K"] }, profile_summary: "旧结果画像", profile_facts: {},
            scrape_task_id: "scrape-old-gone", screen_run_id: "old-snapshot-run",
            status: "partial", resumable: true, has_frozen_filters: true,
          },
          result: {
            jobs: [], dropped: [], total_scraped: 0, total_kept: 0, total_dropped: 0,
            profile_summary: "旧结果画像",
          },
        });
      }
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
    vi.useFakeTimers();
    expect(wrapper.find('[data-testid="pause-ai-screen"]').exists()).toBe(true);
    await wrapper.get('[data-testid="pause-ai-screen"]').trigger("click");
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();
    vi.useRealTimers();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await flushPromises();
    const continueBtn = wrapper.find('[data-testid="continue-ai-screen"]');
    expect(continueBtn.exists()).toBe(true);
    continueBtn.element.dispatchEvent(new Event("click", { bubbles: true }));
    await flushPromises();
    const continueCall = fetchMock.mock.calls.find(
      ([u]) => String(u).includes("/api/task/continue/screen-pause-stale"),
    );
    expect(continueCall).toBeTruthy();
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/api/ai-screen"))).toBe(false);
    vi.unstubAllGlobals();
  });

  it("013: paused screen offers finish-save that ends the run", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        return response({
          ok: true, has_task: true, task_id: "paused-finish-1", kind: "ai_screen",
          status: "paused", platform: "boss", scrape_task_id: "scrape-parent",
          scrape_completed: true, frozen_filters: { salary: ["406"] },
          profile_summary: "3年Python后端候选人",
          round_context: {
            platform: "boss", keywords: ["Python"], cities: ["上海"],
            screening_fields: { salary: ["406"] }, profile_summary: "3年Python后端候选人", profile_facts: {},
            scrape_task_id: "scrape-parent", screen_run_id: "paused-finish-1",
            status: "paused", resumable: true, has_frozen_filters: true,
          },
        });
      }
      if (url.includes("/api/task-state/paused-finish-1")) {
        return response({ status: "paused", pause_info: { error_code: "source_verification_required", error_reason: "验证码" }, success_count: 1, fail_count: 0, unstarted_count: 1, total: 2 });
      }
      if (url.includes("/api/task/finish/paused-finish-1")) {
        return response({
          ok: true, run_id: "paused-finish-1", snapshot_run_id: "snap-finish", platform: "boss",
          status: "completed_with_pending", scrape_task_id: "scrape-parent",
          result: {
            jobs: [{ job_id: "p1", platform: "boss", verdict: "uncertain", title: "暂停部分岗位" }],
            dropped: [], total_scraped: 2, total_kept: 1, total_dropped: 0,
          },
        });
      }
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
    const finishBtn = wrapper.get('[data-testid="finish-save-results"]');
    expect(finishBtn.attributes("disabled")).toBeUndefined();
    finishBtn.element.dispatchEvent(new Event("click", { bubbles: true }));
    await flushPromises();
    const finishCall = fetchMock.mock.calls.find(
      ([u]) => String(u).includes("/api/task/finish/paused-finish-1"),
    );
    expect(finishCall).toBeTruthy();
    vi.unstubAllGlobals();
  });

  it("013: only the newest resumable platform can be continued from 04", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian") || !url.includes("platform=")) {
          return response({
            ok: true, has_result: true, source_run_id: "run-z", platform: "zhilian",
            status: "paused", scrape_task_id: "scrape-z", started_at: 2_000,
            round_context: {
              platform: "zhilian", keywords: ["前端"], cities: ["北京"],
              screening_fields: { salary: ["20-30K"] }, profile_summary: "5年前端", profile_facts: {},
              scrape_task_id: "scrape-z", screen_run_id: "run-z",
              status: "paused", resumable: true, has_frozen_filters: true,
            },
            result: {
              jobs: [{ job_id: "z1", platform: "zhilian", verdict: "match", title: "智联岗位" }],
              dropped: [], total_scraped: 1, total_kept: 1, total_dropped: 0, profile_summary: "5年前端",
            },
          });
        }
        return response({
          ok: true, has_result: true, source_run_id: "run-b", platform: "boss",
          status: "paused", scrape_task_id: "scrape-b", started_at: 1_000,
          round_context: {
            platform: "boss", keywords: ["Python"], cities: ["上海"],
            screening_fields: { salary: ["20-30K"] }, profile_summary: "3年Python后端", profile_facts: {},
            scrape_task_id: "scrape-b", screen_run_id: "run-b",
            status: "paused", resumable: true, has_frozen_filters: true,
          },
          result: {
            jobs: [{ job_id: "b1", platform: "boss", verdict: "match", title: "BOSS岗位" }],
            dropped: [], total_scraped: 1, total_kept: 1, total_dropped: 0, profile_summary: "3年Python后端",
          },
        });
      }
      if (url.includes("/api/task/continue/run-z")) {
        return response({ ok: true, task_id: "screen-continue-z" });
      }
      if (url.includes("/api/task-state/screen-continue-z")) {
        return response({ status: "running", progress: { message: "续跑中" }, logs: [] });
      }
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
    await wrapper.findAll("button").find((b) => b.text().includes("查看结果"))!.trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("AI 筛选"))!.trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="continue-ai-screen"]').trigger("click");
    await flushPromises();
    const continueCall = fetchMock.mock.calls.find(
      ([u]) => String(u).includes("/api/task/continue/run-z"),
    );
    expect(continueCall).toBeTruthy();
    expect(wrapper.find('[data-testid="continue-platform-guide"]').exists()).toBe(false);
    expect(wrapper.find(".results-stage").isVisible()).toBe(false);
    vi.unstubAllGlobals();
  });

  it("013: an older resumable platform is hidden behind a newer completed round", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian") || !url.includes("platform=")) {
          return response({
            ok: true, has_result: true, source_run_id: "run-z", platform: "zhilian",
            status: "succeeded", scrape_task_id: "scrape-z", started_at: 2_000,
            round_context: {
              platform: "zhilian", keywords: ["前端"], cities: ["北京"],
              screening_fields: { salary: ["20-30K"] }, profile_summary: "5年前端", profile_facts: {},
              scrape_task_id: "scrape-z", screen_run_id: "run-z",
              status: "succeeded", resumable: false, has_frozen_filters: true,
            },
            result: {
              jobs: [{ job_id: "z1", platform: "zhilian", verdict: "match", title: "智联岗位" }],
              dropped: [], total_scraped: 1, total_kept: 1, total_dropped: 0,
            },
          });
        }
        return response({
          ok: true, has_result: true, source_run_id: "run-b", platform: "boss",
          status: "paused", scrape_task_id: "scrape-b", started_at: 1_000,
          round_context: {
            platform: "boss", keywords: ["Python"], cities: ["上海"],
            screening_fields: { salary: ["20-30K"] }, profile_summary: "3年Python后端", profile_facts: {},
            scrape_task_id: "scrape-b", screen_run_id: "run-b",
            status: "paused", resumable: true, has_frozen_filters: true,
          },
          result: {
            jobs: [{ job_id: "b1", platform: "boss", verdict: "match", title: "BOSS岗位" }],
            dropped: [], total_scraped: 1, total_kept: 1, total_dropped: 0,
          },
        });
      }
      if (url.includes("/api/task/continue/run-b")) {
        return response({ ok: true, task_id: "screen-continue-b" });
      }
      if (url.includes("/api/task-state/screen-continue-b")) {
        return response({ status: "running", progress: { message: "续跑中" }, logs: [] });
      }
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
    await wrapper.findAll("button").find((b) => b.text().includes("查看结果"))!.trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("AI 筛选"))!.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="continue-ai-screen"]').exists()).toBe(false);
    const continueCall = fetchMock.mock.calls.find(
      ([u]) => String(u).includes("/api/task/continue/run-b"),
    );
    expect(continueCall).toBeFalsy();
    expect(wrapper.find('[data-testid="continue-platform-guide"]').exists()).toBe(false);
    expect(wrapper.find(".results-stage").isVisible()).toBe(false);
    vi.unstubAllGlobals();
  });

  it("013/035: newest resumable round is handled without an obsolete platform chooser", async () => {
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) {
          return response({
            ok: true, has_result: true, source_run_id: "run-z", platform: "zhilian",
            status: "paused", scrape_task_id: "scrape-z", started_at: 2_000,
            round_context: {
              platform: "zhilian", keywords: ["前端"], cities: ["北京"],
              screening_fields: { salary: ["20-30K"] }, profile_summary: "5年前端", profile_facts: {},
              scrape_task_id: "scrape-z", screen_run_id: "run-z",
              status: "paused", resumable: true, has_frozen_filters: true,
            },
            result: {
              jobs: [{ job_id: "z1", platform: "zhilian", verdict: "match", title: "智联岗位" }],
              dropped: [], total_scraped: 1, total_kept: 1, total_dropped: 0,
            },
          });
        }
        return response({
          ok: true, has_result: true, source_run_id: "run-b", platform: "boss",
          status: "paused", scrape_task_id: "scrape-b", started_at: 1_000,
          round_context: {
            platform: "boss", keywords: ["Python"], cities: ["上海"],
            screening_fields: { salary: ["20-30K"] }, profile_summary: "3年Python后端", profile_facts: {},
            scrape_task_id: "scrape-b", screen_run_id: "run-b",
            status: "paused", resumable: true, has_frozen_filters: true,
          },
          result: {
            jobs: [{ job_id: "b1", platform: "boss", verdict: "match", title: "BOSS岗位" }],
            dropped: [], total_scraped: 1, total_kept: 1, total_dropped: 0,
          },
        });
      }
      if (url.endsWith("/api/result-history/archive-latest")) {
        return response({ ok: true, archived_run_ids: ["run-b", "run-z"] });
      }
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) return response({ ok: true, items: [] });
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
    await wrapper.findAll("button").find((b) => b.text().includes("查看结果"))!.trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("AI 筛选"))!.trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="continue-ai-screen"]').trigger("click");
    expect(wrapper.find('[data-testid="continue-platform-guide"]').exists()).toBe(false);
    // 返回结果页后仍可开始新一轮，且不会恢复过期平台选择器。
    await wrapper.findAll("button").find((b) => b.text().includes("查看结果"))!.trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("开始新一轮"))!.trigger("click");
    await flushPromises();
    // 035：未结束任务存在时开始新一轮跳回任务视图，不弹确认、不取消任务
    expect(confirmMock).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="continue-platform-guide"]').exists()).toBe(false);
    vi.unstubAllGlobals();
    confirmMock.mockRestore();
  });
  it("B027: latest result restores scrape task id and AI screen uses it", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        // 025 B078：完成态启动已自动新一轮；此场景改为"刷新时任务刚完成"——
        // running 任务轮询到 completed 后进入结果页（recrawl 等结果页功能仍可达）。
        return response({
          ok: true, has_task: true, task_id: "screen-1", kind: "ai_screen",
          status: "running", platform: "boss", scrape_task_id: "scrape-parent",
          scrape_completed: true,
          progress: { stage: "ai_fine", message: "AI 筛选中" }, logs: [],
        });
      }
      if (url.includes("/api/task-state/screen-1")) {
        return response({ ok: true, status: "completed", progress: {}, logs: [], platform: "boss" });
      }
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
      if (url.endsWith("/api/pipeline/recrawl")) return response({ ok: true, task_id: "recrawl-1" }, 202);
      if (url.includes("/api/task-state/recrawl-1")) return response({ status: "running", progress: {}, logs: [] });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(true);
    await wrapper.get('[data-testid="pending-recrawl"]').trigger("click");
    await flushPromises();
    // 单平台待确认：直接重抓该平台，不再弹平台选择引导
    expect(wrapper.find('[data-testid="recrawl-platform-guide"]').exists()).toBe(false);
    const recrawlCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/pipeline/recrawl"));
    expect(recrawlCall).toBeTruthy();
    expect(JSON.parse(String(recrawlCall![1]!.body))).toMatchObject({ source_run_id: "run-boss" });
    vi.unstubAllGlobals();
  });

  it("B030: latest single-platform result recrawls its own run directly", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(true);
    await wrapper.get('[data-testid="pending-recrawl"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="recrawl-platform-guide"]').exists()).toBe(false);
    expect(wrapper.find(".results-stage").classes()).not.toContain("has-recrawl-guide");
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/pipeline/recrawl"));
    expect(call).toBeTruthy();
    expect(JSON.parse(String(call![1]!.body))).toMatchObject({ source_run_id: "run-boss", job_ids: ["b1"] });
    await wrapper.get('[data-testid="result-platform-filter-zhilian"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
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
    const guideStageBlock = css.match(/\.results-stage\.has-recrawl-guide\s*\{[^}]*\}/s)?.[0] || "";
    expect(guideStageBlock).toContain("grid-template-rows: auto auto minmax(0, 1fr)");
    const viewportScript = readFileSync(
      path.join(__dirname, "../../../../tests/sc015_viewport_check.py"), "utf8",
    );
    expect(viewportScript).toContain("(390, 844)");
    expect(viewportScript).toContain("pending-recrawl");
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

  async function confirmProfile(wrapper: ReturnType<typeof mount>, text?: string) {
    const input = wrapper.find('.profile-summary-input');
    if (text !== undefined && input.exists()) {
      await input.setValue(text);
    }
    const confirm = wrapper.find('[data-testid="profile-confirm"]');
    if (confirm.exists()) await confirm.trigger("click");
    await flushPromises();
  }

  function oneClickBase(overrides: Record<string, (url: string, init?: RequestInit) => Promise<Response> | Response> = {}) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      // 路由键按路径匹配，查询参数（如画像参数）不影响命中。
      const route = url.split("?")[0];
      if (overrides[url]) return overrides[url](url, init);
      if (overrides[route]) return overrides[route](url, init);
      if (url.includes("/api/flows/current")) {
        return response({
          ok: true,
          flow: {
            id: "legacy-test-flow",
            profile_id: "profile-1",
            selection: "boss",
            status: "done",
            tracks: [],
          },
        });
      }
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(oneClickSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:one" }, deduplicated: {} });
      }
      if (url.endsWith("/api/analyze-resume")) {
        // B033：简历分析响应携带画像事实（后端契约已由 test_webui_app 覆盖）
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
            profile_summary: "3年Python后端候选人",
            profile_facts: { core_skills: ["Python"], job_type: "全职" },
          },
          labels: {},
        });
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

  async function setKeywordOnly(wrapper: ReturnType<typeof mount>) {
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("Python");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await flushPromises();
    await confirmProfile(wrapper, "3年Python后端候选人");
  }

  it("B042: empty city asks national scope confirm for scrape and submits nationwide", async () => {
    const fetchMock = oneClickBase({
      "/api/execute-search": () => response({ ok: true, task_id: "scrape-nation" }),
      "/api/task-state/scrape-nation": () => response({ status: "completed", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await setKeywordOnly(wrapper);

    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="national-scope-confirm"]').exists()).toBe(true);
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/api/execute-search"))).toBe(false);

    await wrapper.get('[data-testid="cancel-national-scope"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="national-scope-confirm"]').exists()).toBe(false);
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/api/execute-search"))).toBe(false);

    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await wrapper.get('[data-testid="confirm-national-scope"]').trigger("click");
    await flushPromises();
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/execute-search"));
    expect(call).toBeDefined();
    const body = JSON.parse(String(call![1]?.body));
    expect(body.script_params.city).toEqual(["全国"]);
    expect((wrapper.get('[data-testid="custom-city"]').element as HTMLInputElement).value).toBe("");
    vi.unstubAllGlobals();
  });

  it("B042: empty city asks national scope confirm for one-click and continues after confirm", async () => {
    const fetchMock = oneClickBase();
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await setKeywordOnly(wrapper);

    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="national-scope-confirm"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="one-click-confirm"]').exists()).toBe(false);

    await wrapper.get('[data-testid="confirm-national-scope"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="national-scope-confirm"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="one-click-confirm"]').exists()).toBe(true);
    vi.unstubAllGlobals();
  });

  it("B042: zhilian empty city uses the same national scope confirm", async () => {
    const fetchMock = oneClickBase({
      "/api/execute-search": () => response({ ok: true, task_id: "scrape-nation-zhilian" }),
      "/api/task-state/scrape-nation-zhilian": () => response({ status: "completed", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.get('[data-testid="platform-segment-zhilian"]').trigger("click");
    await flushPromises();
    await setKeywordOnly(wrapper);

    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="national-scope-confirm"]').exists()).toBe(true);
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/api/execute-search"))).toBe(false);

    await wrapper.get('[data-testid="confirm-national-scope"]').trigger("click");
    await flushPromises();
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/execute-search"));
    expect(call).toBeDefined();
    const body = JSON.parse(String(call![1]?.body));
    expect(body.platform).toBe("zhilian");
    expect(body.script_params.city).toEqual(["全国"]);
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
    await wrapper.get('[data-testid="profile-confirm"]').trigger("click");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="one-click-confirm"]').exists()).toBe(true);
    vi.unstubAllGlobals();
  });

  it("B032: short or unconfirmed profile only blocks AI entries; edits require re-confirmation", async () => {
    const fetchMock = oneClickBase({
      "/api/execute-search": () => response({ ok: true, task_id: "scrape-short" }),
      "/api/task-state/scrape-short": () => response({ status: "completed", progress: {}, logs: [] }),
      "/api/ai-screen": () => response({ ok: true, task_id: "screen-short" }),
      "/api/task-state/screen-short": () => response({ status: "completed", progress: {}, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);

    await wrapper.get('.profile-summary-input').setValue("短画像");
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/execute-search")).length).toBe(1);

    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/execute-search")).length).toBe(2);

    await wrapper.get('[data-testid="profile-confirm"]').trigger("click");
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/api/execute-search"))).toBe(true);

    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("广泛抓取"))!.trigger("click");
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人（已调整）");
    await wrapper.findAll("button").find((b) => b.text().includes("AI 筛选"))!.trigger("click");
    await wrapper.get('[data-testid="start-ai-screen"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(0);

    await wrapper.findAll("button").find((b) => b.text().includes("广泛抓取"))!.trigger("click");
    await wrapper.get('[data-testid="profile-confirm"]').trigger("click");
    await wrapper.findAll("button").find((b) => b.text().includes("AI 筛选"))!.trigger("click");
    await wrapper.get('[data-testid="start-ai-screen"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(1);
    vi.unstubAllGlobals();
  });

  it("B041: profile confirm button is red by default, turns gray on confirm, and resets on edit", async () => {
    const fetchMock = oneClickBase();
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);

    const confirmBtn = wrapper.get('[data-testid="profile-confirm"]');
    expect(confirmBtn.attributes("data-tip")).toContain("确认后 AI 精筛按当前画像判断");
    expect(confirmBtn.classes()).not.toContain("confirmed");
    expect(confirmBtn.attributes("aria-pressed")).toBe("false");
    expect(wrapper.find(".profile-empty-hint").exists()).toBe(true);

    await wrapper.get('.profile-summary-input').setValue("太短");
    await confirmBtn.trigger("click");
    await flushPromises();
    expect(confirmBtn.classes()).not.toContain("confirmed");
    expect(wrapper.get('[data-testid="profile-inline-error"]').text()).toContain("至少 10 个字");

    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await confirmBtn.trigger("click");
    await flushPromises();
    expect(confirmBtn.classes()).toContain("confirmed");
    expect(confirmBtn.attributes("aria-pressed")).toBe("true");

    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人（调整）");
    await flushPromises();
    expect(confirmBtn.classes()).not.toContain("confirmed");
    expect(confirmBtn.attributes("aria-pressed")).toBe("false");
    vi.unstubAllGlobals();
  });

  it("keeps profile confirmation targetable when the search card is pointer-locked", () => {
    const css = readFileSync(path.join(__dirname, "../../styles.css"), "utf8");
    const lockedContentBlock = css.match(/\.collapsible-card\.locked \.collapsible-content\s*\{[^}]*\}/s)?.[0] || "";
    expect(lockedContentBlock).toContain("pointer-events: none");
    const confirmOverride = css.match(/\.collapsible-card\.locked \.profile-confirm-btn\s*\{[^}]*\}/s)?.[0] || "";
    expect(confirmOverride).toContain("pointer-events: auto");
    expect(confirmOverride).toContain("z-index");
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
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
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
    await confirmProfile(wrapper, "3年Python后端候选人");
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
    await confirmProfile(wrapper);
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(0);

    mode = "failed";
    const wrapper2 = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper2);
    await wrapper2.get('.profile-summary-input').setValue("3年Python后端候选人");
    await confirmProfile(wrapper2);
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
    await confirmProfile(wrapper);
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
    await confirmProfile(wrapper);
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
    await confirmProfile(wrapper);
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
    await confirmProfile(wrapper);
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/ai-screen")).length).toBe(0);
    vi.unstubAllGlobals();
  });

  it("B031: running AI screen disables new scrape and one-click from search step", async () => {
    const fetchMock = oneClickBase({
      "/api/execute-search": () => response({ ok: true, task_id: "one-scrape" }),
      "/api/task-state/one-scrape": () => response({ status: "completed", progress: {}, logs: [], scraped_count: 1 }),
      "/api/ai-screen": () => new Promise<Response>(() => { /* noop */ }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await oneClickSearch(wrapper);
    await wrapper.get('.profile-summary-input').setValue("3年Python后端候选人");
    await confirmProfile(wrapper);
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("广泛抓取"))!.trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="start-scrape"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-testid="start-one-click"]').attributes("disabled")).toBeDefined();
    vi.unstubAllGlobals();
  });

  it("B031: refresh restores a running scrape on the search step with announcement", async () => {
    const fetchMock = oneClickBase({
      "/api/latest-running-task": () => response({
        ok: true, has_task: true, task_id: "running-scrape", kind: "scrape", status: "running",
        platform: "boss", progress: { message: "运行中，列表抓取" }, logs: [], error: "",
      }),
      "/api/task-state/running-scrape": () => response({ status: "running", progress: { message: "运行中，列表抓取" }, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    expect(wrapper.find('[data-testid="custom-keyword"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="task-progress-announcement"]').text()).toContain("运行中");
    vi.unstubAllGlobals();
  });

  it.each(["boss", "zhilian"] as const)(
    "scrape %s exposes the shared pause/finish/terminate actions while running",
    async (platform) => {
      const runId = `scrape-running-actions-${platform}`;
      const fetchMock = oneClickBase({
        "/api/latest-running-task": () => response({
          ok: true, has_task: true, task_id: runId, kind: "scrape", status: "running",
          platform, progress: { current: 5, total: 6, job_count: 198, page: 2, page_total: 2 }, logs: [],
        }),
        [`/api/task-state/${runId}`]: () => response({
          status: "running", progress: { current: 5, total: 6, job_count: 198, page: 2, page_total: 2 }, logs: [],
        }),
      });
      vi.stubGlobal("fetch", fetchMock);
      const wrapper = mount(DiscoveryView, { props: { profileId: `scrape-actions-${platform}` } });
      await flushPromises();

      expect(wrapper.find('[data-testid="pause-scrape"]').exists()).toBe(true);
      expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(true);
      expect(wrapper.find('[data-testid="cancel-scrape"]').exists()).toBe(true);
      expect(wrapper.get('[data-testid="pause-scrape"]').attributes("disabled")).toBeUndefined();

      wrapper.unmount();
      vi.unstubAllGlobals();
    },
  );

  it.each(["boss", "zhilian"] as const)(
    "paused scrape %s routes continue/finish/terminate through the shared action set",
    async (platform) => {
      const runId = `scrape-paused-actions-${platform}`;
      const fetchMock = oneClickBase({
        "/api/latest-running-task": () => response({
          ok: true, has_task: true, task_id: runId, kind: "scrape", status: "paused",
          platform, progress: { current: 5, total: 6, job_count: 198, page: 2, page_total: 2 }, logs: [],
          pause_info: { error_code: "user_paused", error_reason: "用户已暂停" },
        }),
        [`/api/task-state/${runId}`]: () => response({
          status: "paused", progress: { current: 5, total: 6, job_count: 198, page: 2, page_total: 2 }, logs: [],
          pause_info: { error_code: "user_paused", error_reason: "用户已暂停" },
        }),
        [`/api/task/continue/${runId}`]: () => response({ ok: true, task_id: `${runId}-continued` }),
        [`/api/task/finish/${runId}`]: () => response({
          ok: true, platform, status: "completed_with_pending", scrape_task_id: runId,
          result: { jobs: [], total_scraped: 198, total_kept: 0, total_dropped: 0 },
        }),
        [`/api/task/cancel/${runId}`]: () => response({ ok: true, platform, status: "cancelled" }),
      });
      vi.stubGlobal("fetch", fetchMock);
      const wrapper = mount(DiscoveryView, { props: { profileId: `scrape-actions-${platform}` } });
      await flushPromises();

      expect(wrapper.find('[data-testid="continue-scrape"]').exists()).toBe(true);
      expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(true);
      expect(wrapper.find('[data-testid="cancel-paused-scrape"]').exists()).toBe(true);

      await wrapper.get('[data-testid="continue-scrape"]').trigger("click");
      await flushPromises();
      expect(fetchMock.mock.calls.some(([url]) => String(url) === `/api/task/continue/${runId}`)).toBe(true);

      wrapper.unmount();
      vi.unstubAllGlobals();
    },
  );

  it("B031: refresh restores a running AI screen on the screen step with announcement", async () => {
    const fetchMock = oneClickBase({
      "/api/latest-running-task": () => response({
        ok: true, has_task: true, task_id: "screen-running-1", kind: "ai_screen", status: "running",
        platform: "boss", scrape_task_id: "scrape-parent", scrape_completed: true,
        progress: { message: "AI 筛选中" }, logs: [], error: "",
      }),
      "/api/task-state/screen-running-1": () => response({ status: "running", progress: { message: "AI 筛选中" }, logs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    expect(wrapper.find('[data-testid="pause-ai-screen"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="task-progress-announcement"]').text()).toContain("运行中");
    vi.unstubAllGlobals();
  });

  it("B033: recrawl updates merge flags back into current results", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        // 025 B078：完成态启动已自动新一轮；此场景改为"刷新时任务刚完成"
        return response({
          ok: true, has_task: true, task_id: "screen-b033", kind: "ai_screen",
          status: "running", platform: "boss", scrape_task_id: "scrape-b",
          scrape_completed: true,
          progress: { stage: "ai_fine", message: "AI 筛选中" }, logs: [],
        });
      }
      if (url.includes("/api/task-state/screen-b033")) {
        return response({ ok: true, status: "completed", progress: {}, logs: [], platform: "boss" });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) {
          return response({ ok: true, has_result: false });
        }
        return response({
          ok: true, has_result: true, source_run_id: "run-boss", platform: "boss",
          scrape_task_id: "scrape-b", started_at: 1000, status: "completed",
          result: {
            jobs: [{ job_id: "b1", platform: "boss", verdict: "uncertain", verdict_reason: "待确认" }],
            total_kept: 1, total_dropped: 0, profile_summary: "画像",
          },
        });
      }
      if (url.includes("/api/filter-labels")) return response(bossSchema());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      if (url.endsWith("/api/pipeline/recrawl")) return response({ ok: true, task_id: "recrawl-1" }, 202);
      if (url.includes("/api/task-state/recrawl-1")) {
        return response({
          status: "completed", progress: { message: "完成" }, logs: [],
          result: {
            updates: {
              b1: {
                verdict: "not_match",
                verdict_reason: "疑似骗局：要求先交培训费",
                caveats: [],
                flags: [{ code: "C1", level: "high", reason: "要求先交培训费" }],
              },
            },
          },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    // 进入结果页的待确认 tab，发起重抓
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(true);
    await wrapper.findAll("button").find((b) => b!.text().includes("待确认"))!.trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="pending-recrawl"]').trigger("click");
    await flushPromises();
    // 单平台待确认：直接重抓该平台，不再弹平台选择引导
    expect(wrapper.find('[data-testid="recrawl-platform-guide"]').exists()).toBe(false);
    // 重抓完成：岗位判定与 flags 原地合并（not_match 移入"不匹配"分组）
    await wrapper.findAll("button").find((b) => b!.text().includes("不匹配"))!.trigger("click");
    await flushPromises();
    const rows = wrapper.findAll('[data-testid="job-row"]');
    expect(rows.length).toBeGreaterThan(0);
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("疑似骗局：要求先交培训费");
    vi.unstubAllGlobals();
  });

  it("B054: city chip shows city only and submit has no locations", async () => {
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
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.endsWith("/api/session")) return response({ token: "test" });
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      if (url.endsWith("/api/search-scope/preview")) return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "sha256:b054" }, deduplicated: {} });
      if (url.includes("/api/location-catalog")) return response({ ok: true, platform: "boss", city: "上海", city_code: "101020100", districts: [{ code: "310115", name: "浦东新区", children: [] }] });
      if (url.endsWith("/api/execute-search")) return response({ ok: true, task_id: "b054" });
      if (url.startsWith("/api/task-state/b054")) return response({ status: "completed", progress: {}, logs: [], result: { jobs: [] } });
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
    const cityChip = wrapper.get(".city-chip");
    expect(cityChip.text()).toContain("上海");
    await wrapper.get('[data-testid="city-chip-toggle"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="location-panel"]').exists()).toBe(true);
    await wrapper.get('[data-testid="location-district-310115"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="city-chip-toggle"]').text()).toContain("上海 · 浦东新区");
    await confirmProfile(wrapper, "3年Python后端候选人");
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/execute-search"));
    expect(call).toBeTruthy();
    const body = JSON.parse(String((call as unknown as [unknown, RequestInit | undefined])[1]?.body));
    expect(body.script_params.locations).toEqual(expect.arrayContaining([expect.objectContaining({ district_code: "310115" })]));
    vi.unstubAllGlobals();
  });

  it("B054: removing city removes its chip", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/session")) return response({ token: "test" });
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: { inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2, detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5, screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10 }, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("上海");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".city-chip")).toHaveLength(1);
    await wrapper.get(".city-chip-remove").trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".city-chip")).toHaveLength(0);
    vi.unstubAllGlobals();
  });

  it("B054: restores completed scrape locations and profile after refresh", async () => {
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
    const roundContext = {
      platform: "boss" as const,
      keywords: ["Python"],
      cities: ["上海"],
      locations: [{ platform: "boss" as const, city_name: "上海", district_name: "浦东新区", district_code: "310115" }],
      screening_fields: {},
      profile_summary: "测试画像",
      profile_facts: { years: 3 },
      scrape_task_id: "scrape-done",
      screen_run_id: "",
      status: "completed",
      resumable: false,
      has_frozen_filters: false,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/session")) return response({ token: "test" });
      if (url.includes("/api/latest-running-task")) return response({
        ok: true, has_task: true, task_id: "scrape-done", kind: "scrape", status: "completed",
        platform: "boss", scrape_task_id: "scrape-done", scrape_completed: true, auto_screen: false,
        scraped_count: 1, source_total: 1, profile_summary: "测试画像", profile_facts: { years: 3 },
        round_context: roundContext, progress: { message: "抓取已完成" }, logs: [], error: "",
      });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/scrape-result-save")) return response({
        ok: true, saved: true, run_id: "snap",
        result: { ok: true, jobs: [], dropped: [], total_scraped: 1, total_kept: 1, total_matched: 0, total_dropped: 0, profile_summary: "测试画像", error: "" },
      });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    expect(wrapper.get('[data-testid="city-chip-toggle"]').text()).toContain("上海 · 浦东新区");
    expect((wrapper.find(".profile-summary-input").element as HTMLTextAreaElement).value).toContain("测试画像");
    vi.unstubAllGlobals();
  });

  it("B054: scope preview refreshes on district selection and before submit", async () => {
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
    let previewCalls = 0;
    const executeCalls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.endsWith("/api/session")) return response({ token: "test" });
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      if (url.includes("/api/location-catalog")) return response({ ok: true, platform: "boss", city: "上海", city_code: "101020100", districts: [{ code: "310115", name: "浦东新区", children: [] }] });
      if (url.endsWith("/api/search-scope/preview")) {
        previewCalls += 1;
        return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "fresh-digest" }, deduplicated: {} });
      }
      if (url.endsWith("/api/execute-search")) {
        const body = JSON.parse(String((init as RequestInit | undefined)?.body));
        executeCalls.push(body.scope_digest);
        return response({ ok: true, task_id: "scope-task" });
      }
      if (url.startsWith("/api/task-state/")) return response({ status: "completed", progress: {}, logs: [], error: "", scraped_count: 0 });
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
    const beforeDistrict = previewCalls;
    await wrapper.get('[data-testid="city-chip-toggle"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="location-district-310115"]').trigger("click");
    await flushPromises();
    expect(previewCalls).toBeGreaterThan(beforeDistrict);
    await confirmProfile(wrapper, "3年Python后端候选人");
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(previewCalls).toBeGreaterThan(beforeDistrict + 1);
    expect(executeCalls).toEqual(["fresh-digest"]);
    vi.unstubAllGlobals();
  });

  it("B054: startScrape does not submit stale digest while preview refresh is pending", async () => {
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
    let resolvePreview!: (value: Response) => void;
    const pendingPreview = new Promise<Response>((r) => { resolvePreview = r; });
    let previewCalls = 0;
    const executeCalls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return singleFlowCurrentResponse();
      if (url.endsWith("/api/session")) return response({ token: "test" });
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      if (url.endsWith("/api/search-scope/preview")) {
        previewCalls += 1;
        if (previewCalls === 1) return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "nationwide", cities: [], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "stale-digest" }, deduplicated: {} });
        if (previewCalls === 2) return pendingPreview;
        return response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "fresh-digest" }, deduplicated: {} });
      }
      if (url.endsWith("/api/execute-search")) {
        const body = JSON.parse(String((init as RequestInit | undefined)?.body));
        executeCalls.push(body.scope_digest);
        return response({ ok: true, task_id: "race-task" });
      }
      if (url.startsWith("/api/task-state/")) return response({ status: "completed", progress: {}, logs: [], error: "", scraped_count: 0 });
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
    await confirmProfile(wrapper, "3年Python后端候选人");
    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(executeCalls).toEqual(["fresh-digest"]);
    resolvePreview(response({ ok: true, scope: { keywords: ["Python"], scope_kind: "cities", cities: ["上海"], pages_per_combination: 3, combination_count: 1, planned_pages: 3, task_size: "small", scope_digest: "stale-digest" }, deduplicated: {} }));
    await flushPromises();
    vi.unstubAllGlobals();
  });

  it("B057: paused scrape continue uses the frozen account context", async () => {
    const continueBodies: Array<Record<string, unknown>> = [];
    const fetchMock = oneClickBase({
      "/api/latest-running-task": () => response({
        ok: true, has_task: true, task_id: "paused-scrape", kind: "scrape", status: "paused",
        platform: "boss", scrape_completed: false, progress: {}, logs: [], error: "源账号限流",
      }),
      "/api/task-state/paused-scrape": () => response({ status: "paused", progress: {}, logs: [], error: "源账号限流" }),
      "/api/task/continue/paused-scrape": (url, init) => {
        continueBodies.push(JSON.parse(String((init as RequestInit | undefined)?.body || "{}")));
        return response({ ok: true, task_id: "resumed-scrape" });
      },
      "/api/task-state/resumed-scrape": () => response({ status: "completed", progress: {}, logs: [], error: "", scraped_count: 1 }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    expect(wrapper.find("[data-testid=\"scrape-continue-account\"]").exists()).toBe(false);
    await wrapper.get("[data-testid=\"continue-scrape\"]").trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toContain("/api/task/continue/paused-scrape");
    expect(continueBodies.length).toBe(1);
    expect(continueBodies[0]).toEqual({});
    vi.unstubAllGlobals();
  });

  it.each(["boss", "zhilian"] as const)(
    "screen %s keeps live actions while terminal errors return to the new-task path",
    async (platform) => {
      const statusCases = [
        { status: "running", actionTestId: "pause-ai-screen", occupies: true },
        { status: "paused", actionTestId: "continue-ai-screen", occupies: true },
        { status: "interrupted", actionTestId: "", occupies: false },
        { status: "failed", actionTestId: "", occupies: false },
      ] as const;

      for (const { status, actionTestId, occupies } of statusCases) {
        const runId = `screen-${platform}-${status}`;
        const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
          const url = String(input);
          if (url.includes("/api/latest-running-task")) {
            return response({
              ok: true, has_task: true, task_id: runId, kind: "ai_screen", status,
              platform, scrape_task_id: `scrape-${platform}`, scrape_completed: true,
              frozen_filters: { salary: ["406"] }, profile_summary: "3年Python后端候选人",
              pause_info: { error_code: "source_verification_required", error_reason: "验证码" },
              progress: { message: `AI 筛选${status}` }, logs: [],
            });
          }
          if (url.includes(`/api/task-state/${runId}`)) {
            return response({
              status, progress: { message: `AI 筛选${status}` }, logs: [],
              pause_info: { error_code: "source_verification_required", error_reason: "验证码" },
            });
          }
          if (url === `/api/task/finish/${runId}`) {
            return response({
              ok: true, run_id: runId, snapshot_run_id: `snapshot-${runId}`, platform,
              status: "completed_with_pending", scrape_task_id: `scrape-${platform}`,
              cleanup_error: "browser_cleanup_failed",
              cleanup: { ok: false, error_code: "source_cdp_unavailable" },
              result: { jobs: [], total_scraped: 0, total_kept: 0, total_dropped: 0 },
            });
          }
          if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
          if (url.includes("/api/filter-labels")) {
            return response({
              ok: true, platform, schema_version: 1, enabled_for_new_tasks: true,
              fields: platform === "boss"
                ? [{ key: "stage", label: "融资阶段", multiple: false, options: [{ value: "804", label: "B轮" }] }]
                : [{ key: "company_nature", label: "公司性质", multiple: false, options: [{ value: "1", label: "国企" }] }],
            });
          }
          if (url.includes("/api/options")) return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
          if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
          return response({});
        });
        vi.stubGlobal("fetch", fetchMock);

        const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
        await flushPromises();
        if (occupies) {
          expect(wrapper.find(`[data-testid="${actionTestId}"]`).exists()).toBe(true);
          expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(true);

          await wrapper.get('[data-testid="finish-save-results"]').trigger("click");
          await flushPromises();
          const finishCalls = fetchMock.mock.calls.filter(
            ([url]) => String(url) === `/api/task/finish/${runId}`,
          );
          expect(finishCalls).toHaveLength(1);
          expect(finishCalls[0]?.[1]?.method).toBe("POST");
          expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/task/cancel/"))).toBe(false);
          expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/task/continue/"))).toBe(false);
        } else {
          // failed/interrupted are visible facts, not an occupied task slot.
          // 中断不再给「继续」（后端续跑只收 paused/可续 failed）：按钮根本不再出现；
          // 普通 failed 是终态；既有可恢复失败由 task-state 投影成 paused。
          const continuation = wrapper.find('[data-testid="continue-ai-screen"]');
          expect(continuation.exists()).toBe(false);
          expect(wrapper.find('[data-testid="finish-save-results"]').isVisible()).toBe(false);
          expect(wrapper.find('[data-testid="start-scrape"]').exists()).toBe(true);
          expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("disabled")).toBeUndefined();
        }

        wrapper.unmount();
        sessionStorage.clear();
        localStorage.clear();
        vi.unstubAllGlobals();
      }
    },
  );

  it.each(["boss", "zhilian"] as const)(
    "failed scrape %s shows saved-result plus cleanup failure in the shared view",
    async (platform) => {
      const runId = `scrape-cleanup-${platform}`;
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/latest-running-task")) {
          return response({
            ok: true, has_task: true, task_id: runId, kind: "scrape", status: "failed",
            platform, scraped_count: 1, source_total: 1, progress: { message: "抓取失败" },
            logs: [], error: "列表抓取失败",
            pause_info: { error_code: "source_unreachable", error_reason: "列表抓取失败" },
          });
        }
        if (url === `/api/task/finish/${runId}`) {
          return response({
            ok: true, run_id: runId, snapshot_run_id: `snapshot-${platform}`,
            platform, status: "completed_with_pending", scrape_task_id: runId,
            cleanup_error: "browser_cleanup_failed",
            cleanup: { ok: false, error_code: "source_cdp_unavailable" },
            result: { jobs: [], total_scraped: 1, total_kept: 0, total_dropped: 0 },
          });
        }
        if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
        if (url.includes("/api/filter-labels")) {
          return response(platform === "boss" ? bossSchema() : {
            ok: true, platform, schema_version: 1, enabled_for_new_tasks: true,
            fields: [{ key: "company_nature", label: "公司性质", multiple: false, options: [{ value: "1", label: "国企" }] }],
          });
        }
        if (url.includes("/api/options")) return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
        if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
        return response({});
      });
      vi.stubGlobal("fetch", fetchMock);
      const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
      await flushPromises();

      await wrapper.get('[data-testid="finish-save-results"]').trigger("click");
      await flushPromises();

      expect(wrapper.text()).toContain("结果已保存，但浏览器清理失败");
      expect(wrapper.text()).not.toContain("任务已结束，已完成结果已保存");
      expect(fetchMock.mock.calls.some(([url]) => String(url) === `/api/task/finish/${runId}`)).toBe(true);

      wrapper.unmount();
      sessionStorage.clear();
      localStorage.clear();
      vi.unstubAllGlobals();
    },
  );

  it.each(["boss", "zhilian"] as const)(
    "paused scrape %s can abandon the round while surfacing cleanup failure",
    async (platform) => {
      const runId = `paused-scrape-cleanup-${platform}`;
      const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/latest-running-task")) {
          return response({
            ok: true, has_task: true, task_id: runId, kind: "scrape", status: "paused",
            platform, progress: {}, logs: [], error: "源平台暂停",
            pause_info: { error_code: "source_verification_required", error_reason: "需要人工处理" },
          });
        }
        if (url.includes(`/api/task-state/${runId}`)) {
          return response({
            status: "paused", progress: {}, logs: [], error: "源平台暂停",
            pause_info: { error_code: "source_verification_required", error_reason: "需要人工处理" },
          });
        }
        if (url === `/api/task/cancel/${runId}`) {
          return response({
            ok: false, error: "browser_cleanup_failed",
            cleanup_error: "browser_cleanup_failed",
            cleanup: { ok: false, error_code: "source_cdp_unavailable" }, platform,
            status: "cancelled",
          });
        }
        if (url === "/api/scrape-result-save") {
          return response({
            saved: true,
            run_id: `snapshot-${platform}`,
            result: {
              platform,
              source_run_id: runId,
              jobs: [{ job_id: `${platform}-saved-job`, platform, title: "当前轮岗位" }],
              dropped: [],
              total_scraped: 1,
              total_kept: 1,
              total_matched: 1,
              total_dropped: 0,
            },
          });
        }
        if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
        if (url.includes("/api/filter-labels")) {
          return response(platform === "boss" ? bossSchema() : {
            ok: true, platform, schema_version: 1, enabled_for_new_tasks: true,
            fields: [{ key: "company_nature", label: "公司性质", multiple: false, options: [{ value: "1", label: "国企" }] }],
          });
        }
        if (url.includes("/api/options")) return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
        if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
        return response({});
      });
      vi.stubGlobal("fetch", fetchMock);
      const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
      await flushPromises();

      expect(wrapper.get('[data-testid="cancel-paused-scrape"]').text()).toContain("放弃本轮");
      await wrapper.get('[data-testid="cancel-paused-scrape"]').trigger("click");
      await flushPromises();

      expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/scrape-result-save")).toBe(false);
      expect(wrapper.find('[data-testid="discovery-view"]').classes()).not.toContain("results-view");
      expect(wrapper.find('[data-testid="resume-input"]').exists()).toBe(true);
      expect(wrapper.emitted("notify")?.flat()).toContainEqual(
        expect.objectContaining({ message: "已放弃本轮，但浏览器清理失败" }),
      );
      expect(fetchMock.mock.calls.some(([url]) => String(url) === `/api/task/cancel/${runId}`)).toBe(true);

      wrapper.unmount();
      sessionStorage.clear();
      localStorage.clear();
      vi.unstubAllGlobals();
    },
  );

  describe("按钮矩阵：AI 筛选三态与结果页胶囊", () => {
    const MATRIX_ACTION_IDS = [
      "pause-ai-screen", "continue-ai-screen", "start-ai-screen",
      "finish-save-results", "view-screen-results",
    ];
    function matrixButtonIds(wrapper: ReturnType<typeof mount>) {
      return MATRIX_ACTION_IDS.filter((id) => wrapper.find(`[data-testid="${id}"]`).exists());
    }

    it("运行中：步骤 3 只显示 暂停筛选 + 结束并保存结果", async () => {
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/latest-running-task")) {
          return response({ ok: true, has_task: true, task_id: "matrix-running", kind: "ai_screen", status: "running", platform: "boss", scrape_task_id: "scrape-m", scrape_completed: true, progress: { message: "AI 筛选中" }, logs: [], error: "" });
        }
        if (url.includes("/api/task-state/matrix-running")) return response({ status: "running", progress: { message: "AI 筛选中" }, logs: [] });
        if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
        if (url.includes("/api/filter-labels")) return response(bossSchema());
        if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
        if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
        return response({});
      });
      vi.stubGlobal("fetch", fetchMock);
      const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
      await flushPromises();
      expect(matrixButtonIds(wrapper)).toEqual(["pause-ai-screen", "finish-save-results"]);
      expect(wrapper.find('[data-testid="view-screen-results"]').exists()).toBe(false);
      vi.unstubAllGlobals();
    });

    it.each(["boss", "zhilian"] as const)(
      "步骤 3 的 %s 详情筛选可以放弃本轮并回到上传页",
      async (platform) => {
        const runId = `screen-abandon-${platform}`;
        const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
          const url = String(input);
          if (url.includes("/api/latest-running-task")) {
            return response({
              ok: true, has_task: true, task_id: runId, kind: "ai_screen", status: "running",
              platform, scrape_task_id: `scrape-${platform}`, scrape_completed: true,
              progress: { message: "抓取岗位详情中" }, logs: [],
            });
          }
          if (url.includes(`/api/task-state/${runId}`)) {
            return response({ status: "running", progress: { message: "抓取岗位详情中" }, logs: [] });
          }
          if (url === `/api/task/cancel/${runId}`) return response({ ok: true, status: "cancelled" });
          if (url.endsWith("/api/result-history/archive-latest")) return response({ ok: true, archived_run_ids: [] });
          if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
          if (url.includes("/api/filter-labels")) {
            return response(platform === "boss" ? bossSchema() : {
              ok: true, platform, schema_version: 1, enabled_for_new_tasks: true,
              fields: [{ key: "company_nature", label: "公司性质", multiple: false, options: [{ value: "1", label: "国企" }] }],
            });
          }
          if (url.includes("/api/options")) return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
          if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
          return response({});
        });
        vi.stubGlobal("fetch", fetchMock);

        const wrapper = mount(DiscoveryView, { props: { profileId: `screen-abandon-${platform}` } });
        await flushPromises();

        const abandon = wrapper.get('[data-testid="abandon-screen-round"]');
        expect(abandon.text()).toContain("放弃本轮");
        await abandon.trigger("click");
        await flushPromises();

        expect(fetchMock.mock.calls.some(([url]) => String(url) === `/api/task/cancel/${runId}`)).toBe(true);
        expect(wrapper.find('[data-testid="resume-input"]').exists()).toBe(true);
        expect(wrapper.find('[data-testid="discovery-view"]').classes()).not.toContain("results-view");
        expect(wrapper.emitted("notify")?.flat()).toContainEqual(
          expect.objectContaining({ message: "已放弃本轮，已回到第一步" }),
        );

        wrapper.unmount();
        sessionStorage.clear();
        localStorage.clear();
        vi.unstubAllGlobals();
      },
    );

    it("结束保存后刷新（B078）：完成态自动新一轮，干净 01 页、无上一轮胶囊糊脸", async () => {
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
        if (url.includes("/api/latest-pipeline-result")) {
          if (url.includes("platform=zhilian")) return response({ ok: true, has_result: false });
          return response({
            ok: true, has_result: true, source_run_id: "run-f", platform: "boss",
            status: "completed_with_pending", scrape_task_id: "scrape-f",
            result: {
              jobs: [{ job_id: "p1", platform: "boss", verdict: "uncertain", title: "待确认岗位" }],
              dropped: [], total_scraped: 2, total_kept: 1, total_dropped: 0,
            },
          });
        }
        if (url.endsWith("/api/result-history/archive-latest")) {
          return response({ ok: true, archived_run_ids: [] });
        }
        if (url.endsWith("/api/pipeline/recrawl")) return response({ ok: true, task_id: "recrawl-f" }, 202);
        if (url.includes("/api/task-state/recrawl-f")) return response({ status: "completed", progress: {}, logs: [], result: { updates: {} } });
        if (url.includes("/api/filter-labels")) return response(bossSchema());
        if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
        if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
        return response({});
      });
      vi.stubGlobal("fetch", fetchMock);
      const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
      await flushPromises();
      // 025 B078：结束保存（完成态）后刷新 → 自动「开始新一轮」：
      // 干净 01 页、无上一轮结果/胶囊糊脸（想看结论去历史）。
      expect(wrapper.find('[data-testid="resume-input"]').exists()).toBe(true);
      expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').isVisible()).toBe(false);
      expect(wrapper.find(".results-stage").isVisible()).toBe(false);
      vi.unstubAllGlobals();
    });
  });

  // ---------- B074：重抓胶囊「暂不处理」会话内隐藏，仅新结果重载复位 ----------
  it("B074: dismissed capsule never reappears on platform/tab switches, only on new result epoch", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) {
          return response({
            ok: true, has_result: true, source_run_id: "run-zhilian", platform: "zhilian",
            scrape_task_id: "scrape-z", started_at: 1000,
            result: { jobs: [], total_kept: 0, total_dropped: 0 },
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
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: t513Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text().includes("查看结果"))!.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(true);

    // 点「暂不处理」（隐藏断言由 PendingRecrawlCapsule.spec.ts 正本覆盖）
    await wrapper.get('[data-testid="pending-recrawl-dismiss"]').trigger("click");
    await flushPromises();

    // 切平台（全部→智联→BOSS）→ count 抖动，胶囊不得重弹
    await wrapper.get('[data-testid="result-platform-filter-all"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
    await wrapper.get('[data-testid="result-platform-filter-zhilian"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
    await wrapper.get('[data-testid="result-platform-filter-boss"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);

    // 切页签（匹配→待确认）→ 不重弹
    await wrapper.findAll("button").find((b) => b.text().includes("匹配"))!.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
    await wrapper.findAll("button").find((b) => b.text().includes("待确认"))!.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);

    // 切到 0 待确认平台视图：不重弹（绿色对勾否定断言由正本覆盖）
    await wrapper.get('[data-testid="result-platform-filter-zhilian"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
    await wrapper.get('[data-testid="result-platform-filter-boss"]').trigger("click");
    await flushPromises();

    // 新结果重载（profileId 变化触发 loadLatestResult → resultEpoch 递增）→ 若仍有待确认，胶囊重现
    await wrapper.setProps({ profileId: "profile-2" });
    await flushPromises();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(true);
    vi.unstubAllGlobals();
  });
});
describe("DiscoveryView 完成态自动新一轮（025 B078）", () => {
  const b078Settings = {
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

  function completedHistoryResult() {
    return response({
      ok: true,
      has_result: true,
      source_run_id: "completed-run",
      status: "completed",
      result: {
        jobs: [{ job_id: "old", title: "旧结果" }],
        dropped: [],
        total_kept: 1,
        total_dropped: 0,
        profile_summary: "历史画像",
      },
      started_at: 1_000,
      finished_at: 2_000,
      execution_config: null,
    });
  }

  function baseFetch(overrides: Record<string, unknown> = {}) {
    return vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) {
        return response(overrides.latestTask ?? NO_TASK_PAYLOAD);
      }
      if (url.includes("/api/latest-pipeline-result")) {
        if (overrides.noHistory) return response({ ok: true, has_result: false });
        if (overrides.scrapedOnly) {
          return response({ ok: true, has_result: true, source_run_id: "scrape-run", status: "scraped_only", result: { jobs: [], dropped: [], total_kept: 0, total_dropped: 0 } });
        }
        return completedHistoryResult();
      }
      if (url.endsWith("/api/result-history/archive-latest")) {
        return response({ ok: true, archived_run_ids: [] });
      }
      if (url.includes("/api/filter-labels")) return response({ labels: {} });
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) {
        return response({ ok: true, selection: "balanced", settings: b078Settings, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      }
      return response({});
    });
  }

  afterEach(() => {
    sessionStorage.clear();
    // 026 B078：localStorage 已结束事实也随测试隔离清空
    localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("T027: 完成态启动 A——无活动任务 + 历史轮已完成 → 自动新一轮（干净 01 页、无旧结果残留）", async () => {
    const fetchMock = baseFetch();
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b078-a" } });
    await flushPromises();
    // 01 页上传区可见（自动新一轮后回到干净起点）
    expect(wrapper.find('[data-testid="resume-input"]').exists()).toBe(true);
    // 02 页草稿不出现、旧结果不糊脸
    expect(wrapper.find('[data-testid="custom-keyword"]').isVisible()).toBe(false);
    expect(wrapper.text()).not.toContain("旧结果");
    wrapper.unmount();
  });

  it("T031: 完成态 session 快照残留时也只进入新一轮 01 页", async () => {
    sessionStorage.setItem("career-scout-workflow:profile-b078-g", JSON.stringify({
      version: 1, unfinished: true, activeStep: "results", analysisReady: true,
      scrapeTaskId: "scrape-done", screenTaskId: "screen-done", scrapeCompleted: true,
      scrapeSnapshot: { status: "completed", progress: {}, logs: [] },
      screenSnapshot: { status: "completed", progress: {}, logs: [] },
      pipelineResult: {
        ok: true,
        jobs: [{ job_id: "old-session", title: "旧快照结果" }],
        dropped: [], total_kept: 1, total_dropped: 0,
      },
      pipelineResultRunId: "completed-run", currentRoundStatus: "screened",
      resultLoaded: true, resultsPageSeen: false,
    }));
    const fetchMock = baseFetch();
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b078-g" } });
    await flushPromises();

    expect(wrapper.find('[data-testid="resume-input"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="custom-keyword"]').isVisible()).toBe(false);
    expect(wrapper.text()).not.toContain("旧快照结果");
    wrapper.unmount();
  });

  it("T028: 完成态启动 B——latest-running-task 返回已完成终态任务 → 同样自动新一轮", async () => {
    const fetchMock = baseFetch({
      latestTask: {
        ok: true, has_task: true, task_id: "screen-done", kind: "ai_screen",
        status: "completed", platform: "boss", scrape_task_id: "scrape-done",
        progress: { message: "上次已完成" }, logs: [],
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b078-b" } });
    await flushPromises();
    expect(wrapper.find('[data-testid="resume-input"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="custom-keyword"]').isVisible()).toBe(false);
    expect(wrapper.text()).not.toContain("旧结果");
    wrapper.unmount();
  });

  it("T029: 无历史轮（全新用户）→ 不误触发、保持干净 01 页", async () => {
    const fetchMock = baseFetch({ noHistory: true });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b078-c" } });
    await flushPromises();
    expect(wrapper.find('[data-testid="resume-input"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="custom-keyword"]').isVisible()).toBe(false);
    wrapper.unmount();
  });

  it("T030: 未完成态——running 任务 → 不自动新一轮、恢复现场（B068 不变）", async () => {
    const fetchMock = baseFetch({
      latestTask: {
        ok: true, has_task: true, task_id: "screen-live", kind: "ai_screen",
        status: "running", platform: "boss", scrape_task_id: "scrape-live",
        scrape_completed: true, frozen_filters: { salary: ["406"] },
        progress: { stage: "fetch_jd", message: "AI 筛选中" }, logs: [],
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b078-d" } });
    await flushPromises();
    // 恢复现场：03 页筛选区出现，且不触发新一轮（01 页不回归为主视图）
    expect(wrapper.find('[data-testid="resume-input"]').isVisible()).toBe(false);
    wrapper.unmount();
  });

  it("035: 运行中任务 + 快照 activeStep=results → 刷新恢复不写「已结束」标记（B086 刷新路径）", async () => {
    // 构造运行中任务的未完成快照：恢复时 activeStep 会被还原为 results
    sessionStorage.setItem(
      "career-scout-workflow:profile-b078-f",
      JSON.stringify({
        version: 1, unfinished: true, activeStep: "results",
        analysisReady: true, screenTaskId: "screen-live",
        screenSnapshot: { status: "running", progress: {}, logs: [] },
        scrapeCompleted: true, resultLoaded: false, resultsPageSeen: false,
      }),
    );
    const fetchMock = baseFetch({
      latestTask: {
        ok: true, has_task: true, task_id: "screen-live", kind: "ai_screen",
        status: "running", platform: "boss", scrape_task_id: "scrape-live",
        scrape_completed: true, progress: { stage: "fetch_jd", message: "AI 筛选中" }, logs: [],
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b078-f" } });
    await flushPromises();
    // 运行中任务存在：不得因恢复 activeStep=results 而写「已结束」标记（否则再次刷新会取消任务）
    expect(localStorage.getItem("career-scout-workflow:profile-b078-f:finished")).toBeNull();
    wrapper.unmount();
  });

  it("T030b: 未完成态——paused 任务 → 恢复现场可继续，不自动新一轮", async () => {
    const fetchMock = baseFetch({
      latestTask: {
        ok: true, has_task: true, task_id: "screen-paused", kind: "ai_screen",
        status: "paused", platform: "boss", scrape_task_id: "scrape-paused",
        scrape_completed: true,
        pause_info: { error_code: "user_paused", error_reason: "用户已暂停" },
        progress: { stage: "ai_fine", message: "已暂停" }, logs: [],
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b078-e" } });
    await flushPromises();
    // 恢复现场：不回归 01 页上传
    expect(wrapper.find('[data-testid="resume-input"]').isVisible()).toBe(false);
    wrapper.unmount();
  });

  it("T005: 026 B078——已进 04 页（已结束）+ 后端残留 interrupted → 只显示 01 页、无被中断提示", async () => {
    // 已结束事实独立持久化（localStorage）：上次进过 04 页
    localStorage.clear();
    localStorage.setItem(
      "career-scout-workflow:profile-b078-026:finished",
      JSON.stringify({ resultsPageSeen: true, finishedPartial: false }),
    );
    const fetchMock = baseFetch({
      latestTask: {
        ok: true, has_task: true, task_id: "interrupted-026", kind: "ai_screen",
        status: "interrupted", platform: "boss", scrape_task_id: "scrape-026",
        scrape_completed: true, frozen_filters: { salary: ["406"] },
        profile_summary: "3年Python后端",
        progress: { stage: "ai_fine", message: "已中断" }, logs: [],
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-b078-026" } });
    await flushPromises();
    // 01 页上传区可见（自动新一轮回到干净起点）
    expect(wrapper.find('[data-testid="resume-input"]').exists()).toBe(true);
    // 02 页不出现、无"服务重启被中断"提示、无恢复横幅
    expect(wrapper.find('[data-testid="custom-keyword"]').isVisible()).toBe(false);
    expect(wrapper.text()).not.toContain("服务重启被中断");
    expect(wrapper.find(".restore-banner").exists()).toBe(false);
    wrapper.unmount();
    localStorage.clear();
  });
});

// 035 界面收口（重拆版）：三个真机问题的界面级验收——测试以「用户看到的界面」为准。
describe("DiscoveryView 035 界面收口（US1/US2 界面级）", () => {
  const settings035 = {
    inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
    detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
    screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
  };

  function bossSchema035() {
    return {
      ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true,
      fields: [{ key: "stage", label: "融资阶段", multiple: false, options: [{ value: "804", label: "B轮" }] }],
    };
  }

  function historyItem035(runId: string) {
    return {
      run_id: runId, platform: "boss", status: "done",
      created_at: "2026-09-01 10:00:00",
      total_scraped: 10, total_kept: 1, total_matched: 1, mismatch_count: 0,
      total_dropped: 9, pending_count: 0,
      keyword_summary: "Python 后端 / 上海", profile_summary_preview: "3年Python后端",
      archived_at: null, is_latest: false,
    };
  }

  function historyDetail035(runId: string) {
    return {
      ok: true, has_result: true, source_run_id: runId, platform: "boss", status: "done",
      started_at: 1_720_000_000_000, finished_at: 1_720_000_036_000,
      result: {
        jobs: [{ job_id: "j1", platform: "boss", verdict: "match", title: "历史岗位" }],
        dropped: [], total_kept: 1, total_dropped: 9,
        profile_summary: "完整画像文本",
      },
    };
  }

  // T006（真机问题①，FR-010）：抓取中刷新恢复 → 03 页不残留旧一轮 AI 筛选内容。
  it("035 T006: 抓取运行中刷新恢复 → 03 页无旧一轮筛选内容（真机问题①）", async () => {
    sessionStorage.setItem("career-scout-workflow:profile-035-t006", JSON.stringify({
      version: 1, unfinished: true, activeStep: "search", analysisReady: true,
      keywords: [{ word: "Python", recommended: true }], selectedKeywords: ["Python"], cityText: "上海",
      filterValues: { boss: {}, zhilian: {} }, profileSummary: "3年Python", profileFacts: {},
      scrapeTaskId: "scrape-035-t006", screenTaskId: "screen-old", scrapeCompleted: true,
      scrapeSnapshot: { status: "completed", progress: {}, logs: [] },
      screenSnapshot: { status: "completed", progress: { message: "旧一轮 AI 筛选完成" }, logs: [], total: 42, kept_count: 20, dropped_count: 22 },
      resultLoaded: false, resultsPageSeen: false,
    }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response({
        ok: true, has_task: true, task_id: "scrape-035-t006", kind: "scrape",
        status: "running", platform: "boss", progress: { message: "正在抓取" }, logs: [],
      });
      if (url.includes("/api/task-state/scrape-035-t006")) return response({
        status: "running", progress: { message: "正在抓取" }, logs: [],
      });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) return response(bossSchema035());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: settings035, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-035-t006" } });
    await flushPromises();

    // 02 页显示抓取运行中的真实进度
    expect(wrapper.find('[data-testid="start-scrape"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="pause-scrape"]').text()).toContain("暂停");
    // 进入 03 页（scrapeCompleted 由旧快照带入 true，步骤可达——真机踩中路径）
    await wrapper.get('[data-testid="continue-to-screen"]').trigger("click");
    await flushPromises();
    // 03 页不得渲染旧一轮筛选进度卡与旧轮计数/文案
    expect(wrapper.find('[data-testid="task-progress-announcement"]').isVisible()).toBe(false);
    expect(wrapper.text()).not.toContain("旧一轮 AI 筛选完成");

    wrapper.unmount();
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  // T012（真机问题②，FR-011）：后台抓取运行中 + 历史模式 04 页点「开始新一轮」
  // → 跳回 02 抓取进度页，任务未被取消、未开新一轮。
  it("035 T012: 历史模式 04 页点「开始新一轮」→ 跳回 02、任务未被取消（真机问题②）", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/latest-running-task")) return response({
        ok: true, has_task: true, task_id: "scrape-035-t012", kind: "scrape",
        status: "running", platform: "boss", progress: { message: "正在抓取" }, logs: [],
      });
      if (url.includes("/api/task-state/scrape-035-t012")) return response({
        status: "running", progress: { message: "正在抓取" }, logs: [],
      });
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if ((url.includes("/api/result-history?") || url.endsWith("/api/result-history"))) return response({ ok: true, items: [historyItem035("h1")] });
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1"))) return response(historyDetail035("h1"));
      if (url.includes("/api/filter-labels")) return response(bossSchema035());
      if (url.includes("/api/options")) return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      if (url.endsWith("/api/advanced-settings")) return response({ ok: true, selection: "balanced", settings: settings035, last_custom: null, mode_version: null, manual_ranges: {}, config_schema_version: 1 });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-035-t012" } });
    await flushPromises();

    // 后台抓取运行中（02 页为当前进度页）
    expect(wrapper.find('[data-testid="start-scrape"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="pause-scrape"]').text()).toContain("暂停");

    // 进历史模式，查看旧一轮 04 页
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(true);

    // 在历史模式 04 页点「开始新一轮」
    await wrapper.findAll("button").find((b) => b.text().includes("开始新一轮"))!.trigger("click");
    await flushPromises();

    // 跳回 02 抓取进度页（真机问题②验收），并退出历史模式
    expect(wrapper.find('[data-testid="start-scrape"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="pause-scrape"]').text()).toContain("暂停");
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(false);
    // 任务未被取消、未开新一轮、未归档最新结果
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/task/cancel"))).toBe(false);
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/execute-search"))).toBe(false);
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/result-history/archive-latest"))).toBe(false);

    wrapper.unmount();
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });
});

// ---------------------------------------------------------------------------
// SPEC 046 Edge Cases：任一平台被系统禁用新建任务时，「全部」仍阻止启动并指出
// 不可用平台；可用单平台仍可单独启动。
// 缺陷现场：前端只看草稿平台的 schema（选了「全部」时非草稿平台的禁用看不见），
// 用户点完启动、等后端 503 才知道，而且提示语把内部平台码直接吐在脸上。
// 可用性事实取既有的平台 schema 投影，显示名取平台显示名投影，树干不写平台名。
// ---------------------------------------------------------------------------
describe("DiscoveryView 「全部」启动前的平台可用性门禁（046 Edge Cases）", () => {
  const gateSettings = {
    inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
    detail_reset_every: 4, detail_batch_cooldown: 5, detail_tab_pool_size: 5,
    screen_batch_size: 50, screen_concurrency: 5, match_batch_size: 10, match_concurrency: 10,
  };

  function platformSchema(platform: string, enabled: boolean) {
    return {
      ok: true, platform, schema_version: 1, enabled_for_new_tasks: enabled,
      fields: [{
        key: "salary", label: "薪资范围", multiple: false,
        options: [{ value: "0", label: "不限" }, { value: "406", label: "10-20K" }],
      }],
    };
  }

  function gateFetch(options: { disabled: string[] }) {
    return vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return response({ ok: true, flow: null });
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) {
        const platform = url.includes("platform=zhilian") ? "zhilian" : "boss";
        return response(platformSchema(platform, !options.disabled.includes(platform)));
      }
      if (url.includes("/api/options")) {
        const platform = url.includes("platform=zhilian") ? "zhilian" : "boss";
        return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings: gateSettings, last_custom: null,
          mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      if (url.endsWith("/api/analyze-resume")) {
        return response({ ok: true, fields: {}, platform: "boss", filter_schema_version: 1 });
      }
      if (url.includes("/api/flows")) return response({ ok: true, flow_id: "must-not-start", flow: null });
      return response({});
    });
  }

  async function mountOnSearchStep(disabled: string[]) {
    const fetchMock = gateFetch({ disabled });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-gate" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("跳过简历"))!.trigger("click");
    await flushPromises();
    return { fetchMock, wrapper };
  }

  it("非草稿平台被禁用时，「全部」的主启动在提交前就挡住并点名", async () => {
    const { fetchMock, wrapper } = await mountOnSearchStep(["zhilian"]);

    // 默认选中「全部」，草稿平台仍是可用平台：旧写法只看草稿平台，这里必须已经挡住。
    expect(wrapper.get('[data-testid="platform-segment-all"]').attributes("aria-selected")).toBe("true");
    const notice = wrapper.get('[data-testid="parallel-platform-disabled-notice"]');
    expect(notice.text()).toContain("智联");
    expect(notice.text()).not.toContain("zhilian");
    expect(wrapper.get('[data-testid="start-one-click"]').attributes("disabled")).toBeDefined();

    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/flows"))).toBe(false);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("平台都可用时不设门禁，「全部」照常启动", async () => {
    const { wrapper } = await mountOnSearchStep([]);

    expect(wrapper.find('[data-testid="parallel-platform-disabled-notice"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="start-one-click"]').attributes("disabled")).toBeUndefined();
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("禁用另一平台时，可用平台仍可单独启动", async () => {
    const { wrapper } = await mountOnSearchStep(["zhilian"]);

    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="parallel-platform-disabled-notice"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="platform-disabled-notice"]').exists()).toBe(false);
    const scrapeButton = wrapper.get('[data-testid="start-scrape"]');
    expect(scrapeButton.attributes("disabled")).toBeUndefined();
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("禁用当前草稿平台时仍按既有单平台口径提示，不重复两条", async () => {
    const { wrapper } = await mountOnSearchStep(["boss"]);

    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="platform-disabled-notice"]').text()).toContain("BOSS");
    // 「全部」侧的门禁此时不适用（不在全部模式），只留单平台那一条口径。
    expect(wrapper.find('[data-testid="parallel-platform-disabled-notice"]').exists()).toBe(false);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });
});

// ---------------------------------------------------------------------------
// SPEC 046 D-07：本轮范围锁定时，03 页卡片汇总与界面自相矛盾。
// 七类条件因锁定全部置灰，芯片按空草稿把「不限 / 全部」点亮，右上角汇总却写
// 「未设置筛选条件」。本轮条件的唯一事实源是轨道的 confirmed_filters_snapshot，
// 汇总必须读它并如实说「已按本轮确认条件锁定，当前只读」。
// ---------------------------------------------------------------------------
describe("DiscoveryView 03 页锁定轮次的条件汇总（046 D-07）", () => {
  function lockedFlowFetch(flow: Record<string, unknown> | null) {
    return vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return response({ ok: true, flow });
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) {
        const platform = url.includes("platform=zhilian") ? "zhilian" : "boss";
        return response({
          ok: true, platform, schema_version: 1, enabled_for_new_tasks: true,
          fields: [{
            key: "salary", label: "薪资范围", multiple: false,
            options: [{ value: "0", label: "不限" }, { value: "406", label: "10-20K" }],
          }],
        });
      }
      if (url.includes("/api/options")) {
        const platform = url.includes("platform=zhilian") ? "zhilian" : "boss";
        return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings: {}, last_custom: null,
          mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      return response({});
    });
  }

  const frozenSnapshot = {
    snapshotVersion: 2,
    mappingVersion: "b096-v2-locked",
    unifiedValues: { salary: ["10k-20k"] },
    platformValues: { boss: { salary: ["406"] }, zhilian: { salary: ["10-20K"] } },
  };

  it("锁定轮次：汇总读本轮冻结快照，不再说「未设置筛选条件」", async () => {
    const fetchMock = lockedFlowFetch({
      id: "flow-locked-round",
      profile_id: "profile-locked-round",
      selection: "all",
      status: "running",
      tracks: [
        { id: "t-b", flow_id: "flow-locked-round", platform: "boss", status: "running", stage: "ai", confirmed_filters_snapshot: frozenSnapshot },
        { id: "t-z", flow_id: "flow-locked-round", platform: "zhilian", status: "queued", stage: "scrape", confirmed_filters_snapshot: frozenSnapshot },
      ],
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-locked-round" } });
    await flushPromises();

    const summary = wrapper.get('[data-testid="screen-summary-locked"]');
    expect(summary.text()).toContain("已按本轮确认条件锁定");
    expect(summary.text()).toContain("只读");
    expect(wrapper.text()).not.toContain("未设置筛选条件");
    // 冻结快照的原始 JSON、字段码与映射版本不得整坨搬到汇总上。
    expect(summary.text()).not.toContain("snapshotVersion");
    expect(summary.text()).not.toContain("b096-v2-locked");
    expect(summary.text()).not.toContain("salary");

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("没有冻结快照的新轮次仍按草稿如实显示，不被锁定文案带跑", async () => {
    const fetchMock = lockedFlowFetch(null);
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-unlocked-round" } });
    await flushPromises();

    expect(wrapper.find('[data-testid="screen-summary-locked"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("未设置筛选条件");

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // D-07 的另一半：文案写了「当前只读」，界面就必须真的锁住。锁定事实只有一份
  // （useDiscoveryFlowCoordinator 的 roundConditionLocked），汇总、卡片与芯片都读它。
  it("锁定轮：汇总说出该轮快照里的实际条件，芯片与卡片按同一份锁定事实置灰", async () => {
    const lockedBothPlatforms = {
      snapshotVersion: 2,
      mappingVersion: "b096-v2-locked",
      unifiedValues: { salary: ["10K-20K"] },
      platformValues: { boss: { salary: ["406"] }, zhilian: { salary: ["406"] } },
    };
    const fetchMock = lockedFlowFetch({
      id: "flow-locked-values",
      profile_id: "profile-locked-values",
      selection: "all",
      status: "running",
      tracks: [
        { id: "t-b", flow_id: "flow-locked-values", platform: "boss", status: "running", stage: "ai", confirmed_filters_snapshot: lockedBothPlatforms },
        { id: "t-z", flow_id: "flow-locked-values", platform: "zhilian", status: "queued", stage: "scrape", confirmed_filters_snapshot: lockedBothPlatforms },
      ],
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-locked-values" } });
    await flushPromises();

    // 汇总报的是这一轮冻结的条件本身，不是「有快照」这件事。
    const summary = wrapper.get('[data-testid="screen-summary-locked"]');
    expect(summary.text()).toContain("已按本轮确认条件锁定");
    expect(summary.text()).toContain("薪资范围");
    expect(summary.text()).toContain("10-20K");

    // 同一份锁定事实：03 卡自己标出锁定，芯片全部点不动。
    const card = wrapper.get('[data-testid="screen-condition-card"]');
    expect(card.attributes("data-locked")).toBe("true");
    const chips = wrapper.findAll(".choice-chip");
    expect(chips.length).toBeGreaterThan(0);
    expect(chips.every((chip) => chip.attributes("disabled") !== undefined)).toBe(true);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("没有锁定事实时，芯片照常可点，锁定标记不外溢", async () => {
    const fetchMock = lockedFlowFetch(null);
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-unlocked-chips" } });
    await flushPromises();

    expect(wrapper.get('[data-testid="screen-condition-card"]').attributes("data-locked")).toBeUndefined();
    const chips = wrapper.findAll(".choice-chip");
    expect(chips.length).toBeGreaterThan(0);
    expect(chips.every((chip) => chip.attributes("disabled") === undefined)).toBe(true);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // 锁定事实只许有一份：卡片汇总、芯片置灰与锁定标记都读同一个 computed。
  it("锁定判定与汇总同出一份事实，界面不再各自拼一套", () => {
    const view = readFileSync(path.join(__dirname, "../../views/DiscoveryView.vue"), "utf8");
    const coordinator = readFileSync(
      path.join(__dirname, "../../composables/useDiscoveryFlowCoordinator.ts"), "utf8",
    );
    expect(coordinator).toMatch(/const roundConditionLocked = computed/);
    // 汇总自己不再判一遍：它读的是同一份锁定事实。
    expect(coordinator).toMatch(/if \(!roundConditionLocked\.value\) return ""/);
    // 两组芯片（哨兵芯片 + 档位芯片）都按同一份锁定事实置灰，没有第三份判定。
    expect((view.match(/:disabled="Boolean\([^"]*roundConditionLocked[^"]*"/g) ?? []).length).toBe(2);
    expect((view.match(/roundConditionLocked/g) ?? []).length).toBeGreaterThanOrEqual(4);
  });
});

// ---------------------------------------------------------------------------
// SPEC 046 D-08：04 页在整轮未完成时开放是分轨合流的设计如此，缺陷只是没说清。
// 用户会先把领先平台的结果读成"整轮筛完了"。补一句口径明确的说明：本轮仍在进行、
// 当前只含某一条平台线的结果；落后平台完成时原地加入。判定取既有 Flow 投影与
// 状态词表的唯一谓词，页面不自算一套活体判定。
// ---------------------------------------------------------------------------
describe("DiscoveryView 04 页「本轮仍在进行」说明（046 D-08）", () => {
  function partialRoundFetch(status: string, tracks: Array<Record<string, unknown>>) {
    return vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) {
        return response({
          ok: true,
          flow: {
            id: "flow-partial-round", profile_id: "profile-partial-round", selection: "all", status, tracks,
          },
        });
      }
      if (url.includes("/api/flows/flow-partial-round/results")) {
        return response({ ok: true, results: { flow_id: "flow-partial-round", selection: "all", status, tracks } });
      }
      if (url.includes("/api/latest-running-task")) return response(NO_TASK_PAYLOAD);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.includes("/api/filter-labels")) {
        const platform = url.includes("platform=zhilian") ? "zhilian" : "boss";
        return response({
          ok: true, platform, schema_version: 1, enabled_for_new_tasks: true,
          fields: [{ key: "salary", label: "薪资范围", multiple: false, options: [{ value: "0", label: "不限" }] }],
        });
      }
      if (url.includes("/api/options")) {
        const platform = url.includes("platform=zhilian") ? "zhilian" : "boss";
        return response({ ok: true, platform, city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true, selection: "balanced", settings: {}, last_custom: null,
          mode_version: null, manual_ranges: {}, config_schema_version: 1,
        });
      }
      return response({});
    });
  }

  const bossDelivered = { id: "p-b", platform: "boss", status: "done", stage: "complete", result_run_id: "p-b-result" };
  const zhilianCatchingUp = { id: "p-z", platform: "zhilian", status: "running", stage: "scrape", result_run_id: null };

  it("领先平台已出结果、落后平台仍在跑时，04 页说明本轮仍在进行", async () => {
    const fetchMock = partialRoundFetch("running", [bossDelivered, zhilianCatchingUp]);
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-partial-round" } });
    await flushPromises();
    const resultsStep = wrapper.findAll(".step-nav button").find((button) => button.text().includes("查看结果"));
    await resultsStep!.trigger("click");
    await flushPromises();

    const notice = wrapper.get('[data-testid="flow-round-progress-notice"]');
    expect(notice.text()).toContain("本轮仍在进行");
    expect(notice.text()).toContain("BOSS");
    expect(notice.text()).toContain("智联");
    expect(notice.text()).toContain("原地加入");
    // 面向用户的文案只走平台显示名投影，不回吐内部码。
    expect(notice.text()).not.toContain("zhilian");
    expect(notice.text()).not.toContain("boss");
    // 与装饰性提示不同类：窄屏隐藏装饰时不能把这句话说没。
    expect(notice.classes()).not.toContain("command-note");

    const css = readFileSync(path.join(__dirname, "../../styles.css"), "utf8");
    const narrow = mediaBlocks(css, "max-width: 760px");
    for (const className of notice.classes()) {
      expect(narrow).not.toMatch(new RegExp(`\\.${className}\\s*\\{[^}]*display:\\s*none`));
    }
    expect(css).toMatch(/\.flow-round-progress-notice\s*\{/);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("两条线都已出结果时不再说本轮仍在进行", async () => {
    const fetchMock = partialRoundFetch("done", [
      bossDelivered,
      { id: "p-z2", platform: "zhilian", status: "done", stage: "complete", result_run_id: "p-z2-result" },
    ]);
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-full-round" } });
    await flushPromises();
    const resultsStep = wrapper.findAll(".step-nav button").find((button) => button.text().includes("查看结果"));
    await resultsStep!.trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="flow-round-progress-notice"]').exists()).toBe(false);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("还没有任何结果到达时不承诺原地加入", async () => {
    const fetchMock = partialRoundFetch("running", [
      { id: "p-b2", platform: "boss", status: "running", stage: "scrape", result_run_id: null },
      zhilianCatchingUp,
    ]);
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-no-result" } });
    await flushPromises();

    expect(wrapper.find('[data-testid="flow-round-progress-notice"]').exists()).toBe(false);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // D-08 的另一半：04 页的「本轮仍在进行」必须与「04 能不能进」读同一份事实。
  // 一条线 AI 失败却已持久化部分岗位时，页面按分轨合流规则已经开放（contracts
  // /flow-presentation.md 第 2、6 节），说明就必须跟着一起出现，否则用户把
  // 「只有半条线的结果」读成「整轮筛完了」。
  it("一条线失败带部分岗位、另一条仍在跑时，04 页同样说明本轮仍在进行", async () => {
    const fetchMock = partialRoundFetch("running", [
      {
        id: "p-b3", platform: "boss", status: "failed", stage: "ai", result_run_id: null,
        jobs: [{ job_id: "job-1", platform: "boss", title: "Python" }],
        unfinished_ai_screening: true,
      },
      { id: "p-z3", platform: "zhilian", status: "running", stage: "scrape", result_run_id: null },
    ]);
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-failed-partial-round" } });
    await flushPromises();
    const resultsStep = wrapper.findAll(".step-nav button").find((button) => button.text().includes("查看结果"));
    await resultsStep!.trigger("click");
    await flushPromises();

    const notice = wrapper.get('[data-testid="flow-round-progress-notice"]');
    expect(notice.text()).toContain("本轮仍在进行");
    expect(notice.text()).toContain("BOSS");
    expect(notice.text()).toContain("智联");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  // 同源门禁：04 的解锁判定与这句说明必须用同一个「这条线有没有结果」的谓词，
  // 页面这一层不许再自己拼一份 result_run_id 判定。
  it("04 解锁判定与本轮说明读同一份「这条线有没有结果」的谓词", () => {
    const presentation = readFileSync(
      path.join(__dirname, "../../composables/useDiscoveryFlowPresentation.ts"), "utf8",
    );
    const coordinator = readFileSync(
      path.join(__dirname, "../../composables/useDiscoveryFlowCoordinator.ts"), "utf8",
    );
    expect(presentation).toMatch(/const hadResult = tracks\.value\.some\(\(track\) => trackHasDeliveredResult\(track\)\)/);
    // 说明读的是呈现层同一份轨道投影，且不再自己判 result_run_id。
    expect(coordinator).toMatch(/flowPresentation\.flowTracks\.value/);
    expect(coordinator).toMatch(/trackHasDeliveredResult/);
    expect(coordinator).not.toMatch(/filter\(\(track\) => String\(track\.result_run_id/);
  });
});
