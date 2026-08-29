import { flushPromises, mount } from "@vue/test-utils";
import BrowserKernelPicker from "../BrowserKernelPicker.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";
import type { BrowserRegistryEntryState, BrowserSelection } from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const registryEntries: BrowserRegistryEntryState[] = [
  { key: "chrome", name: "Chrome", installed: true, path: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" },
  { key: "edge", name: "Edge", installed: true, path: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" },
  { key: "brave", name: "Brave", installed: false, path: null },
];

function mountPicker(props: {
  entries?: BrowserRegistryEntryState[];
  selection?: BrowserSelection;
  effectivePath?: string | null;
  locked?: boolean;
} = {}) {
  return mount(BrowserKernelPicker, {
    props: {
      entries: props.entries ?? registryEntries,
      selection: props.selection ?? { mode: "auto" },
      effectivePath: props.effectivePath ?? null,
      locked: props.locked ?? false,
    },
  });
}

function findButton(wrapper: ReturnType<typeof mount>, testId: string) {
  const btn = wrapper.findAll("button").find((b) => b.attributes("data-testid") === testId);
  if (!btn) throw new Error(`button [data-testid="${testId}"] not found`);
  return btn;
}

// 本文件引用的 api 模块实例可能未被 setup.ts 验证过（vitest 模块实例隔离），逐用例重新验证
beforeEach(() => {
  setBuildIdentity(expectedBackendBuildHash);
});

describe("BrowserKernelPicker", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders registry list with uninstalled disabled", () => {
    const wrapper = mountPicker();

    const chrome = findButton(wrapper, "browser-option-chrome");
    expect(chrome.attributes("disabled")).toBeUndefined();
    expect(chrome.text()).toContain("Chrome");
    // 路径收进悬停提示（浮层窄，不占版面）
    expect(chrome.attributes("title")).toContain("chrome.exe");

    const brave = findButton(wrapper, "browser-option-brave");
    expect(brave.attributes("disabled")).toBeDefined();
    expect(brave.attributes("title")).toContain("未安装");
  });

  it("clicking an option only moves the highlight until 使用 is pressed", async () => {
    const putBodies: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-registry" && (init?.method || "GET") === "PUT") {
        putBodies.push(JSON.parse(String(init?.body)));
        return response({
          ok: true,
          registry: registryEntries,
          selection: { mode: "registry", key: "edge" },
          effective_path: registryEntries[1]!.path,
        });
      }
      return response({});
    });
    const wrapper = mountPicker();
    vi.stubGlobal("fetch", fetchMock);

    // 点 Edge：只是草稿选中，不发请求、不弹任何确认
    await findButton(wrapper, "browser-option-edge").trigger("click");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(findButton(wrapper, "browser-option-edge").classes()).toContain("selected");
    expect(findButton(wrapper, "browser-option-auto").classes()).not.toContain("selected");
    expect(wrapper.get('[data-testid="browser-apply-button"]').text()).toContain("使用所选浏览器");

    // 点回当前选择 → 草稿清空，使用按钮退场
    await findButton(wrapper, "browser-option-auto").trigger("click");
    expect(wrapper.find('[data-testid="browser-apply-button"]').exists()).toBe(false);

    // 重新选中 Edge 后点「使用所选浏览器」才真正切换
    await findButton(wrapper, "browser-option-edge").trigger("click");
    await wrapper.get('[data-testid="browser-apply-button"]').trigger("click");
    await flushPromises();

    expect(putBodies).toEqual([{ mode: "registry", key: "edge" }]);
    expect(wrapper.emitted("changed")).toEqual([
      [{ selection: { mode: "registry", key: "edge" }, effectivePath: registryEntries[1]!.path }],
    ]);
    expect(wrapper.find('[data-testid="browser-apply-button"]').exists()).toBe(false);
  });

  it("clicking the current selection drafts nothing", async () => {
    const fetchMock = vi.fn(async () => response({}));
    const wrapper = mountPicker({ selection: { mode: "registry", key: "edge" } });
    vi.stubGlobal("fetch", fetchMock);

    expect(findButton(wrapper, "browser-option-edge").classes()).toContain("selected");
    await findButton(wrapper, "browser-option-edge").trigger("click");

    expect(wrapper.find('[data-testid="browser-apply-button"]').exists()).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("locked picker ignores option clicks", async () => {
    const fetchMock = vi.fn(async () => response({}));
    const wrapper = mountPicker({ locked: true });
    vi.stubGlobal("fetch", fetchMock);

    await findButton(wrapper, "browser-option-edge").trigger("click");

    expect(wrapper.find('[data-testid="browser-apply-button"]').exists()).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("validates manual path then saves directly", async () => {
    const postBodies: unknown[] = [];
    const putBodies: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-registry/validate-path") {
        postBodies.push(JSON.parse(String(init?.body)));
        return response({ ok: true, version: "Vivaldi 7.0" });
      }
      if (url === "/api/browser-registry" && (init?.method || "GET") === "PUT") {
        putBodies.push(JSON.parse(String(init?.body)));
        return response({
          ok: true,
          registry: registryEntries,
          selection: { mode: "manual", manual_path: "C:\\Browsers\\vivaldi.exe" },
          effective_path: "C:\\Browsers\\vivaldi.exe",
        });
      }
      return response({});
    });
    const wrapper = mountPicker();
    vi.stubGlobal("fetch", fetchMock);

    await findButton(wrapper, "browser-option-manual").trigger("click");
    await wrapper.get('[data-testid="browser-manual-input"]').setValue("C:\\Browsers\\vivaldi.exe");
    await findButton(wrapper, "browser-validate-button").trigger("click");
    await flushPromises();

    expect(postBodies).toEqual([{ path: "C:\\Browsers\\vivaldi.exe" }]);
    // 校验通过直接保存并上报（父级收到 changed 会收起浮层）
    expect(putBodies).toEqual([{ mode: "manual", path: "C:\\Browsers\\vivaldi.exe" }]);
    expect(wrapper.emitted("changed")).toEqual([
      [{ selection: { mode: "manual", manual_path: "C:\\Browsers\\vivaldi.exe" }, effectivePath: "C:\\Browsers\\vivaldi.exe" }],
    ]);
  });

  it("surfaces kernel-incompatible message and saves nothing", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/browser-registry/validate-path") {
        return response(
          { ok: false, error: "kernel_incompatible", message: "内核不兼容：该浏览器不是 Chromium 内核，无法用于抓取" },
          400,
        );
      }
      return response({});
    });
    const wrapper = mountPicker();
    vi.stubGlobal("fetch", fetchMock);

    await findButton(wrapper, "browser-option-manual").trigger("click");
    await wrapper.get('[data-testid="browser-manual-input"]').setValue("C:\\Browsers\\firefox.exe");
    await findButton(wrapper, "browser-validate-button").trigger("click");
    await flushPromises();

    const message = wrapper.get('[data-testid="browser-validation-message"]');
    expect(message.text()).toContain("内核不兼容");
    expect(message.classes()).toContain("bad");
  });

  it("keeps the draft and shows error notice when save fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-registry" && (init?.method || "GET") === "PUT") {
        return response(
          { ok: false, error: "path_validation_failed", message: "路径校验失败：该文件无法作为浏览器执行" },
          400,
        );
      }
      return response({});
    });
    const wrapper = mountPicker();
    vi.stubGlobal("fetch", fetchMock);

    await findButton(wrapper, "browser-option-edge").trigger("click");
    await wrapper.get('[data-testid="browser-apply-button"]').trigger("click");
    await flushPromises();

    const notice = wrapper.get('[data-testid="browser-save-notice"]');
    expect(notice.text()).toContain("路径校验失败");
    expect(notice.classes()).toContain("bad");
    // 草稿保留，可改选或重试
    expect(wrapper.find('[data-testid="browser-apply-button"]').exists()).toBe(true);
    expect(wrapper.emitted("changed")).toBeUndefined();
  });

  it("shows zero-install guidance when nothing detected and hides it in manual branch", async () => {
    const emptyEntries = registryEntries.map((item) => ({ ...item, installed: false, path: null }));
    const wrapper = mountPicker({ entries: emptyEntries });

    const hint = wrapper.get('[data-testid="browser-zero-hint"]');
    expect(hint.text()).toContain("手动指定路径");
    // 手动分支展开后指引退场
    await findButton(wrapper, "browser-option-manual").trigger("click");
    expect(wrapper.find('[data-testid="browser-zero-hint"]').exists()).toBe(false);
  });

  it("shows the effective path when present", () => {
    const wrapper = mountPicker({ effectivePath: registryEntries[0]!.path });
    expect(wrapper.get('[data-testid="browser-effective-path"]').text()).toContain("chrome.exe");
  });
});
