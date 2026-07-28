import { afterEach, describe, expect, it, vi } from "vitest";
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

  it("blocks writes locally when the running backend has a different build", async () => {
    const fetchMock = vi.fn(async (
      _input: RequestInfo | URL,
      _init?: RequestInit,
    ) => response({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    expect(setBuildIdentity("stale-build")).toBe(false);

    await expect(apiRequest("/api/task/cancel/run-1", { method: "POST" }))
      .rejects.toMatchObject({ status: 409 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the embedded build identity after the backend matches", async () => {
    const fetchMock = vi.fn(async (
      _input: RequestInfo | URL,
      _init?: RequestInit,
    ) => response({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    expect(setBuildIdentity(expectedBackendBuildHash)).toBe(true);

    await apiRequest("/api/task/cancel/run-1", { method: "POST" });

    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("X-Boss-Build")).toBe(expectedBackendBuildHash);
  });
});
