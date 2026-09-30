import { flushPromises, mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import path from "node:path";
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
    expect(wrapper.get('[data-testid="open-boss-a"]').text()).toBe("");
    expect(wrapper.get('[data-testid="open-boss-a"]').find("svg").exists()).toBe(true);
    expect(wrapper.get('[data-testid="open-zhilian-a"]').text()).toBe("");
    expect(wrapper.get('[data-testid="open-zhilian-a"]').find("svg").exists()).toBe(true);
    expect(wrapper.find('[data-testid="open-browser-a"]').exists()).toBe(false);
  });

  it("labels the current account row and leaves the other rows without the label", async () => {
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
    expect(accountCards[0].find(".account-sheet-icon").exists()).toBe(false);
    // 当前账号那一行必须写得出「当前账号」，不再靠"哪行没有按钮"反推
    expect(accountCards[0].text()).toContain("当前账号");
    expect(accountCards[1].text()).not.toContain("非当前账号");
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
    expect(notice.text()).not.toContain("切换后继续");
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

  // Spec 038 B091：模拟后端 pool 配置端点（PUT /api/browser-accounts/<id>/pool）。
  // 默认每账号都进池、默认全选、默认配额取中值（R1 25 / R2 150）。
  function poolFetchMock(
    initial: Array<{ id: string; name: string }> = [accA, accB],
    rateLimited: string[] = [],
  ) {
    let accounts = initial.map((a, i) => ({
      ...a,
      pool: { selected: true, order: i, r1_quota: 25, r2_quota: 150 },
      rate_limited: rateLimited.includes(a.id),
    }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts, active_account: "a" });
      }
      const poolMatch = /\/api\/browser-accounts\/([^/]+)\/pool$/.exec(url);
      if (poolMatch && init?.method === "PUT") {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        const id = poolMatch[1];
        accounts = accounts.map((account) => {
          if (account.id !== id) return account;
          const next = { ...account };
          if (typeof body.selected === "boolean") {
            next.pool = { ...next.pool, selected: body.selected };
          }
          if (typeof body.order === "number") {
            next.pool = { ...next.pool, order: body.order };
          }
          if (typeof body.r1_quota === "number") {
            next.pool = { ...next.pool, r1_quota: body.r1_quota };
          }
          if (typeof body.r2_quota === "number") {
            next.pool = { ...next.pool, r2_quota: body.r2_quota };
          }
          return next;
        });
        return response({ ok: true, account_id: id, pool: accounts.find((a) => a.id === id)!.pool });
      }
      return response({});
    });
    return fetchMock;
  }

  it("renders every account with default pool selection and quota", async () => {
    const wrapper = await mountOpen(poolFetchMock());
    const cbA = wrapper.get('[data-testid="pool-selected-a"]');
    const cbB = wrapper.get('[data-testid="pool-selected-b"]');
    expect((cbA.element as HTMLInputElement).checked).toBe(true);
    expect((cbB.element as HTMLInputElement).checked).toBe(true);
    // 默认配额 25/150
    expect((wrapper.get('[data-testid="pool-r1-quota-a"]').element as HTMLInputElement).value).toBe("25");
    expect((wrapper.get('[data-testid="pool-r2-quota-a"]').element as HTMLInputElement).value).toBe("150");
  });

  it("toggles pool selected via PUT /pool", async () => {
    const fetchMock = poolFetchMock();
    const wrapper = await mountOpen(fetchMock);
    await wrapper.get('[data-testid="pool-selected-b"]').trigger("change");
    await flushPromises();
    const putCall = fetchMock.mock.calls.find(([u, i]) =>
      String(u).endsWith("/api/browser-accounts/b/pool") && i?.method === "PUT");
    expect(putCall).toBeTruthy();
    expect(JSON.parse(String(putCall![1]!.body))).toEqual({ selected: false });
  });

  it("puts a reselected account at the end of the checkbox order", async () => {
    const fetchMock = poolFetchMock();
    const wrapper = await mountOpen(fetchMock);
    await wrapper.get('[data-testid="pool-selected-b"]').trigger("change");
    await flushPromises();
    await wrapper.get('[data-testid="pool-selected-b"]').trigger("change");
    await flushPromises();
    const putCalls = fetchMock.mock.calls.filter(([u, i]) =>
      String(u).endsWith("/api/browser-accounts/b/pool") && i?.method === "PUT");
    expect(putCalls).toHaveLength(2);
    expect(JSON.parse(String(putCalls[1]![1]!.body))).toEqual({ selected: true, order: 2 });
  });

  it("updates r1 quota via PUT /pool", async () => {
    const fetchMock = poolFetchMock();
    const wrapper = await mountOpen(fetchMock);
    const input = wrapper.get('[data-testid="pool-r1-quota-a"]');
    await input.setValue("10");
    await input.trigger("change");
    await flushPromises();
    const putCall = fetchMock.mock.calls.find(([u, i]) =>
      String(u).endsWith("/api/browser-accounts/a/pool") && i?.method === "PUT");
    expect(putCall).toBeTruthy();
    expect(JSON.parse(String(putCall![1]!.body))).toEqual({ r1_quota: 10 });
  });

  it("shows rate-limited badge when account.rate_limited is true", async () => {
    // 通过工厂参数标记 b 撞墙限流：按 URL 应答、不依赖调用顺序，
    // 消除模块级 session 缓存预热差异导致的单跑必挂。
    const fetchMock = poolFetchMock([accA, accB], ["b"]);
    const wrapper = await mountOpen(fetchMock);
    const badge = wrapper.get('[data-testid="rate-limited-b"]');
    expect(badge.text()).toContain("限流");
    // 撞墙账号名变红（card 上 data-rate-limited="true"）
    const card = wrapper.find('.browser-account-card[data-rate-limited="true"]');
    expect(card.exists()).toBe(true);
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

describe("BrowserAccountsDialog compact account sheet (B091 V3)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("keeps the existing dialog context inside the compact account sheet", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/browser-accounts") {
        return response({ accounts: [dualAccount], active_account: "a" });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.text()).toContain("每个账号使用独立的浏览器环境");
    expect(wrapper.find('[data-testid="browser-kernel-chip"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="account-sheet-header"]').text()).toContain("账号池");
    expect(wrapper.find('[data-testid="pool-selected-a"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="account-platforms-a"]').text()).toContain("BOSS");
    expect(findButton(wrapper, "添加账号").exists()).toBe(true);
  });

  it("uses a wider panel and keeps account-pool column labels aligned", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/browser-accounts") {
        return response({ accounts: [dualAccount], active_account: "a" });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    const panel = wrapper.get(".dialog-panel");
    const header = wrapper.get('[data-testid="account-sheet-header"]');

    expect(panel.classes()).toContain("dialog-account");
    expect(header.find('[data-testid="account-sheet-column-account"]').text()).toBe("账号");
    expect(header.find('[data-testid="account-sheet-column-pool"]').text()).toBe("轮询与配额");
    expect(header.find('[data-testid="account-sheet-column-platform"]').text()).toBe("平台");
    expect(header.find('[data-testid="account-sheet-column-actions"]').text()).toBe("操作");
    expect(header.find(".account-sheet-columns").classes()).toContain("account-sheet-columns");
    expect(header.find(".account-sheet-header").classes()).toContain("account-sheet-sticky-header");
  });

  it("stacks R1 and R2 quotas like the platform rows", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/browser-accounts") {
        return response({ accounts: [dualAccount], active_account: "a" });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    const quotas = wrapper.get('[data-testid="pool-config-a"]');
    const rows = quotas.findAll(".account-sheet-quota-row");

    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("R1");
    expect(rows[1].text()).toContain("R2");
    expect(rows[0].find("input").classes()).toContain("account-sheet-quota-input");
  });

  it("uses an R2 default of 150 and accepts values through 300", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/browser-accounts") {
        return response({ accounts: [dualAccount], active_account: "a" });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    const input = wrapper.get('[data-testid="pool-r2-quota-a"]');

    expect((input.element as HTMLInputElement).value).toBe("150");
    expect(input.attributes("min")).toBe("1");
    expect(input.attributes("max")).toBe("300");
    expect(input.attributes("placeholder")).toBe("1-300");
  });

  it("lets R1 and R2 inputs fill the available quota column", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/browser-accounts") {
        return response({ accounts: [dualAccount], active_account: "a" });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    const inputs = wrapper.get('[data-testid="pool-config-a"]').findAll("input");

    expect(inputs).toHaveLength(2);
    expect(inputs[0].classes()).toContain("account-sheet-quota-input-fill");
    expect(inputs[1].classes()).toContain("account-sheet-quota-input-fill");
  });

  it("clears only the rate-limit marker and reloads the account", async () => {
    let accounts = [
      {
        id: "b",
        name: "账号 B",
        platforms: { boss: { cdp_port: 9222 }, zhilian: { cdp_port: 9223 } },
        pool: { selected: true, order: 0, r1_quota: 25, r2_quota: 150 },
        rate_limited: true,
      },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts, active_account: "a" });
      }
      if (url === "/api/browser-accounts/b/rate-limited" && init?.method === "DELETE") {
        accounts = accounts.map((account) => ({ ...account, rate_limited: false }));
        return response({ ok: true, account_id: "b", rate_limited: false });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    const clear = wrapper.get('[data-testid="clear-rate-limited-b"]');
    expect(clear.attributes("aria-label")).toBe("清除「账号 B」的限流标记");
    expect(clear.attributes("title")).toBe("清除「账号 B」的限流标记");
    expect(clear.classes()).toContain("rate-limited-clear-compact");
    expect(clear.classes()).toContain("rate-limited-clear-always-visible");

    await clear.trigger("click");
    await flushPromises();

    const deleteCall = fetchMock.mock.calls.find(([url, init]) =>
      String(url) === "/api/browser-accounts/b/rate-limited" && init?.method === "DELETE");
    expect(deleteCall).toBeTruthy();
    expect(wrapper.find('[data-testid="rate-limited-b"]').exists()).toBe(false);
    expect((wrapper.get('[data-testid="pool-r1-quota-b"]').element as HTMLInputElement).value).toBe("25");
    expect((wrapper.get('[data-testid="pool-r2-quota-b"]').element as HTMLInputElement).value).toBe("150");
  });
});

