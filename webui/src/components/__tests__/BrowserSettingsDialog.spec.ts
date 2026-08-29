import { flushPromises, mount } from "@vue/test-utils";
import BrowserSettingsDialog from "../BrowserSettingsDialog.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const registryPayload = {
  registry: [
    { key: "chrome", name: "Chrome", installed: true, path: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" },
    { key: "edge", name: "Edge", installed: true, path: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" },
    { key: "brave", name: "Brave", installed: false, path: null },
  ],
  selection: { mode: "auto" },
  effective_path: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
};

// 组件用 watch(props.open) 加载清单；初始 mount open=false 再切 true 才会触发。
async function mountOpen(fetchMock: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(BrowserSettingsDialog, { props: { open: false } });
  await flushPromises();
  await wrapper.setProps({ open: true });
  await flushPromises();
  return wrapper;
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

describe("BrowserSettingsDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders registry list with installed paths and uninstalled disabled", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/browser-registry") return response(registryPayload);
      return response({});
    });
    const wrapper = await mountOpen(fetchMock);

    const chrome = findButton(wrapper, "browser-option-chrome");
    expect(chrome.attributes("disabled")).toBeUndefined();
    expect(chrome.text()).toContain("chrome.exe");

    const brave = findButton(wrapper, "browser-option-brave");
    expect(brave.attributes("disabled")).toBeDefined();
    expect(brave.text()).toContain("未安装");

    expect(wrapper.get('[data-testid="browser-effective-path"]').text())
      .toContain("chrome.exe");
  });

  it("saves registry selection and shows success notice", async () => {
    const putBodies: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-registry" && (init?.method || "GET") === "PUT") {
        putBodies.push(JSON.parse(String(init?.body)));
        return response({ ok: true, ...registryPayload, selection: { mode: "registry", key: "edge" } });
      }
      if (url === "/api/browser-registry") return response(registryPayload);
      return response({});
    });
    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "browser-option-edge").trigger("click");
    await findButton(wrapper, "browser-save-button").trigger("click");
    await flushPromises();

    expect(putBodies).toEqual([{ mode: "registry", key: "edge" }]);
    expect(wrapper.get('[data-testid="browser-save-notice"]').text()).toContain("已保存");
  });

  it("validates manual path and surfaces kernel-incompatible message", async () => {
    const validateBodies: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-registry/validate-path") {
        validateBodies.push(JSON.parse(String(init?.body)));
        return response(
          { ok: false, error: "kernel_incompatible", message: "内核不兼容：该浏览器不是 Chromium 内核，无法用于抓取" },
          400,
        );
      }
      if (url === "/api/browser-registry") return response(registryPayload);
      return response({});
    });
    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "browser-option-manual").trigger("click");
    const input = wrapper.get('[data-testid="browser-manual-input"]');
    await input.setValue("C:\\Browsers\\firefox.exe");
    await findButton(wrapper, "browser-validate-button").trigger("click");
    await flushPromises();

    expect(validateBodies).toEqual([{ path: "C:\\Browsers\\firefox.exe" }]);
    const message = wrapper.get('[data-testid="browser-validation-message"]');
    expect(message.text()).toContain("内核不兼容");
    expect(message.classes()).toContain("bad");
  });

  it("saves manual path selection", async () => {
    const putBodies: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-registry" && (init?.method || "GET") === "PUT") {
        putBodies.push(JSON.parse(String(init?.body)));
        return response({ ok: true, ...registryPayload });
      }
      if (url === "/api/browser-registry") return response(registryPayload);
      return response({});
    });
    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "browser-option-manual").trigger("click");
    await wrapper.get('[data-testid="browser-manual-input"]').setValue("C:\\Browsers\\vivaldi.exe");
    await findButton(wrapper, "browser-save-button").trigger("click");
    await flushPromises();

    expect(putBodies).toEqual([{ mode: "manual", path: "C:\\Browsers\\vivaldi.exe" }]);
    expect(wrapper.get('[data-testid="browser-save-notice"]').text()).toContain("已保存");
  });

  it("shows error notice when save fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/browser-registry" && (init?.method || "GET") === "PUT") {
        return response(
          { ok: false, error: "path_validation_failed", message: "路径校验失败：该文件无法作为浏览器执行" },
          400,
        );
      }
      if (url === "/api/browser-registry") return response(registryPayload);
      return response({});
    });
    const wrapper = await mountOpen(fetchMock);

    await findButton(wrapper, "browser-option-auto").trigger("click");
    await findButton(wrapper, "browser-save-button").trigger("click");
    await flushPromises();

    const notice = wrapper.get('[data-testid="browser-save-notice"]');
    expect(notice.text()).toContain("路径校验失败");
    expect(notice.classes()).toContain("bad");
  });
});
