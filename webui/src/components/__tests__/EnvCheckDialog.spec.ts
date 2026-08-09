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
    { account_id: "a", platform: "boss", until: 9999999999, until_text: "08-08 12:00", reason: "操作频繁", from_run: "run-123" },
  ],
  checked_at: 1785940000,
};

// EXE 模式响应：runtime_mode="exe"；local 组 deps 为「内置运行时」差异文案，新增 webview2 项。
// 依据 specs/003-desktop-exe/contracts/runtime-mode.md §2.2。
const exePayload = {
  ok: true,
  runtime_mode: "exe",
  groups: [
    {
      id: "browser",
      name: "浏览器",
      items: [
        { id: "browsers", name: "Chromium 浏览器", status: "ok", detail: "找到 Chrome ✅", fix: null },
      ],
    },
    {
      id: "local",
      name: "本地环境",
      items: [
        { id: "deps", name: "内置运行时", status: "ok", detail: "Python 运行时与依赖已内置，无需安装", fix: null },
        { id: "webview2", name: "WebView2 运行时", status: "ok", detail: "已安装", fix: null },
        { id: "data_dir", name: "数据目录可写", status: "ok", detail: "可写", fix: null },
      ],
    },
  ],
  active_account: "",
  cooldowns: [],
  checked_at: 1785940000,
};

// 源码模式响应：runtime_mode="source"；local 组 deps 保持源码模式文案，无 webview2 项（合同 §2.3）。
const sourcePayload = {
  ok: true,
  runtime_mode: "source",
  groups: [
    {
      id: "local",
      name: "本地环境",
      items: [
        { id: "deps", name: "Python 依赖", status: "ok", detail: "已安装", fix: null },
        { id: "data_dir", name: "数据目录可写", status: "ok", detail: "可写", fix: null },
      ],
    },
  ],
  active_account: "",
  cooldowns: [],
  checked_at: 1785940000,
};

