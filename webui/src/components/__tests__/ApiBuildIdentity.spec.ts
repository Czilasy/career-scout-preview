import {
  apiRequest,
  expectedBackendBuildHash,
  setBuildIdentity,
} from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("API build identity", () => {
  afterEach(() => {
    setBuildIdentity(expectedBackendBuildHash);
    vi.unstubAllGlobals();
  });

  it("does not block writes when the running backend has a different build", async () => {
    // 构建身份拦截已下线：启动时自动重建前端 + pre-push 钩子把关，
    // 运行时不再误拦（历史行为是抛 409 build_identity_mismatch）。
    const fetchMock = vi.fn(async (
      _input: RequestInfo | URL,
      _init?: RequestInit,
    ) => response({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    expect(setBuildIdentity("stale-build")).toBe(false);

    await apiRequest("/api/task/cancel/run-1", { method: "POST" });

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/session",
      "/api/task/cancel/run-1",
    ]);
  });

  it("does not send the X-Boss-Build header anymore", async () => {
    const fetchMock = vi.fn(async (
      _input: RequestInfo | URL,
      _init?: RequestInit,
    ) => response({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    expect(setBuildIdentity(expectedBackendBuildHash)).toBe(true);

    await apiRequest("/api/task/cancel/run-1", { method: "POST" });

    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("X-Boss-Build")).toBeNull();
  });
});
