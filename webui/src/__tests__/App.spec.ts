import { flushPromises, mount } from "@vue/test-utils";
import App from "../App.vue";
import { expectedBackendBuildHash } from "../api";

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
      if (url.endsWith("/api/session")) {
        return response({ status: "ok", build_hash: expectedBackendBuildHash });
      }
      if (url.endsWith("/api/version")) {
        return response({
          backend_version: "010",
          build_hash: expectedBackendBuildHash,
          build_time: "now",
        });
      }
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

  it("keeps AI settings reachable", async () => {
    const wrapper = mount(App);
    await flushPromises();

    await wrapper.get('[data-testid="ai-settings-trigger"]').trigger("click");
    const dialog = wrapper.get('[role="dialog"]');
    expect(dialog.attributes("aria-modal")).toBe("true");

    await dialog.trigger("keydown", { key: "Escape" });
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  });

  it("shows a persistent inline notice when the model list is empty", async () => {
    vi.useFakeTimers();
    const wrapper = mount(App);
    await flushPromises();

    await wrapper.get('[data-testid="ai-settings-trigger"]').trigger("click");
    await flushPromises();
    await wrapper.get('[aria-label="拉取可用模型"]').trigger("click");
    await flushPromises();

    // 模型列表为空时，提示以内联 .ai-local-notice 就地展示（非全局 .notice-bar）
    const notice = wrapper.get('.ai-local-notice');
    expect(notice.text()).toContain("服务未返回模型列表");

    // 内联提示不自动消失：推进 5s 仍在
    vi.advanceTimersByTime(5_000);
    await flushPromises();
    expect(wrapper.find('.ai-local-notice').exists()).toBe(true);

    // 关闭对话框后清空
    await wrapper.get('[role="dialog"]').trigger("keydown", { key: "Escape" });
    await flushPromises();
    expect(wrapper.find('.ai-local-notice').exists()).toBe(false);
    wrapper.unmount();
  });
});
