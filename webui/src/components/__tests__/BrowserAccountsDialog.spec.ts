import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import BrowserAccountsDialog from "../BrowserAccountsDialog.vue";

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
  const btn = wrapper.findAll("button").find((b) => b.text().includes(text));
  if (!btn) throw new Error(`button containing "${text}" not found`);
  return btn;
}

describe("BrowserAccountsDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders account platforms and defaults the open platform to boss", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: [dualAccount], active_account: "a" });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    const platforms = wrapper.get('[data-testid="account-platforms-a"]');
    expect(platforms.find('[data-platform="boss"]').text()).toContain("BOSS");
    expect(platforms.find('[data-platform="boss"]').text()).toContain("9222");
    expect(platforms.find('[data-platform="zhilian"]').text()).toContain("智联");
    expect(platforms.find('[data-platform="zhilian"]').text()).toContain("9223");

    const select = wrapper.get('[data-testid="open-platform-a"]').element as HTMLSelectElement;
    expect(select.value).toBe("boss");
  });

  it("sends the selected platform when opening the browser", async () => {
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

    await wrapper.get('[data-testid="open-platform-a"]').setValue("zhilian");
    await findButton(wrapper, "打开浏览器登录").trigger("click");
    await flushPromises();

    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/browser-accounts/a/open"));
    expect(call).toBeDefined();
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ platform: "zhilian" });
  });

  it("defaults the open platform to boss when the platform select is not changed", async () => {
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

    await findButton(wrapper, "打开浏览器登录").trigger("click");
    await flushPromises();

    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/browser-accounts/a/open"));
    expect(call).toBeDefined();
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ platform: "boss" });
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
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "删除").trigger("click");
    await flushPromises();

    expect(confirmSpy).toHaveBeenCalled();
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
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "删除").trigger("click");
    await flushPromises();

    const notice = wrapper.get('.browser-account-notice[data-tone="error"]');
    expect(notice.text()).toContain("当前有任务运行，无法删除账号");
  });
});
