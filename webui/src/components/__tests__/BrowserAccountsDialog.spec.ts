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
            { id: "b", name: "账号B", builtin: false, platforms: { boss: { cdp_port: 9222 } } },
          ],
          active_account: "a",
        });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.text()).toContain("默认账号");
    // 只有默认账号没有删除按钮；账号 B 与自定义账号保留删除入口
    const accountCards = wrapper.findAll(".browser-account-card");
    expect(accountCards[0].find('[data-testid="delete-a"]').exists()).toBe(false);
    expect(accountCards[1].text()).toContain("非当前账号");
    expect(accountCards[1].find('[data-testid="delete-b"]').exists()).toBe(true);
  });

  it("shows 受限中 badge and 上次结果 · 待刷新 for stale records", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({
          accounts: [dualAccount],
          active_account: "a",
          login_states: {
            a: {
              boss: { state: "logged_in", at: Date.now() / 1000 - 16 * 60 },
              zhilian: { state: "restricted", at: Date.now() / 1000 },
            },
          },
        });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    // 过期记录保留上次结果并标注待刷新；新鲜受限记录 → 受限中
    expect(wrapper.get('[data-testid="account-state-a-boss"]').text()).toBe("已登录 · 待刷新");
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

    // 切换后账号 b 的徽章保留上次结果并标注待刷新，直到后端有新鲜探测记录
    expect(wrapper.get('[data-testid="account-state-b-boss"]').text()).toBe("已登录 · 待刷新");
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

  it("enables every account and platform while a paused task is active", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({
          accounts: [bossOnlyAccount, dualAccount],
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
    expect(wrapper.get('[data-testid="open-zhilian-a"]').attributes("disabled")).toBeUndefined();
    expect(wrapper.get('[data-testid="open-boss-b"]').attributes("disabled")).toBeUndefined();
    expect(wrapper.get('[data-testid="activate-b"]').attributes("disabled")).toBeUndefined();
    expect(wrapper.get('[data-testid="delete-b"]').attributes("disabled")).toBeUndefined();
    const notice = wrapper.get(".browser-account-notice");
    expect(notice.text()).toContain("有暂停任务，可切换账号");
    expect(notice.text()).not.toContain("请先结束任务");
  });

  it("paused switching account does not open any platform browser", async () => {
    const openCalls: Array<string> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({
          accounts: [bossOnlyAccount, dualAccount],
          active_account: "a",
          busy: true,
          busy_kind: "paused",
          locked_account: "a",
          locked_platform: "boss",
        });
      }
      if (url.endsWith("/api/browser-accounts/b/activate")) {
        return response({ active_account: "b" });
      }
      if (url.includes("/open")) openCalls.push(url);
      return response({ init });
    });

    const wrapper = await mountOpen(fetchMock);
    await findButton(wrapper, "设为当前账号").trigger("click");
    await flushPromises();

    const activateCalls = fetchMock.mock.calls.filter(
      ([url]) => String(url).endsWith("/api/browser-accounts/b/activate"),
    );
    expect(activateCalls.length).toBe(1);
    expect(openCalls).toEqual([]);
  });

  it("keeps open and manage disabled while running or queued", async () => {
    for (const busyKind of ["running", "queued"]) {
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/browser-accounts") {
          return response({
            accounts: [bossOnlyAccount, dualAccount],
            active_account: "a",
            busy: true,
            busy_kind: busyKind,
          });
        }
        return response({});
      });
      const wrapper = await mountOpen(fetchMock);

      expect(wrapper.get('[data-testid="open-boss-a"]').attributes("disabled")).toBeDefined();
      expect(wrapper.get('[data-testid="open-zhilian-a"]').attributes("disabled")).toBeDefined();
      expect(wrapper.get('[data-testid="open-boss-b"]').attributes("disabled")).toBeDefined();
      expect(wrapper.get('[data-testid="activate-b"]').attributes("disabled")).toBeDefined();
      expect(wrapper.get('[data-testid="delete-b"]').attributes("disabled")).toBeDefined();
      expect(wrapper.get(".browser-account-notice").text()).toContain("请先结束或取消任务后再操作");
      await wrapper.unmount();
    }
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