describe("BrowserAccountsDialog 当前账号标识（SPEC 046 第五轮）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const accA = { id: "a", name: "账号 A", platforms: { boss: { cdp_port: 9222 } } };
  const accB = { id: "b", name: "账号 B", platforms: { boss: { cdp_port: 9222 } } };

  function accountFetchMock(activeAccount: string, list: unknown[] = [accA, accB]) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: list, active_account: activeAccount });
      }
      if (/\/activate$/.test(url)) return response({ ok: true });
      return response({ init });
    });
  }

  it("标出当前账号那一行，且不改任何动作语义", async () => {
    const wrapper = await mountOpen(accountFetchMock("a"));
    const cards = wrapper.findAll(".browser-account-card");

    // 当前账号：徽章写明「当前账号」
    const badge = cards[0].get('[data-testid="active-account-badge-a"]');
    expect(badge.text()).toBe("当前账号");
    expect(badge.element.tagName).toBe("SPAN");
    expect(badge.classes()).toContain("account-sheet-badge");
    // 不是按钮、不带任何动作，不抢「设为当前账号」的语义
    expect(badge.find("button").exists()).toBe(false);
    expect(badge.attributes("aria-label")).toBeUndefined();
    // 标识与账号名同行，账号名照常可读
    expect(cards[0].find(".account-sheet-name").text()).toContain("账号 A");
    expect(cards[0].get(".account-sheet-name strong").text()).toBe("账号 A");

    // data-active 语义未动
    expect(cards[0].attributes("data-active")).toBe("true");
    expect(cards[1].attributes("data-active")).toBeUndefined();

    // 非当前账号：无标识，仍照旧给「设为当前账号」
    expect(cards[1].find('[data-testid="active-account-badge-b"]').exists()).toBe(false);
    expect(cards[1].find(".account-sheet-identity").text()).not.toContain("当前账号");
    expect(cards[1].find('[data-testid="activate-b"]').exists()).toBe(true);
    expect(cards[1].find('[data-testid="activate-b"]').attributes("aria-label")).toBe("将「账号 B」设为当前账号");
    // 当前账号那一行不渲染切换按钮（原行为）
    expect(cards[0].find('[data-testid="activate-a"]').exists()).toBe(false);
  });

  it("切换账号后当前账号标识跟着走", async () => {
    const fetchMock = accountFetchMock("a");
    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "设为当前账号").trigger("click");
    await flushPromises();

    const cards = wrapper.findAll(".browser-account-card");
    expect(cards[1].find('[data-testid="active-account-badge-b"]').text()).toBe("当前账号");
    expect(cards[0].find('[data-testid="active-account-badge-a"]').exists()).toBe(false);
    expect(cards[0].find('[data-testid="activate-a"]').exists()).toBe(true);
    expect(cards[1].find('[data-testid="activate-b"]').exists()).toBe(false);
    expect(cards[1].attributes("data-active")).toBe("true");
    expect(cards[0].attributes("data-active")).toBeUndefined();
    expect(fetchMock.mock.calls.filter(([url]) => /\/activate$/.test(String(url)))).toHaveLength(1);
  });

  it("限流账号同时是当前账号时两个标识并排、账号名仍在", async () => {
    const limited = { ...accB, rate_limited: true };
    const wrapper = await mountOpen(accountFetchMock("b", [accA, limited]));
    const cards = wrapper.findAll(".browser-account-card");
    const current = cards[1];

    expect(current.find('[data-testid="active-account-badge-b"]').text()).toBe("当前账号");
    expect(current.find('[data-testid="rate-limited-b"]').exists()).toBe(true);
    expect(current.find(".account-sheet-name strong").text()).toBe("账号 B");
  });

  it("未登记平台按唯一权威显示其它平台，不显示空白也不默认成智联", async () => {
    const odd = { id: "c", name: "账号 C", platforms: { liepin: { cdp_port: 9224 } } };
    const zhilianOnly = { id: "d", name: "账号 D", platforms: { zhilian: { cdp_port: 9225 } } };
    const wrapper = await mountOpen(accountFetchMock("a", [accA, odd, zhilianOnly]));
    const platforms = wrapper.get('[data-testid="account-platforms-c"]');

    expect(platforms.text()).toContain("其它平台");
    expect(platforms.text()).not.toContain("智联");
    expect(wrapper.get('[data-testid="open-liepin-c"]').attributes("aria-label")).toBe("打开「账号 C」的其它平台浏览器");
    // 已登记平台名字不变
    expect(wrapper.get('[data-testid="account-platforms-a"]').text()).toContain("BOSS");
    expect(wrapper.get('[data-testid="account-platforms-d"]').text()).toContain("智联");
  });

  it("删除占用提示里的未登记平台也说其它平台，不吐内部码", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: [accA], active_account: "a" });
      }
      if (url.endsWith("/api/browser-accounts/a") && init?.method === "DELETE") {
        return response({
          ok: false,
          error_code: "browser_in_use",
          user_message: "该账号正被占用，无法删除",
          details: { locked_platform: "klart" },
        }, 409);
      }
      return response({ init });
    });

    const wrapper = await mountOpen(fetchMock);
    await findButton(wrapper, "删除").trigger("click");
    await findButton(wrapper, "确认删除").trigger("click");
    await flushPromises();

    const notice = wrapper.get('.browser-account-notice[data-tone="error"]');
    expect(notice.text()).toContain("其它平台 被运行中任务占用");
    expect(notice.text()).not.toContain("klart");
  });
});

