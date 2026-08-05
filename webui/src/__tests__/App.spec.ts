import { flushPromises, mount } from "@vue/test-utils";
import App from "../App.vue";
import DiscoveryView from "../views/DiscoveryView.vue";
import { expectedBackendBuildHash } from "../api";
import { toggleTheme } from "../composables/useTheme";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("App", () => {
  beforeEach(() => {
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

    await wrapper.get('[data-testid="ai-settings-trigger"]').trigger("click");
    const dialog = wrapper.get('[role="dialog"]');
    expect(dialog.attributes("aria-modal")).toBe("true");

    await dialog.trigger("keydown", { key: "Escape" });
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  });

  it("keeps browser account manager reachable", async () => {
    const wrapper = mount(App);
    await flushPromises();

    await wrapper.get('[data-testid="browser-accounts-trigger"]').trigger("click");
    const dialog = wrapper.get('[role="dialog"]');
    expect(dialog.text()).toContain("自动化浏览器账号");

    await dialog.trigger("keydown", { key: "Escape" });
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  });

  it("shows a persistent inline notice when the model list is empty", async () => {
    vi.useFakeTimers();
    const wrapper = mount(App);
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
    await wrapper.get('[role="dialog"]').trigger("keydown", { key: "Escape" });
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

  it("closes the drawer on profile switch and drops stale count responses from the old profile", async () => {
    const pendingCounts: Array<{ url: string; resolve: (value: Response) => void }> = [];
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
});
