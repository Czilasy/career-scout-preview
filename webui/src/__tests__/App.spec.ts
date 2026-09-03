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
      if (url.endsWith("/api/profiles")) {
        // 026 B078：DiscoveryView 在 currentProfileId 就绪后才挂载（v-if），
        // 空 profiles 会导致工作台不渲染——测试需提供至少一个 profile。
        return response({ profiles: [{ id: "profile-1", name: "测试" }] });
      }
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

  it("auto-creates the first profile on an empty database", async () => {
    // 回归：026 B078 后 DiscoveryView 只在 currentProfileId 就绪后挂载，
    // 空库首启若不自动建画像，工作台会永远停在「正在加载工作台…」。
    const createdBodies: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/session")) {
        return response({ status: "ok", build_hash: expectedBackendBuildHash });
      }
      if (url.endsWith("/api/profiles")) {
        if ((init?.method || "GET") === "POST") {
          createdBodies.push(String(init?.body || ""));
          return response({ id: "profile-auto", name: "我的求职画像" });
        }
        return response({ profiles: [] });
      }
      return response({});
    }));

    const wrapper = mount(App);
    await flushPromises();

    expect(createdBodies).toHaveLength(1);
    expect(JSON.parse(createdBodies[0])).toEqual({ name: "我的求职画像", confirmed_fields: {} });
    expect(wrapper.find(".app-content-placeholder").exists()).toBe(false);
    expect(wrapper.findComponent(DiscoveryView).props("profileId")).toBe("profile-auto");
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

  it("036: renders DynamicIsland completed state with capsule payload", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    // 待确认 >0：显示匹配与待确认数字
    view.vm.$emit("round-status", {
      platform: "boss", phase: "judged", judged: 5, scope: "all",
      capsule: { state: "completed", platform: "boss", results: { matched: 5, pending: 2 } },
    } as never);
    await flushPromises();
    const completed = wrapper.get('[data-testid="dynamic-island-completed"]');
    expect(completed.text()).toContain("匹配 5");
    expect(completed.text()).toContain("待确认 2");

    // 待确认为 0：不显示待确认数字
    view.vm.$emit("round-status", {
      platform: "boss", phase: "judged", judged: 5, scope: "all",
      capsule: { state: "completed", platform: "boss", results: { matched: 5, pending: 0 } },
    } as never);
    await flushPromises();
    expect(wrapper.get('[data-testid="dynamic-island-completed"]').text()).not.toContain("待确认");
  });

  it("036: DynamicIsland click dispatches capsule navigation", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    view.vm.$emit("round-status", {
      platform: "boss", phase: "running", judged: 0, scope: "boss",
      capsule: { state: "running", platform: "boss", progress: { phase: "scraping", done: 1, total: 10 } },
    } as never);
    await flushPromises();
    await wrapper.get('[data-testid="dynamic-island-running"]').trigger("click");
    // requestCapsuleNavigation 已消费：胶囊导航信号不抛错、不改变挂载
    expect(wrapper.find('[data-testid="dynamic-island-running"]').exists()).toBe(true);
  });

  // ---------- 037：灵动岛通知池 / 面板 / 角标单源回退 ----------

  function emitRoundTransition(view: { vm: { $emit: (e: string, p: unknown) => void } }) {
    // running → completed：触发 useIslandNotices 派生一条 completed 通知。
    view.vm.$emit("round-status", {
      platform: "boss", phase: "judged", judged: 0, scope: "all",
      capsule: { state: "running", platform: "boss", progress: { phase: "scraping", done: 1, total: 10 } },
    } as never);
    view.vm.$emit("round-status", {
      platform: "boss", phase: "judged", judged: 5, scope: "all",
      capsule: { state: "completed", platform: "boss", results: { matched: 5, pending: 2 } },
    } as never);
  }

  // 038 P1-2：completed 通知 read:true（"完成"信号由 pill 彩色芯片展示，
  // panel 行只是历史）；测试需要"未读 → 点击展开 panel"流程时改用 attention
  // 过渡（paused/error 仍 read:false，是仅剩的 panel 未读来源）。
  function emitAttentionTransition(
    view: { vm: { $emit: (e: string, p: unknown) => void } },
    message = "任务已暂停",
  ) {
    view.vm.$emit("round-status", {
      platform: "boss", phase: "judged", judged: 0, scope: "all",
      capsule: { state: "running", platform: "boss", progress: { phase: "scraping", done: 1, total: 10 } },
    } as never);
    view.vm.$emit("round-status", {
      platform: "boss", phase: "judged", judged: 0, scope: "all",
      capsule: { state: "attention", platform: "boss", attention: { kind: "paused", message } },
    } as never);
  }

  it("037: island catches notices first — pill click opens panel, not navigation", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    // 038 P1-2：completed 通知 read:true，改用 attention（paused）触发未读。
    emitAttentionTransition(view);
    await flushPromises();

    // 未读红点出现；点击胶囊展开面板而非派发直达导航。
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");
    await wrapper.get('[data-testid="dynamic-island-attention"]').trigger("click");
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="island-notice-row-paused"]').text()).toContain("任务已暂停");

    // B2 语义：展开期间红点仍在、行以未读渲染（看过才算读 → 收起时 markAllRead）。
    expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="island-notice-row-paused"]').attributes("data-notice-read")).toBe("false");

    // 收起（点 backdrop → dismiss → 快照内全部已读，红点消失）。backdrop 经
    // Teleport 挂到 body，不在组件根内，从 document 查询并派发原生点击。
    const backdrop = document.querySelector<HTMLElement>('[data-testid="island-backdrop"]');
    expect(backdrop).toBeTruthy();
    backdrop!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(false);
  });

  it("037: island panel and top drawers are mutually exclusive", async () => {
    // 默认 mock 对 /api/job-reminders 返回 {}（缺 items 字段），抽屉渲染会崩；
    // 这里给规范空列表（002 合同），聚焦互斥行为本身。
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/job-reminders")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 0, items: [] });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    // 038 P1-2：completed 通知 read:true，改用 attention 触发未读。
    emitAttentionTransition(view, "首次暂停");
    await flushPromises();
    await wrapper.get('[data-testid="dynamic-island-attention"]').trigger("click");
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(true);

    // 打开提醒抽屉 → 岛面板收起（collapse 触 dismiss，关闭瞬间的通知标已读）。
    await wrapper.get('[data-testid="reminder-trigger"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);

    // 通知已全部消费：再点胶囊 = 直达导航（面板不开；抽屉不因导航而强关）。
    await wrapper.get('[data-testid="dynamic-island-attention"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="reminder-drawer"]').exists()).toBe(true);

    // 新 attention 到达（不同 message 绕过 upsert 内容 dedup）→ 未读出现 →
    // 点胶囊开面板，并强制收起提醒抽屉。
    view.vm.$emit("round-status", {
      platform: "boss", phase: "judged", judged: 0, scope: "all",
      capsule: { state: "running", platform: "boss", progress: { phase: "scraping", done: 1, total: 10 } },
    } as never);
    view.vm.$emit("round-status", {
      platform: "boss", phase: "judged", judged: 0, scope: "all",
      capsule: { state: "attention", platform: "boss", attention: { kind: "paused", message: "再次暂停" } },
    } as never);
    await flushPromises();
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");
    await wrapper.get('[data-testid="dynamic-island-attention"]').trigger("click");
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="reminder-drawer"]').exists()).toBe(false);
  });

  it("037: switching profile resets the island notice pool and collapses an open panel", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    // 038 P1-2：completed 通知 read:true，改用 attention 触发未读。
    emitAttentionTransition(view);
    await flushPromises();
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");

    // 先展开面板，再切 profile：面板收起、通知池清空（reset + collapse，B3）。
    await wrapper.get('[data-testid="dynamic-island-attention"]').trigger("click");
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(true);

    // 新建并切换 profile：收起岛面板并清空通知池。
    view.vm.$emit("profile-created", { id: "profile-2", name: "画像B" });
    await flushPromises();
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(false);
  });

  it("037 regression: completed capsule never feeds the reminder badge (single source)", async () => {
    // 036 通用化 bug 的根因回归：角标曾合成胶囊派生数（投递+待确认+…），
    // 导致「角标有数字、抽屉是空的」。回退后角标单源 = 服务端 count。
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
    const view = wrapper.findComponent(DiscoveryView);

    // 任务跑完（matched 5 · pending 2）也不应把数字漏进提醒角标。
    emitRoundTransition(view);
    await flushPromises();

    expect(wrapper.find('[data-testid="reminder-badge"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="reminder-trigger"]').attributes("aria-label")).toBe("查看提醒");
    // 胶囊本身照常显示本轮结果。
    expect(wrapper.get('[data-testid="dynamic-island-completed"]').text()).toContain("匹配 5");
    // 038 P1-2：completed 通知 read:true（"完成"信号由 pill 彩色芯片实时展示，
    // panel 行只是历史不计未读），pill 不显示未读角标——投递/待确认数字不会
    // 反向漏进 island-unread。
    expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(false);
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
    // 037 回退：aria 只描述投递提醒本身，不再合成胶囊派生数。
    expect(trigger.attributes("aria-label")).toBe("查看投递提醒，共 3 个逾期岗位");
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
    expect(wrapper.get('[data-testid="reminder-trigger"]').attributes("aria-label")).toBe("查看提醒");
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
    // 后端权威解析报"岗位身份信息不完整"422；修复后只传内部 job_id。
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
    // 038 复审：取消收藏成功提示进灵动岛打断队列，不再走 notice-bar 浮窗。
    expect(wrapper.find(".notice-bar").exists()).toBe(false);
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");
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

  it("长按主题按钮 1 秒蓄满弹出选择框，且不误触明暗切换", async () => {
    vi.useFakeTimers();
    try {
      toggleTheme("light");
      document.documentElement.removeAttribute("data-theme");
      const wrapper = mount(App);
      await flushPromises();

      const toggle = wrapper.get('[data-testid="theme-toggle"]');
      await toggle.trigger("pointerdown");
      await vi.advanceTimersByTimeAsync(1000);
      await flushPromises();

      expect(wrapper.find('[data-testid="theme-picker"]').exists()).toBe(true);
      // 长按蓄力结束后浏览器补发的 click 必须被吞掉，明暗保持不变。
      await toggle.trigger("click");
      expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    } finally {
      vi.useRealTimers();
    }
  });

  it("蓄力中途松手取消弹出，点击仍正常切换明暗", async () => {
    vi.useFakeTimers();
    try {
      toggleTheme("light");
      document.documentElement.removeAttribute("data-theme");
      const wrapper = mount(App);
      await flushPromises();

      const toggle = wrapper.get('[data-testid="theme-toggle"]');
      await toggle.trigger("pointerdown");
      await vi.advanceTimersByTimeAsync(400);
      await toggle.trigger("pointerup");
      await vi.advanceTimersByTimeAsync(1000);

      expect(wrapper.find('[data-testid="theme-picker"]').exists()).toBe(false);
      await toggle.trigger("click");
      expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    } finally {
      vi.useRealTimers();
    }
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

  // ---------- 038：灵动岛 v3 接线（NoticeBar 分流 / 提醒打断 / carousel reset）----------

  it("038: warning notice 折进 island 打断队列，NoticeBar 不渲染", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    // 推一条 warning notice（经 DiscoveryView @notify → showNotice 分流）。
    view.vm.$emit("notify", { message: "检查更新失败", tone: "warning" });
    await flushPromises();

    // NoticeBar 不渲染（warning 被 showNotice 分流到 carousel pushInterrupt）。
    expect(wrapper.find(".notice-bar").exists()).toBe(false);
    // carousel 收到打断：badgeCount=1，pill 角标 = notices 未读(0) + badge(1) = 1。
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");
  });

  it("038: error notice 折进 island 打断队列", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    view.vm.$emit("notify", { message: "导出失败", tone: "error" });
    await flushPromises();

    expect(wrapper.find(".notice-bar").exists()).toBe(false);
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");
  });

  it("038 复审：info/success notice 也融入 island 打断队列（不再保留 NoticeBar 浮窗）", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    view.vm.$emit("notify", { message: "已保存", tone: "success" });
    await flushPromises();

    // 038 复审：info/success 不再走 NoticeBar 浮窗，统一进灵动岛打断队列
    // （用户要求「信息性浮窗全部进灵动岛，不要弹出小条幅」）。
    expect(wrapper.find(".notice-bar").exists()).toBe(false);
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");
  });

  it("038: 投递提醒 0→N 推一条打断进 carousel", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 3 });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();

    // reminderTotal 0→3 触发 pushInterrupt：badgeCount=1，pill 角标=1。
    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("3");
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");
  });

  it("038: 投递提醒 N→M（都 >0）不重复推打断", async () => {
    let countTotal = 3;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: countTotal });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(App);
    await flushPromises();
    // 首次 0→3 推一条：badgeCount=1。
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");

    // 手动刷新 count，total 3→5（都 >0）：不推新打断（badgeCount 不增长）。
    countTotal = 5;
    wrapper.findComponent(DiscoveryView).vm.$emit("job-feedback-changed", { profileId: "p1" });
    await flushPromises();
    // badgeCount 仍为 1（只 0→N 推一次）；角标仍是 1（notices 未读 0 + badge 1）。
    expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("5");
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");
  });

  it("038: profile 切换清空 carousel 打断队列", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    // 推一条 warning 打断 → badgeCount=1。
    view.vm.$emit("notify", { message: "测试警告", tone: "warning" });
    await flushPromises();
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");

    // 切换 profile → carousel.reset()：打断队列清空，角标消失。
    view.vm.$emit("profile-created", { id: "profile-2", name: "画像B" });
    await flushPromises();
    expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(false);
  });

  it("038: TaskCompletedToast 删除——completed 态不弹独立 toast，由 pill 接管", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    // 跑完一轮 → completed 态：原 toast 会在此弹"点此查看最新"。
    emitRoundTransition(view);
    await flushPromises();

    // 组件已删：不渲染 task-completed-toast 元素；completed pill 是"完成→查看最新"主路径。
    expect(wrapper.find('[data-testid="task-completed-toast"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="dynamic-island-completed"]').exists()).toBe(true);
  });

  // ---------- 038 复审 P2-1 / P2-5 / P2-8 ----------

  it("038 P2-1: completed 通知 read:true，点 pill 直达结果页（不展开 panel）", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const view = wrapper.findComponent(DiscoveryView);

    // 跑完一轮 → completed 通知 read:true（P1-2 裁决）。
    emitRoundTransition(view);
    await flushPromises();

    // 无未读：pill 不显示角标；点击直达 results（不展开 panel）。
    expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(false);
    await wrapper.get('[data-testid="dynamic-island-completed"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
    // 等价被删 TaskCompletedToast 的一键直达——胶囊导航不抛错、不改变挂载。
    expect(wrapper.find('[data-testid="dynamic-island-completed"]').exists()).toBe(true);
  });

  it("038 P2-5: history 浏览期间 notify 暂停消费，回最新时 flush", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = mount(App);
      await flushPromises();
      const view = wrapper.findComponent(DiscoveryView);

      // 进入 history scope（浏览历史轮）。
      view.vm.$emit("round-status", {
        platform: "boss", phase: "judged", judged: 0, scope: "history",
        capsule: { state: "completed", platform: "boss", results: { matched: 3, pending: 0 } },
      } as never);
      await flushPromises();

      // history 期间推一条 warning → pushInterruptOrDefer 缓冲，不打断 carousel。
      view.vm.$emit("notify", { message: "导出失败", tone: "warning" });
      await flushPromises();
      expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(false);

      // 回到最新（scope 离开 history）→ watch flush → pushInterrupt → 角标出现。
      view.vm.$emit("round-status", {
        platform: "boss", phase: "judged", judged: 0, scope: "all",
        capsule: { state: "running", platform: "boss", progress: { phase: "scraping", done: 1, total: 10 } },
      } as never);
      await flushPromises();
      expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");

      // 推进 timer → 打断沉入 panel（未读条出现，kind=interrupt，target=task）。
      await vi.advanceTimersByTimeAsync(2200);
      await flushPromises();
      expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("038 P2-8: 投递提醒打断沉入 panel 后行点击直达 reminders（开提醒抽屉）", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/job-reminders/count")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 3 });
      }
      if (url.includes("/api/job-reminders")) {
        return response({ ok: true, profile_id: "p1", threshold_hours: 720, total: 3, items: [] });
      }
      if (url.endsWith("/api/profiles")) return response({ profiles: [{ id: "p1", name: "画像A" }] });
      return baseAppResponse(url) ?? response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      const wrapper = mount(App);
      await flushPromises();

      // reminder 0→3 触发 pushInterrupt（target="reminders"）：badgeCount=1。
      expect(wrapper.get('[data-testid="reminder-badge"]').text()).toBe("3");
      expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");

      // 推进 timer → 打断沉入 panel（target="reminders"，未读）。
      await vi.advanceTimersByTimeAsync(2200);
      await flushPromises();
      // 沉入后 badgeCount=0；notices 多一条 interrupt 未读 → 总未读仍是 1。
      expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("1");

      // 点 pill → 展开 panel；行点击 → handleIslandNavigate("reminders") → 开提醒抽屉。
      await wrapper.get('[data-testid="island-unread"]').trigger("click");
      await flushPromises();
      const row = wrapper.find('[data-testid="island-notice-row-interrupt"]');
      expect(row.exists()).toBe(true);
      expect(row.text()).toContain("投递提醒");
      await row.trigger("click");
      await flushPromises();
      expect(wrapper.find('[data-testid="reminder-drawer"]').exists()).toBe(true);
      // panel 收起（row-click 触 requestClose）。
      expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });
});