// 窄屏账号池行布局（SPEC 046 第五轮）。
// AccountPoolSheet.vue 里有两份 <style scoped>：前面是压缩成一行的历史块，后面是结构化块。
// 媒体查询不增加特异度，同特异度后写者胜 —— 所以窄屏真正生效的那一条，是整份源码里
// 最后一条给 .account-sheet-row 设 grid-template-columns 的声明。历史块写了窄屏两列，
// 却永远被结构化块后面那条五列声明盖掉，账号行因此留在五列（第 5 列动作掉出可视区）。
// jsdom 不做布局计算，这里只能锁「哪一条生效 + 生效的那条是什么」；
// 实际列宽、是否真的不横向滚动、对比度必须留给真实浏览器验证。
const sheetStyleSource = readFileSync(
  path.join(__dirname, "../AccountPoolSheet.vue"),
  "utf8",
);
const structuredStyle = sheetStyleSource.slice(sheetStyleSource.lastIndexOf("<style scoped>"));
const narrowMediaStart = structuredStyle.indexOf("@media (max-width: 640px)");
const narrowMediaBody = narrowMediaStart < 0
  ? ""
  : (structuredStyle.slice(narrowMediaStart).match(/@media\s*\(\s*max-width:\s*640px\s*\)\s*\{([\s\S]*?)\n\}/)?.[1] ?? "");

