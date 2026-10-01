// Spec 044 B100：常用搜索配置包在第一页/第二页的真实接线测试。
//
// 这一层只 stub 网络边界（fetch），页面内部逻辑全部真实执行：
// 未点击保存不得产生写请求；选择配置包必须一步回填第二页且不触发
// 简历上传、AI 分析或搜索；失败必须留在第一页并发出 error 通知。
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DiscoveryView from "../DiscoveryView.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

enableAutoUnmount(afterEach);

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ADVANCED_SETTINGS = {
  ok: true,
  selection: "balanced",
  settings: {
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
  },
  last_custom: null,
  mode_version: null,
  manual_ranges: {},
  config_schema_version: 1,
};

const FILTER_SCHEMA = {
  ok: true,
  platform: "boss",
  schema_version: 1,
  enabled_for_new_tasks: true,
  fields: [
    {
      key: "salary",
      label: "薪资",
      multiple: false,
      options: [{ value: "20-30K", label: "20-30K" }],
    },
  ],
};

function packageBody(overrides: Record<string, unknown> = {}): any {
  return {
    id: "pkg-1",
    name: "产品经理 · 上海",
    payloadVersion: 2,
    keywords: {
      candidates: [
        { word: "产品经理", recommended: true },
        { word: "项目管理", recommended: false },
      ],
      selected: ["产品经理"],
      custom: "",
    },
    city: { text: "上海", custom: "" },
    profile: { summary: "3 年 B 端产品经验", facts: { experience_years: 3 } },
    conditions: {
      snapshotVersion: 2,
      mappingVersion: "b096-v2-2026-09-27",
      unifiedValues: { salary: [], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [] },
      platformValues: { boss: {}, zhilian: {} },
      overrides: { boss: {}, zhilian: {} },
      exclusiveValues: { boss: { stage: [] }, zhilian: { company_nature: [] } },
    },
    createdAt: "2026-09-21T10:00:00+08:00",
    updatedAt: "2026-09-21T10:00:00+08:00",
    ...overrides,
  };
}

interface StubOptions {
  items?: Array<{ id: string; name: string }>;
  single?: () => Response;
  saveResponse?: () => Response;
  listResponse?: () => Response;
}

function makeFetchMock(options: StubOptions = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = String(init?.method || "GET").toUpperCase();
    if (url.startsWith("/api/search-packages")) {
      if (method === "GET" && /\/api\/search-packages\/[^/?]+$/.test(url)) {
        return options.single ? options.single() : response(packageBody());
      }
      if (method === "GET") {
        if (options.listResponse) return options.listResponse();
        return response({
          items: (options.items ?? []).map((item) => ({
            createdAt: "2026-09-21T10:00:00+08:00",
            updatedAt: "2026-09-21T10:00:00+08:00",
            ...item,
          })),
        });
      }
      if (options.saveResponse) return options.saveResponse();
      if (method === "POST") return response(packageBody(), 201);
      return response(packageBody());
    }
    if (url.includes("/api/latest-running-task")) return response({ ok: true, has_task: false });
    if (url.includes("/api/filter-labels")) return response(FILTER_SCHEMA);
    if (url.includes("/api/options")) {
      return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
    }
    if (url.endsWith("/api/advanced-settings")) return response(ADVANCED_SETTINGS);
    return response({});
  });
}

async function mountView(options: StubOptions = {}) {
  const fetchMock = makeFetchMock(options);
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(DiscoveryView, {
    props: { profileId: "profile-044" },
    global: { stubs: { Teleport: true } },
  });
  await flushPromises();
  return { wrapper, fetchMock };
}

type FetchMock = ReturnType<typeof makeFetchMock>;
type Wrapper = Awaited<ReturnType<typeof mountView>>["wrapper"];

function packageWrites(fetchMock: FetchMock) {
  return fetchMock.mock.calls.filter(([url, init]) => {
    const method = String((init as RequestInit | undefined)?.method || "GET").toUpperCase();
    return String(url).startsWith("/api/search-packages") && method !== "GET";
  });
}

function callsSince(fetchMock: FetchMock, index: number): string[] {
  return fetchMock.mock.calls.slice(index).map(([url]) => String(url));
}

