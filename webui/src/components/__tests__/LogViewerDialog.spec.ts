import { flushPromises, mount } from "@vue/test-utils";
import LogViewerDialog from "../LogViewerDialog.vue";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function tailPayload(lines: string[], identity = "size:1", start = 1) {
  const end = start + lines.length - 1;
  return {
    ok: true,
    lines,
    start,
    end,
    total: end,
    identity,
    rotated: false,
    empty: lines.length === 0,
  };
}

async function mountOpen(fetchMock: (input: RequestInfo | URL) => Promise<Response>) {
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(LogViewerDialog, {
    props: { open: false },
    global: { stubs: { teleport: true } },
  });
  await wrapper.setProps({ open: true });
  await flushPromises();
  return wrapper;
}

describe("LogViewerDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("renders the log dialog with lines in oldest-to-newest order", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/session")) return response({});
      if (url.startsWith("/api/logs")) {
        return response(tailPayload(["line1", "line2", "line3"], "size:1"));
      }
      return response({});
    });
    const wrapper = await mountOpen(fetchMock);
    expect(wrapper.find('[data-testid="log-viewer"]').exists()).toBe(true);
    const lines = wrapper.findAll(".log-line").map((n) => n.text());
    expect(lines).toEqual(["line1", "line2", "line3"]);
  });

  it("defaults to following the latest line", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/session")) return response({});
      if (url.startsWith("/api/logs")) {
        const many = Array.from({ length: 200 }, (_, i) => `line${i + 1}`);
        return response(tailPayload(many, "size:2"));
      }
      return response({});
    });
    const wrapper = await mountOpen(fetchMock);
    // 初始处于自动跟随最新：不显示"回到底部"
    expect(wrapper.find('[data-testid="log-go-bottom"]').exists()).toBe(false);
  });

  it("polls for new lines and appends them live", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/session")) return response({});
      if (url.includes("since=")) {
        return response({
          ok: true,
          lines: ["new1", "new2"],
          start: 4,
          end: 5,
          total: 5,
          identity: "size:3",
          rotated: false,
          empty: false,
        });
      }
      return response(tailPayload(["line1", "line2", "line3"], "size:1"));
    });
    const wrapper = await mountOpen(fetchMock);
    expect(wrapper.findAll(".log-line")).toHaveLength(3);
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    const lines = wrapper.findAll(".log-line").map((n) => n.text());
    expect(lines).toEqual(["line1", "line2", "line3", "new1", "new2"]);
  });

  it("loads older lines when scrolled to the top", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/session")) return response({});
      if (url.includes("offset=")) {
        return response(tailPayload(["older1", "older2"], "size:4", 1));
      }
      // 首屏：文件共 6 行，tail 展示最新 3 行（start=4）
      return response(tailPayload(["line4", "line5", "line6"], "size:1", 4));
    });
    const wrapper = await mountOpen(fetchMock);
    const body = wrapper.get('[data-testid="log-body"]').element as HTMLElement;
    Object.defineProperty(body, "scrollHeight", { value: 600, configurable: true });
    Object.defineProperty(body, "clientHeight", { value: 200, configurable: true });
    body.scrollTop = 0;
    await wrapper.get('[data-testid="log-body"]').trigger("scroll");
    await flushPromises();
    const lines = wrapper.findAll(".log-line").map((n) => n.text());
    expect(lines).toEqual(["older1", "older2", "line4", "line5", "line6"]);
  });

  it("pauses auto-follow when reading older logs and restores on go-bottom", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/session")) return response({});
      if (url.startsWith("/api/logs")) {
        const many = Array.from({ length: 100 }, (_, i) => `line${i + 1}`);
        return response(tailPayload(many, "size:5"));
      }
      return response({});
    });
    const wrapper = await mountOpen(fetchMock);
    const body = wrapper.get('[data-testid="log-body"]').element as HTMLElement;
    Object.defineProperty(body, "scrollHeight", { value: 2000, configurable: true });
    Object.defineProperty(body, "clientHeight", { value: 300, configurable: true });
    // 翻旧：滚到顶部（非底部 → 暂停跟随）
    body.scrollTop = 0;
    await wrapper.get('[data-testid="log-body"]').trigger("scroll");
    await flushPromises();
    expect(wrapper.find('[data-testid="log-go-bottom"]').exists()).toBe(true);
    // 回到底部：恢复自动跟随（"回到底部"按钮消失）
    await wrapper.get('[data-testid="log-go-bottom"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="log-go-bottom"]').exists()).toBe(false);
  });

  it("shows empty state when the log file has no content", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/session")) return response({});
      if (url.startsWith("/api/logs")) return response(tailPayload([], "size:0"));
      return response({});
    });
    const wrapper = await mountOpen(fetchMock);
    expect(wrapper.text()).toContain("暂无日志");
  });

  it("035: opens run logs directly for an external task id (history round)", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/session")) return response({});
      if (url.startsWith("/api/logs")) {
        if (url.includes("task_id=round-1")) {
          return response(tailPayload(["round-line"], "size:3"));
        }
        return response(tailPayload(["global-line"], "size:2"));
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(LogViewerDialog, {
      props: { open: false, initialTaskId: "round-1" },
      global: { stubs: { teleport: true } },
    });
    await wrapper.setProps({ open: true });
    await flushPromises();
    // 外部指定任务：直接进入运行日志模式，请求带该任务 id 并只显示其日志
    expect(wrapper.text()).toContain("运行日志");
    expect(wrapper.findAll(".log-line").map((n) => n.text())).toEqual(["round-line"]);
    vi.unstubAllGlobals();
  });

  it("035: switches between global and run logs via mode tabs", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/session")) return response({});
      if (url.startsWith("/api/latest-running-task")) {
        return response({ has_task: true, task_id: "task-1" });
      }
      if (url.startsWith("/api/logs")) {
        if (url.includes("task_id=task-1")) {
          return response(tailPayload(["run-line1"], "size:9"));
        }
        return response(tailPayload(["global-line1", "global-line2"], "size:8"));
      }
      return response({});
    });
    const wrapper = await mountOpen(fetchMock);
    // 默认全局日志
    expect(wrapper.findAll(".log-line").map((n) => n.text())).toEqual(["global-line1", "global-line2"]);
    // 切到运行日志：按任务过滤返回运行日志
    await wrapper.get('[data-testid="log-mode-run"]').trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".log-line").map((n) => n.text())).toEqual(["run-line1"]);
    // 切回全局日志
    await wrapper.get('[data-testid="log-mode-global"]').trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".log-line").map((n) => n.text())).toEqual(["global-line1", "global-line2"]);
  });
});
