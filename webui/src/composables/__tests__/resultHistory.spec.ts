import { flushPromises } from "@vue/test-utils";
import { useResultHistory, type HistoryRoundItem } from "../resultHistory";

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
      if (url.endsWith("/api/result-history/h1")) {
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
      if (url.endsWith("/api/result-history/h1") && init?.method === "DELETE") {
        return response({ ok: true, deleted: true, run_id: "h1" });
      }
      if (url.endsWith("/api/result-history/h1")) {
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
    const archived = await history.archiveLatest();
    expect(archived).toEqual(["h1"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/result-history/archive-latest",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("archiveLatest rejects when the API fails", async () => {
    const fetchMock = vi.fn(async () => response({ ok: false, error: "persistence_failed" }, 500));
    vi.stubGlobal("fetch", fetchMock);
    const history = useResultHistory();
    await expect(history.archiveLatest()).rejects.toThrow();
  });
});