async function enterSearchStep(wrapper: Wrapper) {
  const skip = wrapper.findAll("button").find((btn) => btn.text().includes("跳过简历"));
  if (!skip) throw new Error("未找到“跳过简历，直接手动搜索”按钮");
  await skip.trigger("click");
  await flushPromises();
}

describe("DiscoveryView 常用搜索配置包", () => {
  beforeEach(() => {
    setBuildIdentity(expectedBackendBuildHash);
    sessionStorage.clear();
    localStorage.clear();
  });

  it("第一页的跳过简历和使用已有配置入口占同一行", async () => {
    const { wrapper } = await mountView();

    const shortcuts = wrapper.get(".upload-shortcuts");
    expect(shortcuts.find('[data-testid="saved-package-entry"]').exists()).toBe(true);
    expect(shortcuts.findAll("button").some((button) => button.text().includes("跳过简历"))).toBe(true);
    expect(wrapper.find(".collapsible-header-actions [data-testid=package-save]").exists()).toBe(true);
  });

  it("第二页编辑但不点保存时，不产生任何配置包写请求", async () => {
    const { wrapper, fetchMock } = await mountView();
    await enterSearchStep(wrapper);

    await wrapper.get('[data-testid="custom-keyword"]').setValue("数据分析");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("杭州");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await wrapper.get(".profile-summary-input").setValue("5 年数据分析经验");
    await flushPromises();

    expect(packageWrites(fetchMock)).toEqual([]);
  });

  it("点击保存后按用户输入创建配置包，且不展示当前配置名称", async () => {
    const { wrapper, fetchMock } = await mountView();
    await enterSearchStep(wrapper);
    await wrapper.get('[data-testid="custom-keyword"]').setValue("数据分析");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get(".profile-summary-input").setValue("5 年数据分析经验");
    await flushPromises();

    await wrapper.get('[data-testid="package-save"]').trigger("click");
    await wrapper.get('[data-testid="package-name-input"]').setValue("我的常用配置");
    await wrapper.get('[data-testid="package-confirm-save"]').trigger("click");
    await flushPromises();

    const writes = packageWrites(fetchMock);
    expect(writes).toHaveLength(1);
    const [url, init] = writes[0];
    expect(String(url)).toBe("/api/search-packages");
    expect(String((init as RequestInit).method).toUpperCase()).toBe("POST");
    const body = JSON.parse(String((init as RequestInit).body));
    expect(body.name).toBe("我的常用配置");
    expect(body.payloadVersion).toBe(2);
    expect(body.keywords.selected).toContain("数据分析");
    expect(body.profile.summary).toBe("5 年数据分析经验");
    expect(body.filterValues).toBeUndefined();
    expect(body.platform).toBeUndefined();
    expect(wrapper.find('[data-testid="package-current-name"]').exists()).toBe(false);
  });

  it("第一页选择配置包：直接进入第二页并完整回填，不重传简历也不开始搜索", async () => {
    const { wrapper, fetchMock } = await mountView({
      items: [{ id: "pkg-1", name: "产品经理 · 上海" }],
    });
    expect(wrapper.findAll(".filter-group .choice-chip.selected")).toHaveLength(0);
    const beforeSelect = fetchMock.mock.calls.length;

    await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-item"]').trigger("click");
    await flushPromises();

    // 进入第二页：上传页隐藏、搜索页可见。
    expect(wrapper.get(".upload-layout").isVisible()).toBe(false);
    // 完整回填关键词、城市、画像。
    expect(wrapper.text()).toContain("产品经理");
    expect((wrapper.get(".profile-summary-input").element as HTMLTextAreaElement).value)
      .toBe("3 年 B 端产品经验");
    expect(wrapper.find('[data-testid="package-current-name"]').exists()).toBe(false);
    // 选择期间没有简历上传、AI 分析或搜索启动请求。
    const during = callsSince(fetchMock, beforeSelect);
    expect(during.some((url) => url.includes("/api/resumes"))).toBe(false);
    expect(during.some((url) => url.includes("/api/ai-"))).toBe(false);
    expect(during.some((url) => url.includes("/api/search-runs"))).toBe(false);
    expect(during.some((url) => url.includes("execute"))).toBe(false);
    // 配置包请求不带平台参数（同一套包两个平台共用）。
    expect(during.every((url) => !url.includes("platform="))).toBe(true);
    // 第三页筛选条件没有被配置包恢复或改写。
    expect(wrapper.findAll(".filter-group .choice-chip.selected")).toHaveLength(0);
  });

  it("T039: V2 配置恢复原始平台微调与专属字段，且不改写映射覆盖", async () => {
    const v2 = packageBody();
    v2.conditions = {
        snapshotVersion: 2,
        mappingVersion: "b096-v2-2026-09-27",
        unifiedValues: { salary: ["10K-20K"], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [] },
        platformValues: { boss: { salary: ["10-20K"] }, zhilian: { salary: ["10K-15K"] } },
        overrides: { boss: { salary: ["20-50K"] }, zhilian: { salary: ["15K-25K"] } },
        exclusiveValues: { boss: { stage: ["full_time"] }, zhilian: { company_nature: ["state_owned"] } },
    };
    const { wrapper } = await mountView({ items: [{ id: "pkg-1", name: "V2" }], single: () => response(v2) });
    await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-item"]').trigger("click");
    await flushPromises();

    expect(wrapper.get(".upload-layout").isVisible()).toBe(false);
    expect(wrapper.findAll(".filter-group .choice-chip.selected")).toHaveLength(0);
  });

  it("T039: 配置包恢复 V2 快照后重开全部弹窗仍冻结两平台专属字段", async () => {
    const v2 = packageBody({
      conditions: {
        snapshotVersion: 2,
        mappingVersion: "b096-v2-2026-09-27",
        unifiedValues: { salary: [], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [] },
        platformValues: {
          boss: { stage: ["boss-stage"], industry: ["boss-industry"] },
          zhilian: {
            company_nature: ["state-owned"], degree: ["206"], industry: ["002"],
            zhilian_mba_tuning: ["emba"],
          },
        },
        overrides: {
          boss: { industry: ["boss-industry"] },
          zhilian: { degree: ["206"], industry: ["002"], zhilian_mba_tuning: ["emba"] },
        },
        exclusiveValues: { boss: { stage: ["boss-stage"] }, zhilian: { company_nature: ["state-owned"] } },
      },
    });
    const baseFetch = makeFetchMock({
      items: [{ id: "pkg-1", name: "V2" }],
      single: () => response(v2),
    });
    const launches: Array<Record<string, unknown>> = [];
    const flow = {
      id: "flow-package-v2", profile_id: "profile-044", selection: "all" as const, status: "queued",
      tracks: [
        { id: "boss-track", flow_id: "flow-package-v2", platform: "boss" as const, status: "queued", stage: "pending" },
        { id: "zhilian-track", flow_id: "flow-package-v2", platform: "zhilian" as const, status: "queued", stage: "pending" },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/flows/current")) return response({ ok: true, flow: null });
      if (url === "/api/flows") return response({ ok: true, flow });
      if (url === "/api/execute-search") {
        launches.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return response({ ok: true, task_id: `package-${launches.length}` });
      }
      return baseFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, {
      props: { profileId: "profile-044" },
      global: { stubs: { Teleport: true } },
    });
    await flushPromises();
    await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-item"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="profile-confirm"]').trigger("click");
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    await flushPromises();
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(launches).toHaveLength(2);
    expect(Object.fromEntries(launches.map((body) => [body.platform, body.auto_screen_fields]))).toEqual({
      boss: { stage: ["boss-stage"], industry: ["boss-industry"] },
      zhilian: {
        company_nature: ["state-owned"], degree: ["206"], industry: ["002"],
        zhilian_mba_tuning: ["emba"],
      },
    });
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("T039: V1 配置不触发条件映射重算，条件保持空白", async () => {
    const v1 = packageBody();
    v1.payloadVersion = 1;
    delete v1.conditions;
    const { wrapper } = await mountView({ items: [{ id: "pkg-1", name: "V1" }], single: () => response(v1) });
    await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-item"]').trigger("click");
    await flushPromises();

    expect(wrapper.get(".upload-layout").isVisible()).toBe(false);
    expect(wrapper.findAll(".filter-group .choice-chip.selected")).toHaveLength(0);
  });

  it("选择新简历但尚未分析时，后续保存按新包创建而不是更新旧包", async () => {
    const { wrapper, fetchMock } = await mountView({
      items: [{ id: "pkg-1", name: "产品经理 · 上海" }],
    });
    await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-item"]').trigger("click");
    await flushPromises();

    await wrapper.findAll(".step-nav button")[0].trigger("click");
    await flushPromises();
    const newResume = new File(["new resume"], "new-resume.txt", {
      type: "text/plain",
      lastModified: 2,
    });
    const resumeInput = wrapper.get('[data-testid="resume-input"]');
    Object.defineProperty(resumeInput.element, "files", {
      configurable: true,
      value: [newResume],
    });
    await resumeInput.trigger("change");
    await flushPromises();
    const skip = wrapper.findAll("button").find((btn) => btn.text().includes("跳过简历"));
    if (!skip) throw new Error("未找到跳过简历按钮");
    await skip.trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-save"]').trigger("click");
    await flushPromises();
    if (wrapper.find('[data-testid="package-confirm-save"]').exists()) {
      await wrapper.get('[data-testid="package-confirm-save"]').trigger("click");
    }
    await flushPromises();

    const writes = packageWrites(fetchMock);
    const lastWrite = writes[writes.length - 1];
    expect(String(lastWrite[0])).toBe("/api/search-packages");
    expect(String((lastWrite[1] as RequestInit).method).toUpperCase()).toBe("POST");
  });

  it("配置包不可用时留在第一页并发出红色错误通知", async () => {
    const { wrapper } = await mountView({
      items: [{ id: "pkg-bad", name: "坏掉的配置" }],
      single: () => response(
        { error: { code: "package_unusable", message: "这套配置无法完整读取，请重新保存" } },
        409,
      ),
    });

    await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-item"]').trigger("click");
    await flushPromises();

    expect(wrapper.get(".upload-layout").isVisible()).toBe(true);
    expect((wrapper.get(".profile-summary-input").element as HTMLTextAreaElement).value)
      .not.toBe("3 年 B 端产品经验");
    const notices = wrapper.emitted("notify")?.flat() ?? [];
    const errorNotice = notices.find(
      (notice) => (notice as { tone?: string }).tone === "error",
    ) as { message: string } | undefined;
    expect(errorNotice?.message).toBe("这套配置无法完整读取，请重新保存");
  });

  it("列表加载失败时保留重试入口，重试成功后展示配置包", async () => {
    let attempt = 0;
    const { wrapper } = await mountView({
      listResponse: () => {
        attempt += 1;
        if (attempt === 1) {
          return response({ error: { code: "persistence_failed", message: "配置包保存失败，请重试" } }, 500);
        }
        return response({
          items: [{
            id: "pkg-1",
            name: "产品经理 · 上海",
            createdAt: "2026-09-21T10:00:00+08:00",
            updatedAt: "2026-09-21T10:00:00+08:00",
          }],
        });
      },
    });

    await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
    await flushPromises();

    expect(wrapper.get(".upload-layout").isVisible()).toBe(true);
    expect(wrapper.get('[data-testid="package-list-error"]').text()).toContain("配置包保存失败，请重试");
    const notices = wrapper.emitted("notify")?.flat() ?? [];
    expect(
      notices.some((notice) => (notice as { tone?: string }).tone === "error"),
    ).toBe(true);

    await wrapper.get('[data-testid="package-list-retry"]').trigger("click");
    await flushPromises();

    expect(wrapper.findAll('[data-testid="package-item"]')).toHaveLength(1);
    expect(wrapper.find('[data-testid="package-list-error"]').exists()).toBe(false);
  });

  it("删除配置包后，下一次保存仍创建新的配置", async () => {
    const { wrapper, fetchMock } = await mountView({
      items: [{ id: "pkg-1", name: "产品经理 · 上海" }],
    });
    await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-item"]').trigger("click");
    await flushPromises();
    // 回到第一页，在选择框内二次确认后删除当前包。
    await wrapper.findAll(".step-nav button")[0].trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-delete"]').trigger("click");
    await wrapper.get('[data-testid="package-delete-confirm"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="saved-package-close"]').trigger("click");

    // 回到第二页再保存：始终创建新的配置，不触碰已删除的 id。
    await wrapper.findAll(".step-nav button")[1].trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-save"]').trigger("click");
    await wrapper.get('[data-testid="package-confirm-save"]').trigger("click");
    await flushPromises();

    const writes = packageWrites(fetchMock);
    const lastWrite = writes[writes.length - 1];
    expect(String(lastWrite[0])).toBe("/api/search-packages");
    expect(String((lastWrite[1] as RequestInit).method).toUpperCase()).toBe("POST");
    expect(
      writes.some(([, init]) => String((init as RequestInit).method).toUpperCase() === "PUT"),
    ).toBe(false);
  });

  it("保存失败时给出可读错误通知且不留下当前配置名称", async () => {
    const { wrapper } = await mountView({
      saveResponse: () => response(
        { error: { code: "invalid_package", message: "关键词内容格式不正确" } },
        400,
      ),
    });
    await enterSearchStep(wrapper);

    await wrapper.get('[data-testid="package-save"]').trigger("click");
    await wrapper.get('[data-testid="package-confirm-save"]').trigger("click");
    await flushPromises();

    const notices = wrapper.emitted("notify")?.flat() ?? [];
    expect(
      notices.some(
        (notice) => (notice as { message?: string }).message === "关键词内容格式不正确",
      ),
    ).toBe(true);
    expect(wrapper.find('[data-testid="package-current-name"]').exists()).toBe(false);
  });

  // SPEC 046 Edge Cases 缺口补测（走真实路径，不测谓词返回值）：配置包恢复刻意不做
  // 逐平台预检（044：同一套包两个平台共用），不可用事实到提交入口的对话框准备阶段
  // 才取回。第一页选包 → 第二页点提交 → 必须被挡住并点名不可用平台（显示名，不吐
  // 内部平台码）；切到可用单平台仍能单独提交。
  it("配置包恢复后某平台被禁用新建任务：提交被挡住并点名不可用平台，可用单平台仍能单独提交", async () => {
    const baseFetch = makeFetchMock({ items: [{ id: "pkg-1", name: "产品经理 · 上海" }] });
    const launches: Array<Record<string, unknown>> = [];
    const flowStarts: Array<Record<string, unknown>> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url.includes("/api/filter-labels") && url.includes("platform=zhilian")) {
        return response({ ok: true, platform: "zhilian", schema_version: 1, enabled_for_new_tasks: false, fields: [] });
      }
      if (url === "/api/flows" && method === "POST") {
        flowStarts.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return response({ ok: false, error: { code: "platform_unavailable", message: "智联已停用，不能新建任务" } }, 503);
      }
      if (url === "/api/execute-search" && method === "POST") {
        launches.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return response({ ok: true, task_id: `package-gate-${launches.length}` });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: {
            keywords: ["产品经理"], scope_kind: "cities", cities: ["上海"],
            pages_per_combination: 3, combination_count: 1, planned_pages: 3,
            task_size: "small", scope_digest: "sha256:package-gate",
          },
          deduplicated: { keywords: ["产品经理"], cities: ["上海市"] },
        });
      }
      return baseFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, {
      props: { profileId: "profile-044" },
      global: { stubs: { Teleport: true } },
    });
    await flushPromises();

    await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="package-item"]').trigger("click");
    await flushPromises();
    expect(wrapper.get(".upload-layout").isVisible()).toBe(false);
    await wrapper.get('[data-testid="profile-confirm"]').trigger("click");
    await flushPromises();

    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    await flushPromises();

    // 挡住落在提交入口本身：主按钮禁用 + 页面点名不可用平台（显示名，不吐内部码）。
    const blocked = wrapper.get('[data-testid="parallel-platform-disabled-notice"]');
    expect(blocked.text()).toContain("智联");
    expect(blocked.text()).not.toMatch(/boss|zhilian/i);
    expect(wrapper.get('[data-testid="start-one-click"]').attributes("disabled")).toBeDefined();
    // 请求一个都没发出去：不许让用户等 503 才知道哪个平台不可用。
    expect(flowStarts).toHaveLength(0);
    expect(launches).toHaveLength(0);

    await wrapper.get('[data-testid="platform-segment-boss"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="start-one-click"]').trigger("click");
    await flushPromises();
    await flushPromises();
    expect(wrapper.get('[data-testid="one-click-confirm"]').attributes("disabled")).toBeUndefined();
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(flowStarts).toHaveLength(0);
    expect(launches.map((body) => body.platform)).toEqual(["boss"]);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });
});
