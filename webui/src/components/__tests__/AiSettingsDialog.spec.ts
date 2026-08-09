import { flushPromises, mount } from "@vue/test-utils";
import AiSettingsDialog from "../AiSettingsDialog.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function mountOpen(fetchMock: (input: RequestInfo | URL) => Promise<Response>) {
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(AiSettingsDialog, { props: { open: false } });
  await flushPromises();
  await wrapper.setProps({ open: true });
  await flushPromises();
  return wrapper;
}

function settingsResponse() {
  return response({
    endpoint_url: "https://api.example.com/v1",
    model: "",
    masked_key: "sk-****",
    is_configured: true,
  });
}

function buttonByText(wrapper: ReturnType<typeof mount>, label: string) {
  const button = wrapper.findAll("button").find((candidate) => candidate.text().includes(label));
  if (!button) throw new Error(`button not found: ${label}`);
  return button;
}

beforeEach(() => {
  setBuildIdentity(expectedBackendBuildHash);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AiSettingsDialog failure messages", () => {
  it("shows backend Chinese message for a known test error and hides raw code", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/ai-settings") return settingsResponse();
      if (url === "/api/ai-settings/test") {
        return response({
          ok: false,
          warning_codes: ["auth_failed"],
          user_message: "API 密钥无效或已过期，请检查 AI 设置",
        });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    await buttonByText(wrapper, "测试连接").trigger("click");
    await flushPromises();

    const notice = wrapper.get(".ai-local-notice");
    expect(notice.text()).toContain("API 密钥无效或已过期");
    expect(notice.text()).not.toContain("auth_failed");
    expect(wrapper.text()).not.toContain("auth_failed");
  });

  it("falls back to pure Chinese for unknown test codes", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/ai-settings") return settingsResponse();
      if (url === "/api/ai-settings/test") {
        return response({ ok: false, warning_codes: ["mystery_code"], user_message: "" });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    await buttonByText(wrapper, "测试连接").trigger("click");
    await flushPromises();

    const notice = wrapper.get(".ai-local-notice");
    expect(notice.text()).toContain("AI 服务连接失败，请检查 AI 设置后重试");
    expect(wrapper.text()).not.toContain("mystery_code");
  });

  it("shows backend Chinese message when model fetching fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/ai-settings") return settingsResponse();
      if (url === "/api/ai-settings/models") {
        return response({
          ok: false,
          error_code: "network_error",
          user_message: "无法连接 AI 服务，请检查网络与地址配置",
        }, 502);
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    await buttonByText(wrapper, "拉取模型").trigger("click");
    await flushPromises();

    const notice = wrapper.get(".ai-local-notice");
    expect(notice.text()).toContain("无法连接 AI 服务");
    expect(notice.text()).not.toContain("network_error");
    expect(wrapper.text()).not.toContain("network_error");
  });

  it("falls back to pure Chinese when model fetching has no user_message", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/ai-settings") return settingsResponse();
      if (url === "/api/ai-settings/models") {
        return response({ ok: false, error_code: "mystery_code" }, 502);
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    await buttonByText(wrapper, "拉取模型").trigger("click");
    await flushPromises();

    const notice = wrapper.get(".ai-local-notice");
    expect(notice.text()).toContain("模型列表拉取失败，请检查 AI 设置后重试");
    expect(wrapper.text()).not.toContain("mystery_code");
  });
});
