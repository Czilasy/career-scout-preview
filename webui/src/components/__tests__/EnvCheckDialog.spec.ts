import { flushPromises, mount } from "@vue/test-utils";
import EnvCheckDialog from "../EnvCheckDialog.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const envPayload = {
  ok: true,
  groups: [
    {
      id: "browser",
      name: "浏览器",
      items: [
        { id: "browsers", name: "Chromium 浏览器", status: "ok", detail: "找到 Chrome ✅", fix: null },
        { id: "cdp", name: "专用浏览器已启动", status: "fail", detail: "无法连接", fix: "启动专用浏览器: ..." },
        { id: "boss_login", name: "BOSS 登录状态", status: "fail", detail: "未登录", fix: "打开账号浏览器登录" },
      ],
    },
    {
      id: "ai",
      name: "AI",
      items: [
        { id: "ai_key", name: "AI Key 配置", status: "fail", detail: "未配置", fix: "打开 AI 设置" },
      ],
    },
    {
      id: "local",
      name: "本地环境",
      items: [
        { id: "data_dir", name: "数据目录可写", status: "ok", detail: "可写", fix: null },
      ],
    },
  ],
  active_account: "a",
  cooldowns: [
    { account_id: "a", platform: "boss", until: 9999999999, until_text: "08-08 12:00", reason: "操作频繁" },
  ],
  checked_at: 1785940000,
};

async function mountOpen(fetchMock: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(EnvCheckDialog, { props: { open: false } });
  await flushPromises();
  await wrapper.setProps({ open: true });
  await flushPromises();
  // 逐项点亮动画：等待所有 setTimeout 完成后再断言真实状态。
  await new Promise((resolve) => setTimeout(resolve, 1200));
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  setBuildIdentity(expectedBackendBuildHash);
});

describe("EnvCheckDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders three groups with per-item status marks and checked time", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") return response(envPayload);
      if (url === "/api/browser-accounts") return response({ accounts: [{ id: "a", name: "账号 A" }] });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.find('[data-testid="env-group-browser"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="env-group-ai"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="env-group-local"]').exists()).toBe(true);

    const okItem = wrapper.get('[data-testid="env-item-browsers"]');
    expect(okItem.text()).toContain("✅");
    expect(okItem.text()).toContain("找到 Chrome ✅");

    const failItem = wrapper.get('[data-testid="env-item-cdp"]');
    expect(failItem.text()).toContain("❌");
    expect(wrapper.get('[data-testid="env-check-time"]').text()).toContain("上次检查");
  });

  it("shows cooldown records with clear button", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") return response(envPayload);
      if (url === "/api/browser-accounts") return response({ accounts: [{ id: "a", name: "账号 A" }] });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    const cooldown = wrapper.get('[data-testid="env-cooldowns"]');
    expect(cooldown.text()).toContain("账号 A");
    expect(cooldown.text()).toContain("建议等待至 08-08 12:00");
    expect(cooldown.find("button").text()).toContain("解除冷却");
  });

  it("clears a cooldown after confirm and removes the row", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/env-check") return response(envPayload);
      if (url === "/api/browser-accounts") return response({ accounts: [{ id: "a", name: "账号 A" }] });
      if (url === "/api/cooldown/clear") return response({ ok: true });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    await wrapper.get('[data-testid="env-cooldowns"]').find("button").trigger("click");
    await flushPromises();

    const clearCall = fetchMock.mock.calls.find(([input]) => String(input) === "/api/cooldown/clear");
    expect(clearCall).toBeDefined();
    expect(JSON.parse(String((clearCall as unknown[])[1] && (clearCall as [unknown, RequestInit])[1].body))).toEqual({
      account_id: "a",
      platform: "boss",
    });
    expect(wrapper.find('[data-testid="env-cooldowns"]').exists()).toBe(false);
  });

  it("emits open-browser-accounts when clicking the login guidance fix", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") return response(envPayload);
      if (url === "/api/browser-accounts") return response({ accounts: [] });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    const bossItem = wrapper.get('[data-testid="env-item-boss_login"]');
    const fixButton = bossItem.find("button");
    expect(fixButton.text()).toContain("登录指引");
    await fixButton.trigger("click");
    expect(wrapper.emitted("open-browser-accounts")).toHaveLength(1);
  });

  it("runs the AI connectivity test on demand", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") return response(envPayload);
      if (url === "/api/browser-accounts") return response({ accounts: [] });
      if (url === "/api/ai-settings/test") return response({ ok: true, message: "ok" });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    await wrapper.get('[data-testid="env-ai-test"]').trigger("click");
    await flushPromises();

    const testCall = fetchMock.mock.calls.some(([input]) => String(input) === "/api/ai-settings/test");
    expect(testCall).toBe(true);
    expect(wrapper.text()).toContain("AI 连通性测试通过");
  });

  it("re-runs the check via the toolbar button", async () => {
    let calls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") {
        calls += 1;
        return response(envPayload);
      }
      if (url === "/api/browser-accounts") return response({ accounts: [] });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    expect(calls).toBe(1);

    await wrapper.get('[data-testid="env-check-rerun"]').trigger("click");
    await flushPromises();
    expect(calls).toBe(2);
  });
});