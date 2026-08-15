import { flushPromises, mount } from "@vue/test-utils";
import BrowserAccountsDialog from "../BrowserAccountsDialog.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const dualAccount = {
  id: "a",
  name: "账号 A",
  platforms: { boss: { cdp_port: 9222 }, zhilian: { cdp_port: 9223 } },
};

const bossOnlyAccount = {
  id: "b",
  name: "账号 B",
  platforms: { boss: { cdp_port: 9222 } },
};

// 组件用 watch(props.open) 加载账号；初始 mount open=false 再切 true 才会触发。
async function mountOpen(fetchMock: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(BrowserAccountsDialog, { props: { open: false } });
  await flushPromises();
  await wrapper.setProps({ open: true });
  await flushPromises();
  return wrapper;
}

function findButton(wrapper: ReturnType<typeof mount>, text: string) {
  // 文字按钮按 text 匹配；图标按钮按 aria-label/title 匹配
  const btn = wrapper.findAll("button").find((b) =>
    b.text().includes(text)
    || (b.attributes("aria-label") || "").includes(text)
    || (b.attributes("title") || "").includes(text));
  if (!btn) throw new Error(`button containing "${text}" not found`);
  return btn;
}

// 本文件引用的 api 模块实例可能未被 setup.ts 验证过（vitest 模块实例隔离），逐用例重新验证
beforeEach(() => {
  setBuildIdentity(expectedBackendBuildHash);
});