function rowColumnDeclarations(source: string) {
  return Array.from(source.matchAll(/\.account-sheet-row\s*\{([^}]*)\}/g))
    .map((match) => ({
      index: match.index ?? 0,
      columns: (match[1].match(/grid-template-columns:\s*([^;]*)/)?.[1] ?? "").replace(/\s+/g, " ").trim(),
    }))
    .filter((declaration) => declaration.columns !== "");
}

function gridTracks(columns: string): string[] {
  const tracks: string[] = [];
  let depth = 0;
  let current = "";
  for (const character of columns) {
    if (character === "(") depth += 1;
    if (character === ")") depth -= 1;
    if (character === " " && depth === 0) {
      if (current) tracks.push(current);
      current = "";
      continue;
    }
    current += character;
  }
  if (current) tracks.push(current);
  return tracks;
}

// 一个网格轨道强制占用的最小宽度：minmax() 取下限，定宽 px 取其值，auto/fr 不设下限。
function trackMinimum(columns: string): number {
  return gridTracks(columns).reduce((total, track) => {
    const minmax = track.match(/minmax\(\s*([\d.]+)px/);
    if (minmax) return total + Number(minmax[1]);
    const fixed = track.match(/^([\d.]+)px$/);
    return total + (fixed ? Number(fixed[1]) : 0);
  }, 0);
}

function ruleBody(block: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(block);
  return match ? match[1].replace(/\s+/g, " ").trim() : "";
}

// 媒体查询块内没有嵌套规则，按「选择器 { 声明 }」平铺解析即可。
function styleRules(block: string) {
  return Array.from(block.matchAll(/([^{}]+)\{([^}]*)\}/g)).map((match) => ({
    selector: match[1].replace(/\s+/g, " ").trim(),
    body: match[2].replace(/\s+/g, " ").trim(),
  }));
}