describe("BrowserAccountsDialog role assignment (B073)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const accA = { id: "a", name: "账号 A", platforms: { boss: { cdp_port: 9222 } } };
  const accB = { id: "b", name: "账号 B", platforms: { boss: { cdp_port: 9222 } } };

  // 模拟后端互斥语义：PUT /roles 后其他账号的相同角色被清除。
  function rolesFetchMock(initial: Array<{ id: string; name: string }> = [accA, accB]) {
    let accounts = initial.map((a) => ({ ...a, roles: [] as string[] }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts, active_account: "a" });
      }
      const rolesMatch = /\/api\/browser-accounts\/([^/]+)\/roles$/.exec(url);
      if (rolesMatch && init?.method === "PUT") {
        const body = JSON.parse(String(init?.body)) as { roles: string[] };
        const id = rolesMatch[1];
        accounts = accounts.map((account) => {
          if (account.id === id) return { ...account, roles: body.roles };
          return { ...account, roles: (account.roles || []).filter((r) => !body.roles.includes(r)) };
        });
        return response({ ok: true, account_id: id, roles: body.roles });
      }
      return response({});
    });
    return fetchMock;
  }

  it("renders the two role chips in 不指定 state", async () => {
    const wrapper = await mountOpen(rolesFetchMock());
    const r1 = wrapper.get('[data-testid="role-chip-R1"]');
    expect(r1.text()).toContain("R1");
    // 描述字降级为悬停提示（与抓取浏览器 chip 同一行）
    expect(r1.attributes("title")).toContain("列表/广泛抓取");
    expect(r1.text()).toContain("不指定");
    const r2 = wrapper.get('[data-testid="role-chip-R2"]');
    expect(r2.text()).toContain("R2");
    expect(r2.attributes("title")).toContain("详情抓取");
    expect(r2.text()).toContain("不指定");
  });

  it("keeps the 不指定 option visible even before any role is assigned", async () => {
    const wrapper = await mountOpen(rolesFetchMock());
    await wrapper.get('[data-testid="role-chip-R1"]').trigger("click");
    const menu = wrapper.get('[data-testid="role-menu"]');
    expect(menu.text()).toContain("不指定");
  });

  it("assigns a role via PUT and shows the account name on the chip", async () => {
    const fetchMock = rolesFetchMock();
    const wrapper = await mountOpen(fetchMock);

    await wrapper.get('[data-testid="role-chip-R1"]').trigger("click");
    const menu = wrapper.get('[data-testid="role-menu"]');
    expect(menu.text()).toContain("账号 A");
    expect(menu.text()).toContain("账号 B");

    await wrapper.get('[data-testid="role-option-R1-b"]').trigger("click");
    await flushPromises();

    const putCall = fetchMock.mock.calls.find(([u, i]) => String(u).endsWith("/api/browser-accounts/b/roles"));
    expect(putCall).toBeTruthy();
    expect(JSON.parse(String(putCall![1]!.body))).toEqual({ roles: ["R1"] });
    expect(wrapper.get('[data-testid="role-chip-R1"]').text()).toContain("账号 B");
    expect(wrapper.get('[data-testid="role-chip-R2"]').text()).toContain("不指定");
  });

  it("allows the same account to hold both R1 and R2", async () => {
    const fetchMock = rolesFetchMock();
    const wrapper = await mountOpen(fetchMock);

    await wrapper.get('[data-testid="role-chip-R1"]').trigger("click");
    await wrapper.get('[data-testid="role-option-R1-b"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="role-chip-R2"]').trigger("click");
    await wrapper.get('[data-testid="role-option-R2-b"]').trigger("click");
    await flushPromises();

    const puts = fetchMock.mock.calls.filter(([u, i]) => String(u).endsWith("/roles") && i?.method === "PUT");
    expect(puts.length).toBe(2);
    expect(wrapper.get('[data-testid="role-chip-R1"]').text()).toContain("账号 B");
    expect(wrapper.get('[data-testid="role-chip-R2"]').text()).toContain("账号 B");
  });

  it("moves the role exclusively when another account is chosen", async () => {
    const fetchMock = rolesFetchMock();
    const wrapper = await mountOpen(fetchMock);

    await wrapper.get('[data-testid="role-chip-R1"]').trigger("click");
    await wrapper.get('[data-testid="role-option-R1-b"]').trigger("click");
    await flushPromises();
    // 换选账号 A → PUT a 的 R1；b 的 R1 被后端互斥清除
    await wrapper.get('[data-testid="role-chip-R1"]').trigger("click");
    await wrapper.get('[data-testid="role-option-R1-a"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="role-chip-R1"]').text()).toContain("账号 A");
    const putB = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/browser-accounts/b/roles"));
    const putA = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/browser-accounts/a/roles"));
    expect(JSON.parse(String(putB![1]!.body))).toEqual({ roles: ["R1"] });
    expect(JSON.parse(String(putA![1]!.body))).toEqual({ roles: ["R1"] });
  });

  it("clears a role back to 不指定", async () => {
    const fetchMock = rolesFetchMock();
    const wrapper = await mountOpen(fetchMock);

    await wrapper.get('[data-testid="role-chip-R1"]').trigger("click");
    await wrapper.get('[data-testid="role-option-R1-b"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="role-chip-R1"]').trigger("click");
    await wrapper.get('[data-testid="role-clear-R1"]').trigger("click");
    await flushPromises();

    const putCalls = fetchMock.mock.calls.filter(
      ([u, i]) => String(u).endsWith("/api/browser-accounts/b/roles") && i?.method === "PUT",
    );
    expect(JSON.parse(String(putCalls.at(-1)![1]!.body))).toEqual({ roles: [] });
    expect(wrapper.get('[data-testid="role-chip-R1"]').text()).toContain("不指定");
  });

  it("shows an empty hint when there are no accounts", async () => {
    const wrapper = await mountOpen(rolesFetchMock([]));
    await wrapper.get('[data-testid="role-chip-R1"]').trigger("click");
    expect(wrapper.get('[data-testid="role-menu"]').text()).toContain("暂无账号");
  });
});