describe("BrowserAccountsDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders account platforms with per-platform login badges and window buttons", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({
          accounts: [dualAccount],
          active_account: "a",
          login_states: {
            a: {
              boss: { state: "logged_in", at: Date.now() / 1000 },
              zhilian: { state: "not_logged_in", at: Date.now() / 1000 },
            },
          },
        });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    const platforms = wrapper.get('[data-testid="account-platforms-a"]');
    expect(platforms.find('[data-platform="boss"]').text()).toContain("BOSS");
    expect(platforms.find('[data-platform="zhilian"]').text()).toContain("智联");

    // 平台徽章：BOSS 已登录 / 智联未登录
    expect(wrapper.get('[data-testid="account-state-a-boss"]').text()).toBe("已登录");
    expect(wrapper.get('[data-testid="account-state-a-zhilian"]').text()).toBe("未登录");

    // 每个平台独立「打开」入口，不再有单按钮一次开全部
    expect(wrapper.get('[data-testid="open-boss-a"]').text()).toContain("打开");
    expect(wrapper.get('[data-testid="open-zhilian-a"]').text()).toContain("打开");
    expect(wrapper.find('[data-testid="open-browser-a"]').exists()).toBe(false);
  });

  it("shows the builtin profile account as 默认账号 and marks others as 非当前账号", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({
          accounts: [
            { id: "a", name: "账号A", builtin: true, platforms: { boss: { cdp_port: 9222 } } },
            { id: "b", name: "账号B", builtin: true, platforms: { boss: { cdp_port: 9222 } } },
          ],
          active_account: "a",
        });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.text()).toContain("默认账号");
    // 内置默认账号没有删除按钮，非当前账号有「非当前账号」标记
    const accountCards = wrapper.findAll(".browser-account-card");
    expect(accountCards[0].find('[data-testid="delete-a"]').exists()).toBe(false);
    expect(accountCards[1].text()).toContain("非当前账号");
  });

  it("shows 受限中 badge and 待刷新 for expired records", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({
          accounts: [dualAccount],
          active_account: "a",
          login_states: {
            a: {
              boss: { state: "restricted", at: Date.now() / 1000 - 16 * 60 },
              zhilian: { state: "restricted", at: Date.now() / 1000 },
            },
          },
        });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    // 新鲜受限记录 → 受限中；过期记录 → 待刷新
    expect(wrapper.get('[data-testid="account-state-a-boss"]').text()).toBe("待刷新");
    expect(wrapper.get('[data-testid="account-state-a-zhilian"]').text()).toBe("受限中");
  });

  it("shows 未使用过 for accounts without cache records", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: [dualAccount], active_account: "a" });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.get('[data-testid="account-state-a-boss"]').text()).toBe("未使用过");
  });

  it("marks the account as 待刷新 after switching to it", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({
          accounts: [bossOnlyAccount, dualAccount],
          active_account: "a",
          login_states: { b: { boss: { state: "logged_in", at: Date.now() / 1000 } } },
        });
      }
      if (url.endsWith("/api/browser-accounts/b/activate")) {
        return response({ active_account: "b" });
      }
      return response({ init });
    });

    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "设为当前账号").trigger("click");
    await flushPromises();

    // 切换后账号 b 的徽章标记为待刷新，直到后端有新鲜探测记录
    expect(wrapper.get('[data-testid="account-state-b-boss"]').text()).toBe("待刷新");
  });

  it("open button sends one request for the clicked platform only", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: [dualAccount], active_account: "a" });
      }
      if (url.endsWith("/api/browser-accounts/a/open")) {
        return response({ message: "已打开" });
      }
      return response({ init });
    });

    const wrapper = await mountOpen(fetchMock);

    await wrapper.get('[data-testid="open-boss-a"]').trigger("click");
    await flushPromises();

    let calls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/api/browser-accounts/a/open"));
    expect(calls.length).toBe(1);
    expect(JSON.parse(String(calls[0]?.[1]?.body))).toEqual({ platform: "boss" });

    await wrapper.get('[data-testid="open-zhilian-a"]').trigger("click");
    await flushPromises();

    calls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/api/browser-accounts/a/open"));
    expect(calls.length).toBe(2);
    expect(JSON.parse(String(calls[1]?.[1]?.body))).toEqual({ platform: "zhilian" });
  });

  it("single-platform account sends only that platform", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: [bossOnlyAccount], active_account: "a" });
      }
      if (url.endsWith("/api/browser-accounts/b/open")) {
        return response({ message: "已打开" });
      }
      return response({ init });
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.find('[data-testid="open-zhilian-b"]').exists()).toBe(false);
    await wrapper.get('[data-testid="open-boss-b"]').trigger("click");
    await flushPromises();

    const calls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/api/browser-accounts/b/open"));
    expect(calls.length).toBe(1);
    expect(JSON.parse(String(calls[0]?.[1]?.body))).toEqual({ platform: "boss" });
  });

  it("disables non-locked platform while a paused task locks one platform", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({
          accounts: [dualAccount],
          active_account: "a",
          busy: true,
          busy_kind: "paused",
          locked_account: "a",
          locked_platform: "boss",
        });
      }
      return response({ init });
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.get('[data-testid="open-boss-a"]').attributes("disabled")).toBeUndefined();
    expect(wrapper.get('[data-testid="open-zhilian-a"]').attributes("disabled")).toBeDefined();
  });

  it("activate does not send a platform in the request body", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        // b 不是 active，才会渲染「设为当前账号」按钮
        return response({ accounts: [bossOnlyAccount], active_account: "a" });
      }
      if (url.endsWith("/api/browser-accounts/b/activate")) {
        return response({ active_account: "b" });
      }
      return response({ init });
    });

    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "设为当前账号").trigger("click");
    await flushPromises();

    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/browser-accounts/b/activate"));
    expect(call).toBeDefined();
    // activate 不带 body / json（http-api.md L323：平台不属于 activate 状态）
    expect(call?.[1]?.body).toBeUndefined();
    const headers = new Headers(call?.[1]?.headers);
    expect(headers.get("Content-Type")).toBeNull();
  });

  it("delete surfaces dual-platform occupancy from the 409 error details", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: [bossOnlyAccount], active_account: "a" });
      }
      if (url.endsWith("/api/browser-accounts/b") && init?.method === "DELETE") {
        return response({
          ok: false,
          error_code: "browser_in_use",
          user_message: "该账号在两个平台均被占用，无法删除",
          details: {
            locked_platform: "boss",
            locked_run_id: "run-xyz",
            conflicting_platform: "zhilian",
          },
        }, 409);
      }
      return response({ init });
    });
    const confirmSpy = vi.spyOn(window, "confirm");

    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "删除").trigger("click");
    await findButton(wrapper, "确认删除").trigger("click");
    await flushPromises();

    expect(confirmSpy).not.toHaveBeenCalled();
    const notice = wrapper.get('.browser-account-notice[data-tone="error"]');
    // 主文案 + 双平台占用细节（http-api.md L328/L332）
    expect(notice.text()).toContain("该账号在两个平台均被占用，无法删除");
    expect(notice.text()).toContain("BOSS");
    expect(notice.text()).toContain("run-xyz");
    expect(notice.text()).toContain("智联");
    expect(notice.text()).toContain("未知 profile");
  });

  it("delete falls back to user_message when details carry no platform fields", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: [bossOnlyAccount], active_account: "a" });
      }
      if (url.endsWith("/api/browser-accounts/b") && init?.method === "DELETE") {
        return response({
          ok: false,
          error_code: "browser_busy",
          user_message: "当前有任务运行，无法删除账号",
          details: {},
        }, 409);
      }
      return response({ init });
    });
    const confirmSpy = vi.spyOn(window, "confirm");

    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "删除").trigger("click");
    await findButton(wrapper, "确认删除").trigger("click");
    await flushPromises();

    const notice = wrapper.get('.browser-account-notice[data-tone="error"]');
    expect(notice.text()).toContain("当前有任务运行，无法删除账号");
    expect(confirmSpy).not.toHaveBeenCalled();
  });


  it("cancel on the in-app delete confirm performs no action", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: [bossOnlyAccount], active_account: "a" });
      }
      if (url.endsWith("/api/browser-accounts/b") && init?.method === "DELETE") {
        return response({ ok: true });
      }
      return response({ init });
    });

    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "删除").trigger("click");
    expect(wrapper.find('[data-testid="delete-account-confirm"]').exists()).toBe(true);
    await wrapper.get('[data-testid="delete-account-cancel"]').trigger("click");
    await flushPromises();

    const deleteCall = fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/api/browser-accounts/b") && init?.method === "DELETE");
    expect(deleteCall).toBe(false);
    expect(wrapper.find('[data-testid="delete-account-confirm"]').exists()).toBe(false);
    expect(confirmSpy).not.toHaveBeenCalled();
  });
});