function bodiesFor(block: string, ...selectors: string[]) {
  return styleRules(block)
    .filter((rule) => selectors.every((selector) => rule.selector.includes(selector)))
    .map((rule) => rule.body);
}

describe("AccountPoolSheet 窄屏账号行布局（SPEC 046 第五轮）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("窄屏生效的是两列模板，账号行不再留在五列", () => {
    const declarations = rowColumnDeclarations(sheetStyleSource);
    expect(declarations.length).toBeGreaterThan(1);
    const winning = declarations[declarations.length - 1];

    // 生效声明必须落在结构化块的窄屏媒体查询里，而不是宽屏那条五列声明
    expect(narrowMediaStart).toBeGreaterThanOrEqual(0);
    const winningAbsoluteIndex = sheetStyleSource.lastIndexOf(structuredStyle) + winning.index;
    expect(winningAbsoluteIndex).toBeGreaterThan(sheetStyleSource.lastIndexOf(structuredStyle) + narrowMediaStart);
    expect(winning.columns).not.toContain("var(--account-sheet-columns)");

    const tracks = gridTracks(winning.columns);
    expect(tracks).toHaveLength(2);
    // 两列下限合计必须远小于最窄可视宽度，动作列才可能留在可视区内
    expect(trackMinimum(winning.columns)).toBeLessThanOrEqual(140);
  });

  it("窄屏表头与账号行同一套两列，投放语义补齐", () => {
    expect(narrowMediaBody).not.toBe("");
    const headerColumns = (ruleBody(narrowMediaBody, ".account-sheet-columns").match(/grid-template-columns:\s*([^;]*)/)?.[1] ?? "").trim();
    expect(gridTracks(headerColumns)).toHaveLength(2);

    // 勾选 / 配额 / 平台列跨整行，动作列回到首行末列
    expect(bodiesFor(narrowMediaBody, ".pool-toggle", ".account-sheet-quotas", ".account-sheet-platforms").join(";"))
      .toContain("grid-column: 1 / -1");
    const actions = bodiesFor(narrowMediaBody, ".account-sheet-actions").join(";");
    expect(actions).toContain("grid-column: 2");
    expect(actions).toContain("grid-row: 1");
    const platforms = styleRules(narrowMediaBody)
      .filter((rule) => rule.selector.includes(".account-sheet-platforms") && rule.body.includes("grid-template-columns"))
      .map((rule) => (rule.body.match(/grid-template-columns:\s*([^;]*)/)?.[1] ?? "").trim());
    expect(platforms).toHaveLength(1);
    expect(gridTracks(platforms[0])).toHaveLength(2);

    // 表头文案：窄屏只隐藏对不上列的两格，账号与操作两格仍说得出名字
    expect(narrowMediaBody).toContain("display: none");
    expect(sheetStyleSource).not.toMatch(/\.account-sheet-header\s+span\s*\{[^}]*display:\s*none/);
  });

  it("双徽章并存时账号名有整列宽度可用，不被压成纯省略号", () => {
    const name = bodiesFor(narrowMediaBody, ".account-sheet-name").join(";");
    expect(name).toContain("flex-wrap: wrap");
    expect(name).not.toContain("nowrap");
    // 徽章不收缩、不折行，账号名自身可省略号收缩（长名字不撑宽列）
    expect(sheetStyleSource).toMatch(/\.account-sheet-badge\.active-account\{[^}]*flex:\s*0 0 auto/);
    expect(sheetStyleSource).toMatch(/\.account-sheet-name>strong\{[^}]*min-width:\s*0[^}]*text-overflow:\s*ellipsis/);
  });

  it("窄屏不渲染末列表头标签，末列不再由表头与行各算各的宽", () => {
    expect(narrowMediaBody).not.toBe("");
    // 表头与账号行是两个独立 grid，末列都是 auto：表头末列内容是「操作」二字（约 22px），
    // 行末列是两枚图标按钮（约 64px），两列布局下标签只会浮在按钮组右侧而非正上方。
    // 本功能选择的收口方式：窄屏不显示末列标签（列标题在两列布局下本就不是必需）。
    const headerHideRules = styleRules(narrowMediaBody)
      .filter((rule) => rule.selector.includes(".account-sheet-columns") && /display:\s*none/.test(rule.body))
      .map((rule) => rule.selector)
      .join(" | ");
    expect(headerHideRules).toMatch(/span:last-child/);
    // 但收口不能靠给末列加下限（那会把窄屏两列的合计下限抬高、换成横向滚动）
    const declarations = rowColumnDeclarations(sheetStyleSource);
    const narrowRowTracks = gridTracks(declarations[declarations.length - 1].columns);
    expect(narrowRowTracks).toHaveLength(2);
    expect(narrowRowTracks[1]).toBe("auto");
    expect(trackMinimum(declarations[declarations.length - 1].columns)).toBeLessThanOrEqual(140);
    // 宽屏五列版式没被动过
    const wideTracks = gridTracks((structuredStyle.match(/--account-sheet-columns:\s*([^;]*)/)?.[1] ?? "").trim());
    expect(wideTracks).toHaveLength(5);
    expect(narrowMediaBody).not.toContain("--account-sheet-columns");
  });

  it("窄屏只改样式：两个徽章并存时动作按钮与可访问名各自仍在", async () => {
    const limitedCurrent = {
      id: "a",
      name: "账号 A",
      platforms: { boss: { cdp_port: 9222 } },
      rate_limited: true,
      pool: { selected: true, order: 0, r1_quota: 25, r2_quota: 150 },
    };
    const otherAccount = { id: "b", name: "账号 B", platforms: { boss: { cdp_port: 9222 } } };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/browser-accounts") {
        return response({ accounts: [limitedCurrent, otherAccount], active_account: "a" });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    const cards = wrapper.findAll(".browser-account-card");

    expect(cards[0].attributes("data-active")).toBe("true");
    expect(cards[0].get('[data-testid="active-account-badge-a"]').element.tagName).toBe("SPAN");
    expect(cards[0].get('[data-testid="rate-limited-a"]').element.tagName).toBe("SPAN");
    expect(cards[0].get(".account-sheet-name strong").text()).toBe("账号 A");

    const clear = cards[0].get('[data-testid="clear-rate-limited-a"]');
    expect(clear.element.tagName).toBe("BUTTON");
    expect(clear.attributes("aria-label")).toBe("清除「账号 A」的限流标记");
    expect(clear.attributes("title")).toBe("清除「账号 A」的限流标记");

    // 当前账号那一行不给切换按钮，删除按钮照常；非当前行切换按钮照常
    expect(cards[0].find('[data-testid="activate-a"]').exists()).toBe(false);
    expect(cards[0].find('[data-testid="delete-a"]').exists()).toBe(true);
    expect(cards[1].get('[data-testid="activate-b"]').attributes("aria-label")).toBe("将「账号 B」设为当前账号");
    expect(cards[1].get('[data-testid="activate-b"]').attributes("title")).toBe("将「账号 B」设为当前账号");

    // 勾选与配额入口没有被窄屏样式挪走
    expect(cards[0].get('[data-testid="pool-selected-a"]').element.tagName).toBe("INPUT");
    expect(cards[0].findAll('[data-testid="pool-config-a"] input')).toHaveLength(2);
  });
});

