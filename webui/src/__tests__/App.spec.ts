import { flushPromises, mount } from "@vue/test-utils";
import App from "../App.vue";
import DiscoveryView from "../views/DiscoveryView.vue";
import { expectedBackendBuildHash, resetSessionStateForTests } from "../api";
import { toggleTheme } from "../composables/useTheme";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("App", () => {
  beforeEach(() => {
    resetSessionStateForTests();
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/session")) {
        return response({ status: "ok", build_hash: expectedBackendBuildHash });
      }
      if (url.endsWith("/api/version")) {
        return response({
          backend_version: "010",
          build_hash: expectedBackendBuildHash,
          build_time: "now",
        });
      }
      if (url.endsWith("/api/check")) return response({ connected: true });
      if (url.endsWith("/api/profiles")) return response({ profiles: [] });
      if (url.endsWith("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url.endsWith("/api/ai-settings")) {
        return response({ endpoint_url: "", model: "", masked_key: "" });
      }
      return response({});
    }));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps AI settings reachable", async () => {
    const wrapper = mount(App);
    await flushPromises();

    await wrapper.get('[data-testid="settings-trigger"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="ai-settings-trigger"]').trigger("click");
    const dialog = wrapper.get('[role="dialog"]');
    expect(dialog.attributes("aria-modal")).toBe("true");

    await dialog.trigger("keydown", { key: "Escape" });
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  });

  it("keeps browser account manager reachable", async () => {
    const wrapper = mount(App);
    await flushPromises();

    await wrapper.get('[data-testid="settings-trigger"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="browser-accounts-trigger"]').trigger("click");
    const dialog = wrapper.get('[role="dialog"]');
    expect(dialog.text()).toContain("账号");
    expect(dialog.text()).toContain("每个账号使用独立的浏览器环境");

    await dialog.trigger("keydown", { key: "Escape" });
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  });

  it("updates the document title by platform and page state", async () => {
    document.title = "";
    const wrapper = mount(App);
    await flushPromises();
    expect(document.title).toBe("Career Scout 工作台");

    wrapper.findComponent(DiscoveryView).vm.$emit("round-status", { platform: "boss", phase: "scraping", judged: 0 });
    await flushPromises();
    expect(document.title).toBe("Career Scout · BOSS工作台");

    wrapper.findComponent(DiscoveryView).vm.$emit("round-status", { platform: "zhilian", phase: "screening", judged: 0 });
    await flushPromises();
    expect(document.title).toBe("Career Scout · 智联工作台");

    wrapper.findComponent(DiscoveryView).vm.$emit("round-status", { platform: "zhilian", phase: "judged", judged: 3 });
    await flushPromises();
    expect(document.title).toBe("Career Scout · 职位工作台");
    expect(document.title).not.toContain("BOSS 工作台");
  });

  it("B035: aligns judged platform name and count with the view scope", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const pillText = () => wrapper.get('[data-testid="round-status-pill"]').text();
    const view = wrapper.findComponent(DiscoveryView);

    view.vm.$emit("round-status", { platform: "boss", phase: "judged", judged: 5, scope: "all" });
    await flushPromises();
    expect(pillText()).toContain("全部");
    expect(pillText()).toContain("5 个岗位已判定");
    expect(pillText()).not.toContain("BOSS · 5");

    view.vm.$emit("round-status", { platform: "boss", phase: "judged", judged: 2, scope: "boss" });
    await flushPromises();
    expect(pillText()).toContain("BOSS · 2 个岗位已判定");

    view.vm.$emit("round-status", { platform: "zhilian", phase: "judged", judged: 3, scope: "history" });
    await flushPromises();
    expect(pillText()).toContain("历史轮次");
    expect(pillText()).toContain("智联 · 3 个岗位已判定");

    // B038：未筛选轮顶栏显示"已抓取 N 个岗位"，不显示"已判定 N"。
    view.vm.$emit("round-status", { platform: "boss", phase: "scraped", judged: 4, scope: "boss" });
    await flushPromises();
    expect(pillText()).toContain("已抓取 4 个岗位");
    expect(pillText()).not.toContain("已判定");
  });

  it("opens the history drawer from the top bar", async () => {
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.get('[data-testid="history-trigger"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="history-drawer"]').exists()).toBe(true);
  });

  it("keeps history and favorites drawers mutually exclusive", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const historyTrigger = wrapper.get('[data-testid="history-trigger"]');
    // 历史抽屉状态是模块级单例，前面用例可能已把它打开；先确保从关闭态开始。
    if (wrapper.find('[data-testid="history-drawer"]').exists()) {
      await historyTrigger.trigger("click");
      await flushPromises();
    }
    await wrapper.get('[data-testid="history-trigger"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="history-drawer"]').exists()).toBe(true);

    await wrapper.get('.favorites-trigger').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="history-drawer"]').exists()).toBe(false);
    expect(wrapper.find('[role="dialog"][aria-label="我的收藏"]').exists()).toBe(true);
  });

  it("shows a persistent inline notice when the model list is empty", async () => {
    vi.useFakeTimers();
    const wrapper = mount(App);
    await flushPromises();

    await wrapper.get('[data-testid="settings-trigger"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="ai-settings-trigger"]').trigger("click");
    await flushPromises();
    await wrapper.get('[aria-label="拉取可用模型"]').trigger("click");
    await flushPromises();

    // 模型列表为空时，提示以内联 .ai-local-notice 就地展示（非全局 .notice-bar）
    const notice = wrapper.get('.ai-local-notice');
    expect(notice.text()).toContain("服务未返回模型列表");

    // 内联提示不自动消失：推进 5s 仍在
    vi.advanceTimersByTime(5_000);
    await flushPromises();
    expect(wrapper.find('.ai-local-notice').exists()).toBe(true);

    // 关闭对话框后清空
    await wrapper.get('[aria-label="关闭AI 设置"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('.ai-local-notice').exists()).toBe(false);
    wrapper.unmount();
  });

  // ---------- Task 009：顶部提醒入口与 App 持有提醒状态 ----------

  function reminderItemFixture(overrides: Record<string, unknown> = {}) {
    return {
      job_id: "internal-1",
      platform: "zhilian",
      platform_job_id: "z-1",
      title: "Python 后端工程师",
      company: "示例公司",
      salary: "20-30K",
      location: "上海",
      canonical_url: "https://www.zhaopin.com/jobdetail/z-1.htm",
      status: "applied",
      applied_at: "2026-05-01T02:00:00+00:00",
      last_follow_up_at: null,
      baseline_at: "2026-05-01T02:00:00+00:00",
      elapsed_seconds: 8294400,
      elapsed_days: 96,
      can_open: true,
      ...overrides,
    };
  }

  function baseAppResponse(url: string): Response | null {
    if (url.endsWith("/api/session")) {
      return response({ status: "ok", build_hash: expectedBackendBuildHash });
    }
    if (url.endsWith("/api/version")) {
      return response({
        backend_version: "010",
        build_hash: expectedBackendBuildHash,
        build_time: "now",
      });
    }
    if (url.endsWith("/api/check")) return response({ connected: true });
    if (url.endsWith("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
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
    if (url.endsWith("/api/ai-settings")) {
      return response({ endpoint_url: "", model: "", masked_key: "" });
    }
    return null;
  }

  it("shows the reminder badge from server count with an accessible total", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        // 只按当前 profile 查询；不发送 platform 过滤（FR-016/FR-026）。
        expect(url).toContain("profile_id=p1");
        expect(url).not.toContain("platform=");
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 3 });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();

    const trigger = wrapper.get('[data-testid="reminder-trigger"]');
    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("3");
    expect(trigger.attributes("aria-label")).toContain("查看投递提醒");
    expect(trigger.attributes("aria-label")).toContain("3");
  });

  it("hides the reminder badge when the server total is 0", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 0 });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();

    expect(wrapper.find('[data-testid="reminder-badge"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="reminder-trigger"]').attributes("aria-label")).toBe("查看投递提醒");
  });

  it("renders 99+ for totals at or above 100 while keeping the real total accessible", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 137 });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();

    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("99+");
    const label = wrapper.get('[data-testid="reminder-trigger"]').attributes("aria-label") || "";
    expect(label).toContain("137");
  });

  it("does not auto-clear reminders when the drawer is opened and closed", async () => {
    let countCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        countCalls += 1;
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 2 });
      }
      if (url.includes("/api/job-reminders")) {
        return response({
          ok: true, profile_id: "p1", threshold_hours: 720, total: 2,
          items: [reminderItemFixture(), reminderItemFixture({ job_id: "internal-2", platform_job_id: "z-2", canonical_url: "https://www.zhaopin.com/jobdetail/z-2.htm" })],
        });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("2");

    // 打开抽屉加载列表；查看本身不清除提醒（FR-017）。
    await wrapper.get('[data-testid="reminder-trigger"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="reminder-drawer"]').exists()).toBe(true);
    expect(wrapper.findAll('[data-testid="reminder-item"]')).toHaveLength(2);

    await wrapper.get('[data-testid="reminder-close"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="reminder-drawer"]').exists()).toBe(false);
    // 关闭抽屉后徽标保持服务端总数；不因查看而清零。
    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("2");
    expect(countCalls).toBe(1);
  });

  it("refreshes badge and drawer from server results after a follow-up succeeds", async () => {
    let countTotal = 2;
    let reminderItems = [reminderItemFixture()];
    let actionCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: countTotal });
      }
      if (url.includes("/api/job-reminders")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: countTotal, items: reminderItems });
      }
      if (url.endsWith("/api/profile-jobs/actions")) {
        actionCalls += 1;
        const body = JSON.parse(String(init?.body));
        expect(body.action).toBe("follow_up");
        expect(body.profile_id).toBe("p1");
        expect(body.request_id).toBeTruthy();
        expect(body.job).toEqual({ job_id: "internal-1" });
        return response({
          ok: true, replayed: false, changed: true, event_id: "ev-1", event_sequence: 1,
          state: {
            profile_id: "p1", job_id: "internal-1", status: "applied",
            applied_at: "2026-05-01T02:00:00+00:00",
            last_follow_up_at: "2026-08-05T02:00:00+00:00",
            revision: 2, reminder: { eligible: false, baseline_at: "2026-08-05T02:00:00+00:00", elapsed_seconds: 0, elapsed_days: 0 },
          },
        });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.get('[data-testid="reminder-trigger"]').trigger("click");
    await flushPromises();
    expect(wrapper.findAll('[data-testid="reminder-item"]')).toHaveLength(1);

    // 服务端视角：跟进成功后该岗位退出提醒。
    countTotal = 1;
    reminderItems = [];
    await wrapper.get('[data-testid="btn-follow-up"]').trigger("click");
    await flushPromises();

    expect(actionCalls).toBe(1);
    // 徽标与列表均按服务端刷新结果更新，不做本地删除模拟。
    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("1");
    expect(wrapper.find('[data-testid="reminder-empty"]').exists()).toBe(true);
  });

  it("keeps reminders and shows the API message when a drawer action fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 1 });
      }
      if (url.includes("/api/job-reminders")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 1, items: [reminderItemFixture()] });
      }
      if (url.endsWith("/api/profile-jobs/actions")) {
        expect(JSON.parse(String(init?.body)).action).toBe("mark_stale");
        return response({ ok: false, error_code: "persistence_failed", user_message: "保存失败，请稍后重试" }, 500);
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.get('[data-testid="reminder-trigger"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="btn-mark-stale"]').trigger("click");
    await flushPromises();

    // 失败保留原 UI 状态：提醒项与徽标不变，展示真实 API 文案（FR-037）。
    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("1");
    expect(wrapper.findAll('[data-testid="reminder-item"]')).toHaveLength(1);
    expect(wrapper.get('[data-testid="item-action-error"]').text()).toContain("保存失败，请稍后重试");
  });

  it("cancels a favorite with only the internal job id (no partial identity fields)", async () => {
    // 回归：收藏抽屉点 X 取消收藏此前附带 job_link 但缺 platform，
    // 后端权威解析报“岗位身份信息不完整”422；修复后只传内部 job_id。
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/favorites")) {
        return response({
          items: [{
            job_id: "internal-1",
            profile_id: "p1",
            title: "Python 后端",
            company: "甲公司",
            salary: "20-30K",
            location: "上海",
            job_link: "https://www.zhipin.com/job_detail/x1.html",
          }],
          count: 1,
        });
      }
      if (url.endsWith("/api/pipeline/jobs/interest/cancel")) {
        const body = JSON.parse(String(init?.body));
        // 身份字段只允许内部 job_id；不得携带 job_link 等部分三元组候选。
        expect(body).toEqual({ profile_id: "p1", job: { job_id: "internal-1" } });
        return response({ interest_state: "cancelled", job_id: "internal-1" });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.get(".favorites-trigger").trigger("click");
    await flushPromises();
    expect(wrapper.find(".fav-card-remove").exists()).toBe(true);

    await wrapper.get(".fav-card-remove").trigger("click");
    await flushPromises();
    // 取消成功后卡片移除，抽屉保持打开；提示是成功态，不弹错误。
    expect(wrapper.find(".fav-card-remove").exists()).toBe(false);
    const notice = wrapper.find(".notice-bar");
    expect(notice.exists()).toBe(true);
    expect(notice.text()).toContain("已取消收藏");
    expect(notice.text()).not.toContain("失败");
  });

  it("closes the drawer on profile switch and drops stale count responses from the old profile", async () => {    const pendingCounts: Array<{ url: string; resolve: (value: Response) => void }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        return new Promise<Response>((resolve) => { pendingCounts.push({ url, resolve }); });
      }
      if (url.includes("/api/job-reminders")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 1, items: [reminderItemFixture()] });
      }
      if (url.endsWith("/api/profiles")) {
        return response({ profiles: [{ id: "p1", name: "画像A" }, { id: "p2", name: "画像B" }] });
      }
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();
    expect(pendingCounts).toHaveLength(1);

    // 打开旧 profile 的抽屉。
    await wrapper.get('[data-testid="reminder-trigger"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="reminder-drawer"]').exists()).toBe(true);

    // 切换 profile：关闭旧抽屉并重新请求新 profile 的 count。
    wrapper.findComponent(DiscoveryView).vm.$emit("profile-created", { id: "p2", name: "画像B" });
    await flushPromises();
    expect(wrapper.find('[data-testid="reminder-drawer"]').exists()).toBe(false);
    expect(pendingCounts).toHaveLength(2);
    expect(pendingCounts[1].url).toContain("profile_id=p2");

    // 旧 profile 的 count 晚到：不得覆盖新 profile 显示。
    pendingCounts[0].resolve(response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 99 }));
    await flushPromises();
    expect(wrapper.find('[data-testid="reminder-badge"]').exists()).toBe(false);

    pendingCounts[1].resolve(response({ ok: true, profile_id: "p2", threshold_hours: 720, total: 4 }));
    await flushPromises();
    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("4");
  });

  it("refreshes the badge when DiscoveryView lifecycle actions succeed, and drops stale-profile events", async () => {
    let countTotal = 2;
    let countCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        countCalls += 1;
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: countTotal });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("2");
    expect(countCalls).toBe(1);

    // 详情生命周期 action 成功（服务端视角：提醒数 2→1）：App 重新查询当前 profile。
    countTotal = 1;
    wrapper.findComponent(DiscoveryView).vm.$emit("job-feedback-changed", { profileId: "p1", jobId: "internal-1" });
    await flushPromises();
    expect(countCalls).toBe(2);
    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("1");

    // 旧 profile 的 action 事件晚到：不代旧 profile 发请求（ui-interaction.md §当前画像切换 5）。
    const callsBeforeStale = countCalls;
    wrapper.findComponent(DiscoveryView).vm.$emit("job-feedback-changed", { profileId: "profile-other", jobId: "internal-x" });
    await flushPromises();
    expect(countCalls).toBe(callsBeforeStale);
  });

  // ---------- 主题切换按钮（阶段 1）----------

  it("renders a theme toggle button in the header that switches data-theme on click", async () => {
    // 重置 useTheme 单例到已知 light 状态（其他用例可能已切换过）。
    toggleTheme("light");
    document.documentElement.removeAttribute("data-theme");

    const wrapper = mount(App);
    await flushPromises();

    const toggle = wrapper.get('[data-testid="theme-toggle"]');
    // 初始为 light，aria-label 提示可切到暗色。
    expect(toggle.attributes("aria-label")).toBe("切换到暗色模式");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");

    await toggle.trigger("click");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(wrapper.get('[data-testid="theme-toggle"]').attributes("aria-label"))
      .toBe("切换到浅色模式");

    // 再点一次回到 light。
    await wrapper.get('[data-testid="theme-toggle"]').trigger("click");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  // ---------- 更新提示：自动弹窗 / 忽略 / 项目名红点 ----------

  function updateResponse() {
    return response({
      ok: true,
      current: "2.5.0",
      latest: "2.6.0",
      has_update: true,
      release_url: "https://github.com/Czilasy/career-scout-preview/releases/tag/v2.6.0",
      release_notes: "## 更新内容",
      asset_name: "CareerScout-v2.6.0.exe",
      asset_url: "https://github.com/x/x.exe",
      asset_size: 1000,
      sha256_url: "https://github.com/x/x.sha256",
      reason: "",
      checked_at: 0,
      runtime_mode: "exe",
      installable: true,
    });
  }

  function mountWithUpdate(overrides: { ignored?: string } = {}) {
    localStorage.clear();
    if (overrides.ignored) localStorage.setItem("career-scout-ignored-update", overrides.ignored);
    const updateCheckUrls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/session")) {
        return response({ status: "ok", build_hash: expectedBackendBuildHash, runtime_mode: "exe" });
      }
      if (url.includes("/api/update-check")) {
        updateCheckUrls.push(url);
        return updateResponse();
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(App);
    return { wrapper, updateCheckUrls };
  }

  it("startup check always queries GitHub so new releases pop up on next launch", async () => {
    const { wrapper, updateCheckUrls } = mountWithUpdate();
    await flushPromises();
    expect(updateCheckUrls.length).toBeGreaterThan(0);
    for (const url of updateCheckUrls) expect(url).toContain("/api/update-check");
    expect(wrapper.find('[data-testid="update-dialog"]').exists()).toBe(true);
  });

  it("auto-opens update dialog and shows brand dot on first discovery", async () => {
    const { wrapper } = mountWithUpdate();
    await flushPromises();
    expect(wrapper.find('[data-testid="update-dialog"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="brand-update-dot"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="settings-update-badge"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="update-trigger"]').exists()).toBe(false);
  });

  it("does not auto-open dialog for ignored version but keeps brand dot", async () => {
    const { wrapper } = mountWithUpdate({ ignored: "2.6.0" });
    await flushPromises();
    expect(wrapper.find('[data-testid="update-dialog"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="brand-update-dot"]').exists()).toBe(true);
  });

  it("closes dialog and records version when ignoring", async () => {
    const { wrapper } = mountWithUpdate();
    await flushPromises();
    expect(wrapper.find('[data-testid="update-dialog"]').exists()).toBe(true);
    await wrapper.get('[data-testid="update-ignore"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="update-dialog"]').exists()).toBe(false);
    expect(localStorage.getItem("career-scout-ignored-update")).toBe("2.6.0");
    // 红点保留，点击项目名重新打开
    expect(wrapper.find('[data-testid="brand-update-dot"]').exists()).toBe(true);
    await wrapper.get(".brand").trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="update-dialog"]').exists()).toBe(true);
  });

  it("skips update checks and hides update controls in source mode", async () => {
    let updateCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/update-check")) {
        updateCalls += 1;
        return updateResponse();
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(App);
    await flushPromises();

    expect(updateCalls).toBe(0);
    expect(wrapper.find('[data-testid="update-trigger"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="brand-update-dot"]').exists()).toBe(false);
    await wrapper.get('[data-testid="settings-trigger"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="manual-update-check"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="github-link"]').exists()).toBe(true);
  });

  it("falls back to # when a favorite link fails the URL allowlist", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/favorites")) {
        return response({
          items: [{
            job_id: "internal-unsafe",
            profile_id: "p1",
            platform: "boss",
            title: "危险链接岗位",
            company: "甲公司",
            salary: "20-30K",
            location: "上海",
            job_link: "https://user:pass@evil.example.com/job",
          }],
          count: 1,
        });
      }
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.get(".favorites-trigger").trigger("click");
    await flushPromises();
    expect(wrapper.get(".fav-card-main").attributes("href")).toBe("#");
  });
});
