// Spec041 返工（用户拍板 2026-09-13）：第 2 页的搜索输入是"一份、复用的输入"。
//
// 上一版按"两个平台互不覆盖"做成两格，且只存当前标签页；真机上出现"切平台看不到、
// 关标签页/清站点数据后两个平台都空"。现在改为：关键词/城市只有一份，两个平台共用，
// 存 localStorage（跨标签页、跨重启），只有"开新一轮"或用户清空才变。
// 本文件覆盖：共用往返、修改生效方向、本地存档、范围预览乱序、清空语义。
// 注意：第 3 页筛选草稿与区县/商圈草稿仍按平台各存各的（区县码各平台不同，不能通用）。
import { nextTick } from "vue";
import type { ScopePreviewRequest } from "../../types";
import { apiRequest, settingsApi } from "../../api";
import type { SearchNeeds } from "../discoveryDeps";
import { useDiscoverySearch } from "../useDiscoverySearch";
import { useDiscoveryState } from "../useDiscoveryState";
import {
  hydratePage2Draft,
  setPage2DraftRemoteEnabled,
  useSearchDraftSlots,
} from "../useSearchDraftSlots";

vi.mock("../../api", () => ({
  ApiError: class ApiError extends Error {},
  apiRequest: vi.fn(async () => ({})),
  errorMessage: (_error: unknown, fallback: string) => fallback,
  settingsApi: {
    previewScope: vi.fn(),
    selectMode: vi.fn(),
    getAdvancedSettings: vi.fn(),
    saveCustom: vi.fn(),
  },
  userFacingMessage: (_error: unknown, fallback: string) => fallback,
}));

const previewScopeMock = settingsApi.previewScope as unknown as ReturnType<typeof vi.fn>;
const apiRequestMock = apiRequest as unknown as ReturnType<typeof vi.fn>;