// 行内操作的可访问名必须带得出「是哪个账号」（SPEC 046 第五轮）。
// 图标按钮原本只有动作词，多账号时每行读出来一模一样，读屏分不清对象、也没法按名字
// 找到某个账号的按钮。身份一律取父组件传入的显示名口径（「默认账号」这类中文名），
// 不引入内部 id；动作词、按钮可见性条件、data-testid 与点击语义都不变。
describe("AccountPoolSheet 行内操作可访问名（SPEC 046 第五轮）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // builtin 且 id="a"：显示名口径是「默认账号」，与 name「账号A」、id「a」都不同，
  // 用它当探针即可确认标注走的是 displayName 而不是原始字段。
  const defaultAccount = {
    id: "a",
    name: "账号A",
    builtin: true,
    rate_limited: true,
    platforms: { boss: { cdp_port: 9222 }, zhilian: { cdp_port: 9223 } },
    pool: { selected: true, order: 0, r1_quota: 25, r2_quota: 150 },
  };
  const secondAccount = {
    id: "b",
    name: "账号 B",
    platforms: { boss: { cdp_port: 9222 } },
    pool: { selected: true, order: 1, r1_quota: 25, r2_quota: 150 },
  };

  function twoAccountFetchMock() {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-accounts") {
        return response({ accounts: [defaultAccount, secondAccount], active_account: "a" });
      }
      if (/\/activate$/.test(url)) return response({ ok: true });
      return response({ init });
    });
  }

  it("动作按钮的可访问名并进了本行账号的显示名", async () => {
    const wrapper = await mountOpen(twoAccountFetchMock());
    const cards = wrapper.findAll(".browser-account-card");

    const clear = cards[0].get('[data-testid="clear-rate-limited-a"]');
    expect(clear.attributes("aria-label")).toBe("清除「默认账号」的限流标记");
    expect(clear.attributes("title")).toBe("清除「默认账号」的限流标记");

    // 打开平台按钮沿用已有的平台显示名口径与原有拼接方式，只在前面并上账号身份
    const openBoss = cards[0].get('[data-testid="open-boss-a"]');
    expect(openBoss.attributes("aria-label")).toBe("打开「默认账号」的BOSS浏览器");
    expect(openBoss.attributes("title")).toBe("打开「默认账号」的BOSS浏览器");

    const activate = cards[1].get('[data-testid="activate-b"]');
    expect(activate.attributes("aria-label")).toBe("将「账号 B」设为当前账号");
    expect(activate.attributes("title")).toBe("将「账号 B」设为当前账号");

    const remove = cards[1].get('[data-testid="delete-b"]');
    expect(remove.attributes("aria-label")).toBe("删除「账号 B」");
    expect(remove.attributes("title")).toBe("删除「账号 B」");

    // 身份来自显示名口径：既不是内部 id，也不是原始 name
    for (const label of [
      clear.attributes("aria-label"),
      openBoss.attributes("aria-label"),
      activate.attributes("aria-label"),
      remove.attributes("aria-label"),
    ]) {
      expect(label).not.toMatch(/\b[a-z]\b/);
      expect(label).not.toContain("账号A");
    }
  });

  it("勾选与配额输入的可访问名同样带账号身份，且包住界面可见文字", async () => {
    const wrapper = await mountOpen(twoAccountFetchMock());

    expect(wrapper.get('[data-testid="pool-selected-a"]').attributes("aria-label")).toBe("让「默认账号」参与轮询");
    expect(wrapper.get('[data-testid="pool-selected-b"]').attributes("aria-label")).toBe("让「账号 B」参与轮询");
    expect(wrapper.get('[data-testid="pool-r1-quota-a"]').attributes("aria-label")).toBe("「默认账号」的 R1 配额");
    expect(wrapper.get('[data-testid="pool-r2-quota-a"]').attributes("aria-label")).toBe("「默认账号」的 R2 配额");
    expect(wrapper.get('[data-testid="pool-r1-quota-b"]').attributes("aria-label")).toBe("「账号 B」的 R1 配额");
    expect(wrapper.get('[data-testid="pool-r2-quota-b"]').attributes("aria-label")).toBe("「账号 B」的 R2 配额");

    // 可见文字没改，且仍是可访问名的一部分（读屏按界面词也能找到）
    const quotaRow = wrapper.get('[data-testid="pool-config-a"] .account-sheet-quota-row');
    expect(quotaRow.text()).toContain("R1");
    expect(wrapper.get(".pool-toggle").text()).toContain("参与轮询");
  });

  it("多账号时同组可访问名两两不同，动作语义与点击请求不变", async () => {
    const fetchMock = twoAccountFetchMock();
    const wrapper = await mountOpen(fetchMock);
    const sheet = wrapper.get('[data-testid="account-sheet-header"]');

    const labels = sheet.findAll("[aria-label]").map((el) => el.attributes("aria-label") as string);
    expect(labels.length).toBeGreaterThanOrEqual(10);
    expect(new Set(labels).size).toBe(labels.length);
    // 每个可访问名都点得出账号身份
    for (const label of labels) {
      expect(label).toMatch(/默认账号|账号 B/);
    }

    // 按钮可见性与点击语义没动：当前账号那行不给切换按钮，内置账号不给删除按钮
    const cards = wrapper.findAll(".browser-account-card");
    expect(cards[0].find('[data-testid="activate-a"]').exists()).toBe(false);
    expect(cards[0].find('[data-testid="delete-a"]').exists()).toBe(false);
    expect(cards[1].find('[data-testid="activate-b"]').exists()).toBe(true);

    await cards[1].get('[data-testid="activate-b"]').trigger("click");
    await flushPromises();
    const activateCall = fetchMock.mock.calls.find(([url]) => /\/api\/browser-accounts\/b\/activate$/.test(String(url)));
    expect(activateCall).toBeTruthy();
  });
});