describe("BrowserAccountsDialog kernel chip（抓取浏览器合并）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const accA = { id: "a", name: "账号 A", platforms: { boss: { cdp_port: 9222 } } };

  const registryPayload = {
    registry: [
      { key: "chrome", name: "Chrome", installed: true, path: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" },
      { key: "edge", name: "Edge", installed: true, path: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" },
    ],
    selection: { mode: "registry", key: "chrome" },
    effective_path: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  };

  it("shows the current browser on the chip and expands the picker", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: [accA], active_account: "a" });
      }
      if (url === "/api/browser-registry") return response(registryPayload);
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    const chip = wrapper.get('[data-testid="browser-kernel-chip"]');
    expect(chip.text()).toContain("抓取浏览器");
    expect(chip.text()).toContain("Chrome");
    expect(chip.attributes("title")).toContain("chrome.exe");

    await chip.trigger("click");
    expect(wrapper.find('[data-testid="browser-kernel-picker"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="browser-effective-path"]').text()).toContain("chrome.exe");

    // 悬浮窗：点浮层外收起
    document.dispatchEvent(new Event("pointerdown"));
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="browser-kernel-popover"]').exists()).toBe(false);
  });

  it("falls back to 自动探测 when the registry endpoint returns nothing", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/browser-accounts") {
        return response({ accounts: [accA], active_account: "a" });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.get('[data-testid="browser-kernel-chip"]').text()).toContain("自动探测");
  });

  it("locks the kernel chip while a task is running and allows it while paused", async () => {
    for (const [busyKind, locked] of [["running", true], ["paused", false]] as const) {
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/browser-accounts") {
          return response({ accounts: [accA], active_account: "a", busy: true, busy_kind: busyKind });
        }
        return response({});
      });

      const wrapper = await mountOpen(fetchMock);
      const chip = wrapper.get('[data-testid="browser-kernel-chip"]');
      // disabled 属性渲染为空串，必须按存在性断言
      if (locked) expect(chip.attributes("disabled")).toBeDefined();
      else expect(chip.attributes("disabled")).toBeUndefined();
      await wrapper.unmount();
    }
  });
});