function makeProfileId(): string {
  return `draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function makeSearchDeps(profileId: string): SearchNeeds {
  return {
    cancelActiveTasksForNewRound: vi.fn(async () => true),
    clearLatestResult: vi.fn(async () => true),
    enterSearchStep: vi.fn(),
    notify: vi.fn(),
    openOneClickDialog: vi.fn(),
    props: { profileId },
    restoreRunningTask: vi.fn(async () => {}),
    startScrape: vi.fn(async () => {}),
  };
}

function district(platform: "boss" | "zhilian", city: string, name: string, code: string) {
  return {
    platform,
    city_name: city,
    city_code: "440300",
    district_name: name,
    district_code: code,
  };
}

describe("搜索输入一份、两平台共用（Spec041 返工）", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    // 默认关掉数据库同步：只有专门测同步的用例才打开（防抖计时器会跨用例残留）。
    setPage2DraftRemoteEnabled(false);
    apiRequestMock.mockReset();
    apiRequestMock.mockResolvedValue({});
    previewScopeMock.mockReset();
    previewScopeMock.mockResolvedValue({
      ok: true,
      scope: {
        keywords: ["AI应用开发"],
        scope_kind: "cities",
        cities: ["深圳"],
        pages_per_combination: 3,
        combination_count: 1,
        planned_pages: 3,
        task_size: "small",
        scope_digest: "digest-1",
      },
      deduplicated: { keywords: [], cities: [] },
    });
  });

  it("第 2 页输入一份、两平台共用：切平台内容不变，且存进本地存档", async () => {
    const profileId = makeProfileId();
    const state = useDiscoveryState({ profileId }, () => {});
    const search = useDiscoverySearch(state, makeSearchDeps(profileId));
    // 与真实启动一致：恢复流程把工作副本挂上（之后编辑才落存档）。
    state.ensureSearchDraftLoaded();
    await nextTick();

    // ---- 1. 在 BOSS 上填输入 ----
    state.keywords.value = [{ word: "AI应用开发", recommended: true }];
    state.selectedKeywords.value = ["AI应用开发"];
    state.customKeyword.value = "AI应用开发草稿";
    state.cityText.value = "深圳";
    state.customCity.value = "深圳草稿";
    state.locationDraft.setLocations("boss", "深圳", [
      district("boss", "深圳", "福田区", "440304"),
    ]);
    state.filterValues.value.boss.salary = ["406"];
    await nextTick();

    // ---- 2. 切到智联：同一份输入带过去（不再另起一格、也不清空） ----
    search.setDraftPlatform("zhilian");
    await nextTick();
    expect(state.draftPlatform.value).toBe("zhilian");
    expect(state.keywords.value).toEqual([{ word: "AI应用开发", recommended: true }]);
    expect(state.selectedKeywords.value).toEqual(["AI应用开发"]);
    expect(state.customKeyword.value).toBe("AI应用开发草稿");
    expect(state.cityText.value).toBe("深圳");
    expect(state.customCity.value).toBe("深圳草稿");
    // 第 3 页筛选草稿与区县草稿仍按平台各存各的（区县码各平台不同）。
    expect(state.filterValues.value.zhilian).toEqual({});
    expect(state.locationDraft.getLocations("zhilian", "深圳")).toEqual([]);

    // ---- 3. 在智联上改城市：改的是同一份 ----
    state.cityText.value = "广州";
    state.customCity.value = "广州草稿";
    await nextTick();

    // ---- 4. 切回 BOSS：还是这一份（广州），不会被换回旧值 ----
    search.setDraftPlatform("boss");
    await nextTick();
    expect(state.cityText.value).toBe("广州");
    expect(state.customCity.value).toBe("广州草稿");

    // ---- 5. 存档落在 localStorage（关标签页/新会话仍在），而且只有一份 ----
    const persisted = JSON.parse(
      localStorage.getItem(`career-scout-search-draft:${profileId}`) as string,
    );
    expect(persisted.cityText).toBe("广州");
    expect(persisted.keywords).toEqual([{ word: "AI应用开发", recommended: true }]);
    expect(persisted.boss).toBeUndefined();

    // 新页面实例（刷新 / 重开标签页）两个平台取回同一份。
    const reloaded = useDiscoveryState({ profileId }, () => {});
    reloaded.loadSearchDraftFor("zhilian");
    expect(reloaded.cityText.value).toBe("广州");
    reloaded.loadSearchDraftFor("boss");
    expect(reloaded.cityText.value).toBe("广州");
  });

  it("切平台后旧平台的范围预览响应不得覆盖新平台", async () => {
    const profileId = makeProfileId();
    const bossGate = { release: () => {} };
    previewScopeMock.mockImplementation(async (payload: ScopePreviewRequest) => {
      if (payload.platform === "boss") {
        await new Promise<void>((resolve) => { bossGate.release = resolve; });
      }
      return {
        ok: true,
        scope: {
          keywords: payload.keywords,
          scope_kind: "cities",
          cities: payload.cities,
          pages_per_combination: payload.pages_per_combination,
          combination_count: 1,
          planned_pages: payload.pages_per_combination,
          task_size: "small",
          scope_digest: `digest-${payload.platform}`,
        },
        deduplicated: { keywords: [], cities: [] },
      };
    });

    const state = useDiscoveryState({ profileId }, () => {});
    const search = useDiscoverySearch(state, makeSearchDeps(profileId));
    state.ensureSearchDraftLoaded();
    await nextTick();
    state.keywords.value = [{ word: "AI应用开发", recommended: false }];
    state.selectedKeywords.value = ["AI应用开发"];
    state.cityText.value = "深圳";

    const bossPending = search.refreshScopePreview(); // BOSS 请求在飞
    await nextTick();

    // 用户切到智联（智联没有关键词，不会发新请求）
    search.setDraftPlatform("zhilian");
    await nextTick();
    expect(state.scopePreview.value).toBeNull();

    // 旧平台的响应此刻才回来：必须被丢弃，不写进新平台。
    bossGate.release();
    await bossPending;
    await nextTick();
    expect(state.scopePreview.value).toBeNull();
    expect(state.draftPlatform.value).toBe("zhilian");
  });

  it("清空关键词后范围预览不残留「忙」标记", async () => {
    const profileId = makeProfileId();
    const gate = { release: () => {} };
    previewScopeMock.mockImplementation(async (payload: ScopePreviewRequest) => {
      await new Promise<void>((resolve) => { gate.release = resolve; });
      return {
        ok: true,
        scope: {
          keywords: payload.keywords,
          scope_kind: "cities",
          cities: payload.cities,
          pages_per_combination: payload.pages_per_combination,
          combination_count: 1,
          planned_pages: payload.pages_per_combination,
          task_size: "small",
          scope_digest: "digest-hold",
        },
        deduplicated: { keywords: [], cities: [] },
      };
    });

    const state = useDiscoveryState({ profileId }, () => {});
    const search = useDiscoverySearch(state, makeSearchDeps(profileId));
    state.ensureSearchDraftLoaded();
    await nextTick();
    state.keywords.value = [{ word: "AI应用开发", recommended: false }];
    state.selectedKeywords.value = ["AI应用开发"];

    const pending = search.refreshScopePreview(); // 请求在飞
    await nextTick();
    expect(state.scopePreviewBusy.value).toBe(true);

    // 响应回来前清空关键词：这次调用推进请求序号并提前返回，在飞的旧请求
    // 已过期、不会再清「忙」，必须由这次调用自己清干净。
    state.selectedKeywords.value = [];
    void search.refreshScopePreview();
    await nextTick();
    expect(state.scopePreviewBusy.value).toBe(false);

    gate.release();
    await pending;
    await nextTick();
    expect(state.scopePreviewBusy.value).toBe(false);
  });

  it("共用输入：两个平台读到同一份，清空后两平台都为空", () => {
    const profileId = makeProfileId();
    const slots = useSearchDraftSlots(profileId);
    slots.set("boss", { cityText: "深圳", selectedKeywords: ["AI"] });
    expect(slots.slot("boss").cityText).toBe("深圳");
    expect(slots.slot("zhilian").cityText).toBe("深圳");

    slots.set("zhilian", { cityText: "广州" });
    expect(slots.slot("boss").cityText).toBe("广州");

    slots.reset("boss");
    expect(slots.slot("boss").cityText).toBe("");
    expect(slots.slot("zhilian").cityText).toBe("");
    expect(slots.hasStoredSlots()).toBe(true);

    slots.resetAll();
    expect(slots.slot("boss").cityText).toBe("");
  });

  it("打开应用用数据库那份回填；本地已有内容则以本地为准", () => {
    const profileId = makeProfileId();
    // 本地空（换浏览器 / 清过站点数据）→ 用数据库那份回填。
    hydratePage2Draft(profileId, {
      keywords: [{ word: "数据库关键词", recommended: true }],
      selectedKeywords: ["数据库关键词"],
      cityText: "杭州",
      profileSummary: "数据库画像",
    });
    const state = useDiscoveryState({ profileId }, () => {});
    state.loadSearchDraftFor("boss");
    expect(state.keywords.value).toEqual([{ word: "数据库关键词", recommended: true }]);
    expect(state.cityText.value).toBe("杭州");
    expect(state.profileSummary.value).toBe("数据库画像");
    // 两个平台共用同一份。
    state.loadSearchDraftFor("zhilian");
    expect(state.cityText.value).toBe("杭州");

    // 另一个画像：本地先有内容 → 以本地为准，数据库那份不覆盖。
    const localFirst = makeProfileId();
    const slots = useSearchDraftSlots(localFirst);
    slots.set("boss", { cityText: "本地城市" });
    hydratePage2Draft(localFirst, { cityText: "数据库城市" });
    expect(slots.slot("boss").cityText).toBe("本地城市");
  });

  it("输入停下来后把第 2 页输入写进数据库（按画像 PATCH）", async () => {
    vi.useFakeTimers();
    try {
      setPage2DraftRemoteEnabled(true);
      const profileId = makeProfileId();
      const slots = useSearchDraftSlots(profileId);
      slots.set("boss", { cityText: "深圳" });
      vi.advanceTimersByTime(700);

      const patch = apiRequestMock.mock.calls.find(
        ([url, init]) => String(url) === `/api/profiles/${profileId}`
          && (init as { method?: string } | undefined)?.method === "PATCH",
      );
      expect(patch).toBeTruthy();
      const body = (patch?.[1] as { json: { page2_draft: { cityText: string } } }).json;
      expect(body.page2_draft.cityText).toBe("深圳");
    } finally {
      setPage2DraftRemoteEnabled(false);
      vi.useRealTimers();
    }
  });
});
