import { useDiscoveryState } from "../useDiscoveryState";
import { useDiscoveryResults } from "../useDiscoveryResults";
import type { ResultsNeeds, RoundFlowLike } from "../discoveryDeps";
import { apiRequest } from "../../api";
import { setThemePlatform } from "../useTheme";

vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
  errorMessage: (_error: unknown, fallback: string) => fallback,
  settingsApi: { get: vi.fn(), save: vi.fn() },
  userFacingMessage: (_error: unknown, fallback: string) => fallback,
  ApiError: class ApiError extends Error {},
}));

const apiRequestMock = apiRequest as unknown as ReturnType<typeof vi.fn>;

const roundFlow: RoundFlowLike = {
  busyAction: "",
  roundContext: null,
  roundContexts: {},
  suppressProfileWatch: false,
  startRecrawl: vi.fn(async () => {}),
  clearRoundContext: vi.fn(),
  restoreRoundContext: vi.fn(() => false),
  registerRoundContext: vi.fn(),
};

function makeDeps(): ResultsNeeds {
  return {
    emit: vi.fn(),
    notify: vi.fn(),
    pollRecrawl: vi.fn(async () => {}),
    pollTask: vi.fn(async () => {}),
    props: { profileId: "platform-result-test" },
    roundFlow,
    setDraftPlatform: vi.fn(),
  };
}

function result(platform: "boss" | "zhilian", runId: string) {
  return {
    ok: true,
    has_result: true,
    source_run_id: runId,
    platform,
    status: "scraped_only",
    started_at: 2_000,
    finished_at: 3_000,
    result: {
      ok: true,
      jobs: [{
        job_id: `${platform}-job`,
        platform,
        title: `${platform} 岗位`,
        verdict: "",
      }],
      dropped: [],
      total_scraped: 1,
      total_kept: 0,
      total_matched: 0,
      total_dropped: 0,
    },
  };
}

describe("useDiscoveryResults latest platform identity", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
    document.documentElement.setAttribute("data-platform", "boss");
    setThemePlatform("boss");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("binds the result theme to the newest platform without changing existing result loading", async () => {
    const boss = { ...result("boss", "boss-old"), started_at: 1_000 };
    const zhilian = result("zhilian", "zhilian-latest");
    apiRequestMock.mockImplementation(async (url: string) => {
      if (url.includes("platform=boss")) return boss;
      if (url.includes("platform=zhilian")) return zhilian;
      return zhilian;
    });

    const state = useDiscoveryState({ profileId: "platform-result-test" }, () => {});
    const results = useDiscoveryResults(state, makeDeps());

    await results.loadLatestResult();

    const latestCalls = apiRequestMock.mock.calls.filter(([url]) =>
      String(url).includes("/api/latest-pipeline-result"));
    expect(latestCalls).toHaveLength(2);
    expect(state.pipelineResult.value?.jobs?.map((job) => job.platform)).toEqual(["boss", "zhilian"]);
    expect(state.platformState.result).toBe("zhilian");
    expect(document.documentElement.getAttribute("data-platform")).toBe("zhilian");
  });
});