async function mountOpen(fetchMock: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(EnvCheckDialog, { props: { open: false } });
  await flushPromises();
  await wrapper.setProps({ open: true });
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

  it("shows every real status at once without staged pending marks", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") return response(envPayload);
      if (url === "/api/browser-accounts") return response({ accounts: [] });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.text()).not.toContain("⏳");
    expect(wrapper.find('.env-check-mark[data-state="pending"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="env-item-browsers"]').text()).toContain("✅");
    expect(wrapper.get('[data-testid="env-item-cdp"]').text()).toContain("❌");
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("cancel on the in-app cooldown confirm performs no action", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") return response(envPayload);
      if (url === "/api/browser-accounts") return response({ accounts: [{ id: "a", name: "账号 A" }] });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    await wrapper.get('[data-testid="env-cooldowns"]').find("button").trigger("click");
    await wrapper.get('[data-testid="cooldown-confirm-cancel"]').trigger("click");
    await flushPromises();

    const clearCall = fetchMock.mock.calls.some(([input]) => String(input) === "/api/cooldown/clear");
    expect(clearCall).toBe(false);
    expect(wrapper.find('[data-testid="env-cooldowns"]').exists()).toBe(true);
    expect(confirmSpy).not.toHaveBeenCalled();
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
    expect(wrapper.get('[data-testid="cooldown-from-run"]').text()).toContain("run-123");
    expect(cooldown.find("button").text()).toContain("解除冷却");
  });

  it("clears a cooldown after confirm and removes the row", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/env-check") return response(envPayload);
      if (url === "/api/browser-accounts") return response({ accounts: [{ id: "a", name: "账号 A" }] });
      if (url === "/api/cooldown/clear") return response({ ok: true });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    await wrapper.get('[data-testid="env-cooldowns"]').find("button").trigger("click");
    expect(wrapper.find('[data-testid="cooldown-confirm-ok"]').exists()).toBe(true);
    await flushPromises();
    await wrapper.get('[data-testid="cooldown-confirm-ok"]').trigger("click");
    await flushPromises();

    const clearCall = fetchMock.mock.calls.find(([input]) => String(input) === "/api/cooldown/clear");
    expect(clearCall).toBeDefined();
    expect(JSON.parse(String((clearCall as unknown[])[1] && (clearCall as [unknown, RequestInit])[1].body))).toEqual({
      account_id: "a",
      platform: "boss",
    });
    expect(wrapper.find('[data-testid="env-cooldowns"]').exists()).toBe(false);
    expect(confirmSpy).not.toHaveBeenCalled();
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

  it("renders exe mode deps as built-in runtime and webview2 item", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") return response(exePayload);
      if (url === "/api/browser-accounts") return response({ accounts: [] });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    // runtime_mode 读取：exe 模式下容器暴露 data-runtime-mode="exe"（可测试性钩子，不可见）。
    expect(wrapper.find(".env-check-groups").attributes("data-runtime-mode")).toBe("exe");

    // deps 项：名称「内置运行时」、状态 ok（✅）、差异 detail、无修复按钮（fix=null）。
    const deps = wrapper.get('[data-testid="env-item-deps"]');
    expect(deps.text()).toContain("内置运行时");
    expect(deps.text()).toContain("✅");
    expect(deps.text()).toContain("Python 运行时与依赖已内置，无需安装");
    expect(deps.find("button").exists()).toBe(false);

    // webview2 项按通用 CheckItem 渲染（ok → ✅）。
    const webview2 = wrapper.get('[data-testid="env-item-webview2"]');
    expect(webview2.text()).toContain("✅");
  });

  it("renders webview2 fail without fix button for install guidance", async () => {
    // EXE 模式下 webview2 缺失：fix 文案含「安装 WebView2 运行时」，遵循现有 fixAction 策略不生成按钮。
    const exeFailPayload = {
      ...exePayload,
      groups: [
        { id: "browser", name: "浏览器", items: [] },
        {
          id: "local",
          name: "本地环境",
          items: [
            { id: "deps", name: "内置运行时", status: "ok", detail: "Python 运行时与依赖已内置，无需安装", fix: null },
            {
              id: "webview2",
              name: "WebView2 运行时",
              status: "fail",
              detail: "未检测到 WebView2 运行时，请安装 Microsoft Edge WebView2",
              fix: "安装 WebView2 运行时（下载地址见环境检查提示）",
            },
          ],
        },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") return response(exeFailPayload);
      if (url === "/api/browser-accounts") return response({ accounts: [] });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    const webview2 = wrapper.get('[data-testid="env-item-webview2"]');
    expect(webview2.text()).toContain("❌");
    // 现有 fixAction 仅识别三类修复动作；「安装 WebView2 运行时」不在其中 → 不生成按钮（合同 §4）。
    expect(webview2.find("button").exists()).toBe(false);
  });

  it("renders source mode without webview2 and marks runtime-mode source", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") return response(sourcePayload);
      if (url === "/api/browser-accounts") return response({ accounts: [] });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.find(".env-check-groups").attributes("data-runtime-mode")).toBe("source");
    // 源码模式不返回 webview2 项（合同 §2.3）。
    expect(wrapper.find('[data-testid="env-item-webview2"]').exists()).toBe(false);
    // deps 保持源码模式文案，非「内置运行时」。
    const deps = wrapper.get('[data-testid="env-item-deps"]');
    expect(deps.text()).toContain("Python 依赖");
    expect(deps.text()).not.toContain("内置运行时");
  });

  it("defaults missing runtime_mode to source and keeps legacy rendering", async () => {
    // envPayload 无 runtime_mode 字段（模拟源码模式/旧后端），前端应默认 source 且既有渲染不变。
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/env-check") return response(envPayload);
      if (url === "/api/browser-accounts") return response({ accounts: [{ id: "a", name: "账号 A" }] });
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.find(".env-check-groups").attributes("data-runtime-mode")).toBe("source");
    // 既有渲染零回归：三组齐全、browsers ✅、cdp ❌、无 webview2。
    expect(wrapper.find('[data-testid="env-group-browser"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="env-group-ai"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="env-group-local"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="env-item-browsers"]').text()).toContain("✅");
    expect(wrapper.get('[data-testid="env-item-cdp"]').text()).toContain("❌");
    expect(wrapper.find('[data-testid="env-item-webview2"]').exists()).toBe(false);
  });
});
