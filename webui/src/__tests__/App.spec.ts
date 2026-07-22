import { flushPromises, mount } from "@vue/test-utils";
import App from "../App.vue";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/session")) return response({ status: "ok" });
      if (url.endsWith("/api/check")) return response({ connected: true });
      if (url.endsWith("/api/profiles")) return response({ profiles: [] });
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

  it("uses semantic navigation and keeps AI settings reachable", async () => {
    const wrapper = mount(App);
    await flushPromises();

    const tabs = wrapper.get('[role="tablist"]');
    expect(tabs.get('[role="tab"][aria-selected="true"]').text()).toContain("岗位发现");

    await tabs.get('[data-testid="screening-tab"]').trigger("click");
    expect(wrapper.get('[data-testid="screening-view"]').isVisible()).toBe(true);

    await wrapper.get('[data-testid="ai-settings-trigger"]').trigger("click");
    const dialog = wrapper.get('[role="dialog"]');
    expect(dialog.attributes("aria-modal")).toBe("true");

    await dialog.trigger("keydown", { key: "Escape" });
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  });

  it("auto-dismisses notices according to severity", async () => {
    vi.useFakeTimers();
    const wrapper = mount(App);
    await flushPromises();

    await wrapper.get('[data-testid="ai-settings-trigger"]').trigger("click");
    await flushPromises();
    await wrapper.get('[aria-label="拉取可用模型"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('.notice-bar').text()).toContain("服务未返回模型列表");
    vi.advanceTimersByTime(4_999);
    await flushPromises();
    expect(wrapper.find('.notice-bar').exists()).toBe(true);

    vi.advanceTimersByTime(1);
    await flushPromises();
    expect(wrapper.find('.notice-bar').exists()).toBe(false);
    wrapper.unmount();
  });
});
