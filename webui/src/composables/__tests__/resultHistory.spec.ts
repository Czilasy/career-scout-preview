import { flushPromises } from "@vue/test-utils";
import {
  setHistoryProfile,
  useResultHistory,
  type HistoryRoundItem,
} from "../resultHistory";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function item(overrides: Partial<HistoryRoundItem> = {}): HistoryRoundItem {
  return {
    run_id: "h1",
    platform: "boss",
    status: "done",
    created_at: "2026-08-11 10:00:00",
    started_at: null,
    finished_at: null,
    total_scraped: 10,
    total_kept: 4,
    total_matched: 3,
    mismatch_count: 2,
    total_dropped: 6,
    pending_count: 1,
    keyword_summary: "Python 后端 / 上海",
    profile_summary_preview: "3年Python后端",
    archived_at: null,
    is_latest: true,
    ...overrides,
  };
}

describe("useResultHistory", () => {
  afterEach(() => {
    setHistoryProfile("");
    vi.unstubAllGlobals();
  });

  it("loads the history list when shown", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/result-history")) {
        return response({ ok: true, items: [item(), item({ run_id: "h2" })] });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    history.hide();
    history.show();
    await flushPromises();
    expect(history.open.value).toBe(true);
    expect(history.items.value).toHaveLength(2);
  });

  it("opens a round detail and closes the drawer", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1"))) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "h1",
          platform: "boss",
          status: "failed",
          result: { jobs: [], total_kept: 2 },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    history.show();
    await flushPromises();
    await history.openRound("h1");
    await flushPromises();
    expect(history.open.value).toBe(false);
    expect(history.detail.value?.status).toBe("failed");
    expect(history.detail.value?.source_run_id).toBe("h1");
  });

  it("deletes a round and clears the currently opened detail", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1")) && init?.method === "DELETE") {
        return response({ ok: true, deleted: true, run_id: "h1" });
      }
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1"))) {
        return response({ ok: true, has_result: true, source_run_id: "h1", platform: "boss", status: "failed", result: { jobs: [], total_kept: 2 } });
      }
      if (url.includes("/api/result-history")) {
        return response({ ok: true, items: [] });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    history.detail.value = null;
    await history.openRound("h1");
    await flushPromises();
    await history.deleteRound(item());
    await flushPromises();
    expect(history.detail.value).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/result-history/h1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("removes the deleted round locally and reloads without flashing the loading state", async () => {
    let resolveList: (value: Response) => void = () => {};
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1")) && init?.method === "DELETE") {
        return response({ ok: true, deleted: true, run_id: "h1" });
      }
      if (url.includes("/api/result-history")) {
        return new Promise<Response>((resolve) => {
          resolveList = resolve;
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    history.items.value = [item(), item({ run_id: "h2" })];
    history.loading.value = false;
    const pending = history.deleteRound(item());
    await flushPromises();
    expect(history.items.value.map((round) => round.run_id)).toEqual(["h2"]);
    expect(history.loading.value).toBe(false);
    resolveList(response({ ok: true, items: [item({ run_id: "h3" })] }));
    await pending;
    await flushPromises();
    expect(history.items.value.map((round) => round.run_id)).toEqual(["h3"]);
  });

  it("archives current rounds through the history API", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/result-history/archive-latest")) {
        return response({ ok: true, archived_run_ids: ["h1"] });
      }
      if (url.includes("/api/result-history")) {
        return response({ ok: true, items: [] });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    const archived = await history.archiveAllCurrentResults();
    expect(archived).toEqual(["h1"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/result-history/archive-latest",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("archiveAllCurrentResults rejects when the API fails", async () => {
    const fetchMock = vi.fn(async () => response({ ok: false, error: "persistence_failed" }, 500));
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    await expect(history.archiveAllCurrentResults()).rejects.toThrow();
  });

  it("scopes history requests to the current profile and swaps slots on switch", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/result-history?profile_id=profile-a")) {
        return response({ ok: true, items: [item({ run_id: "a1" })] });
      }
      if (url.includes("/api/result-history?profile_id=profile-b")) {
        return response({ ok: true, items: [item({ run_id: "b1" })] });
      }
      return response({ ok: true, items: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    setHistoryProfile("profile-a");
    await history.loadHistory();
    expect(history.items.value.map((round) => round.run_id)).toEqual(["a1"]);

    history.detail.value = {
      ok: true, has_result: true, source_run_id: "a1", platform: "boss",
      status: "done", result: { jobs: [] },
    } as never;
    setHistoryProfile("profile-b");
    expect(history.items.value).toEqual([]);
    expect(history.detail.value).toBeNull();

    await history.loadHistory();
    expect(history.items.value.map((round) => round.run_id)).toEqual(["b1"]);
  });

  it("ignores a late list response from the previous profile", async () => {
    let resolveOld: (value: Response) => void = () => {};
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("profile_id=profile-a")) {
        return new Promise<Response>((resolve) => { resolveOld = resolve; });
      }
      if (url.includes("profile_id=profile-b")) {
        return response({ ok: true, items: [item({ run_id: "b1" })] });
      }
      return response({ ok: true, items: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    setHistoryProfile("profile-a");
    const pending = history.loadHistory();
    setHistoryProfile("profile-b");
    await history.loadHistory();
    resolveOld(response({ ok: true, items: [item({ run_id: "a-late" })] }));
    await pending;
    await flushPromises();
    expect(history.items.value.map((round) => round.run_id)).toEqual(["b1"]);
  });

  it("sends the archive request with the current profile id", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.endsWith("/api/result-history/archive-latest")) {
        return response({ ok: true, archived_run_ids: [] });
      }
      return response({ ok: true, items: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    setHistoryProfile("profile-c");
    await history.archiveAllCurrentResults();
    const archiveCall = calls.find((call) => call.url.endsWith("/api/result-history/archive-latest"));
    expect(JSON.parse(String(archiveCall?.init?.body))).toEqual({ profile_id: "profile-c" });
  });

  // B099：切轮次不是「回到最新」。清空展示会被当成回最新而触发第二条
  // 异步加载，两条加载赛跑导致「先闪最新、点急了停在最新」。
  it("keeps the current detail while another round loads", async () => {
    let resolveDetail: (value: Response) => void = () => {};
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1"))) {
        return response({
          ok: true, has_result: true, source_run_id: "h1", platform: "boss",
          status: "done", result: { jobs: [], total_kept: 1 },
        });
      }
      if ((url.includes("/api/result-history/h2?") || url.endsWith("/api/result-history/h2"))) {
        return new Promise<Response>((resolve) => { resolveDetail = resolve; });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    await history.openRound("h1");
    expect(history.detail.value?.source_run_id).toBe("h1");

    const pending = history.openRound("h2");
    await flushPromises();
    expect(history.detail.value?.source_run_id).toBe("h1");

    resolveDetail(response({
      ok: true, has_result: true, source_run_id: "h2", platform: "boss",
      status: "done", result: { jobs: [], total_kept: 2 },
    }));
    await pending;
    expect(history.detail.value?.source_run_id).toBe("h2");
  });

  it("ignores a late response for a round the user switched away from", async () => {
    let resolveFirst: (value: Response) => void = () => {};
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if ((url.includes("/api/result-history/h1?") || url.endsWith("/api/result-history/h1"))) {
        return new Promise<Response>((resolve) => { resolveFirst = resolve; });
      }
      if ((url.includes("/api/result-history/h2?") || url.endsWith("/api/result-history/h2"))) {
        return response({
          ok: true, has_result: true, source_run_id: "h2", platform: "boss",
          status: "done", result: { jobs: [], total_kept: 2 },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    const first = history.openRound("h1");
    await flushPromises();
    await history.openRound("h2");
    expect(history.detail.value?.source_run_id).toBe("h2");

    resolveFirst(response({
      ok: true, has_result: true, source_run_id: "h1", platform: "boss",
      status: "done", result: { jobs: [], total_kept: 1 },
    }));
    await first;
    await flushPromises();
    expect(history.detail.value?.source_run_id).toBe("h2");
  });

  it("backToLatest discards an in-flight round load", async () => {
    let resolveDetail: (value: Response) => void = () => {};
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/result-history/h1")) {
        return new Promise<Response>((resolve) => { resolveDetail = resolve; });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    const pending = history.openRound("h1");
    await flushPromises();
    history.backToLatest();

    resolveDetail(response({
      ok: true, has_result: true, source_run_id: "h1", platform: "boss",
      status: "done", result: { jobs: [], total_kept: 1 },
    }));
    await pending;
    await flushPromises();
    expect(history.detail.value).toBeNull();
  });
});
