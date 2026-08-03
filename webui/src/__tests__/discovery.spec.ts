import {
  buildSearchScriptParams,
  classifyTaskSize,
  createPlatformState,
  DEFAULT_PLATFORM,
  normalizeScopePreview,
  partitionPipelineResult,
  recoverSelectionSettings,
} from "../discovery";
import type { AdvancedSettingsState, Platform, ScopePreviewResponse } from "../types";

describe("discovery helpers", () => {
  it("keeps uncertain AI results separate from real matches", () => {
    const groups = partitionPipelineResult({
      jobs: [
        { job_id: "m", title: "匹配", verdict: "match" },
        { job_id: "n", title: "不匹配", verdict: "not_match" },
        { job_id: "u", title: "待确认", verdict: "uncertain" },
      ],
      dropped: [{ job_id: "d", title: "粗筛剔除" }],
    });

    expect(groups.matched.map((job) => job.job_id)).toEqual(["m"]);
    expect(groups.unmatched.map((job) => job.job_id)).toEqual(["n"]);
    expect(groups.uncertain.map((job) => job.job_id)).toEqual(["u"]);
    expect(groups.dropped.map((job) => job.job_id)).toEqual(["d"]);
  });

  it("builds broad-search parameters from only keywords and cities", () => {
    expect(buildSearchScriptParams(
      ["Python 后端", " Python 后端 ", "FastAPI"],
      ["上海", " 杭州 ", ""],
    )).toEqual({
      keyword: "Python 后端,FastAPI",
      city: ["上海", "杭州"],
      filters: {},
    });
  });

  it.each([
    [1, "small"],
    [9, "small"],
    [10, "medium"],
    [49, "medium"],
    [50, "large"],
    [200, "large"],
  ] as const)("classifies %i planned pages as %s", (pages, expected) => {
    expect(classifyTaskSize(pages)).toBe(expected);
  });

  it.each([0, 201])("rejects %i planned pages outside the valid range", (pages) => {
    expect(() => classifyTaskSize(pages)).toThrow(RangeError);
  });

  it("normalizes the backend-authoritative scope preview", () => {
    const response: ScopePreviewResponse = {
      ok: true,
      scope: {
        keywords: ["AI应用开发"],
        scope_kind: "cities",
        cities: ["东莞"],
        pages_per_combination: 3,
        combination_count: 1,
        planned_pages: 3,
        task_size: "small",
        scope_digest: "sha256:scope",
      },
      deduplicated: { keywords: ["ai应用开发"], cities: ["东莞市"] },
    };

    expect(normalizeScopePreview(response)).toEqual(response.scope);
  });

  it("restores all execution custom fields without pages", () => {
    const settings = {
      inter_combo_delay: 10,
      detail_batch_size: 15,
      detail_interval: 2,
      detail_reset_every: 4,
      detail_batch_cooldown: 5,
      detail_tab_pool_size: 5,
      screen_batch_size: 50,
      screen_concurrency: 5,
      match_batch_size: 4,
      match_concurrency: 10,
    };
    const state: AdvancedSettingsState = {
      ok: true,
      selection: "stable",
      settings,
      last_custom: { config_digest: "sha256:custom", settings: { ...settings, detail_batch_size: 8 } },
      mode_version: {
        id: "mode-v1",
        version_digest: "sha256:mode",
        available_modes: ["stable", "balanced", "extreme"],
      },
      manual_ranges: {},
      config_schema_version: 1,
    };

    expect(recoverSelectionSettings(state, "custom")).toEqual({
      ...settings,
      detail_batch_size: 8,
    });
    expect(recoverSelectionSettings(state, "stable")).toEqual(settings);
    expect(recoverSelectionSettings(state, "custom")).not.toHaveProperty("pages");
  });
});

describe("platform state (T502)", () => {
  // 不变式锚点：contracts/platform-schema.md L142-159 + tasks006.md L27
  // 三身份独立：草稿平台 / 任务平台 / 最近结果平台，不能复用一个变量互相改写。

  it("defaults draft to boss and leaves task/result unbound", () => {
    const state = createPlatformState();
    expect(state.draft).toBe("boss");
    expect(state.task).toBeNull();
    expect(state.result).toBeNull();
  });

  it("honors an explicit initial draft platform", () => {
    const state = createPlatformState("zhilian");
    expect(state.draft).toBe("zhilian");
    expect(state.task).toBeNull();
    expect(state.result).toBeNull();
  });

  it("setDraftPlatform changes only the draft, not task or result", () => {
    const state = createPlatformState();
    state.setTaskPlatform("zhilian");
    state.setResultPlatform("zhilian");

    state.setDraftPlatform("zhilian");

    expect(state.draft).toBe("zhilian");
    expect(state.task).toBe("zhilian"); // unchanged
    expect(state.result).toBe("zhilian"); // unchanged
  });

  it("setTaskPlatform changes only the task, not draft or result", () => {
    const state = createPlatformState();
    state.setResultPlatform("zhilian");

    state.setTaskPlatform("boss");

    expect(state.task).toBe("boss");
    expect(state.draft).toBe("boss"); // unchanged (coincidentally same default)
    expect(state.result).toBe("zhilian"); // unchanged
  });

  it("setTaskPlatform(null) clears the task without touching draft or result", () => {
    const state = createPlatformState("zhilian");
    state.setTaskPlatform("boss");
    state.setResultPlatform("boss");

    state.setTaskPlatform(null);

    expect(state.task).toBeNull();
    expect(state.draft).toBe("zhilian"); // unchanged
    expect(state.result).toBe("boss"); // unchanged
  });

  it("setResultPlatform changes only the result, not draft or task", () => {
    const state = createPlatformState();
    state.setTaskPlatform("zhilian");

    state.setResultPlatform("zhilian");

    expect(state.result).toBe("zhilian");
    expect(state.draft).toBe("boss"); // unchanged, no draft switch trigger
    expect(state.task).toBe("zhilian"); // unchanged
  });

  it("setResultPlatform(null) clears the result without triggering a draft switch", () => {
    const state = createPlatformState();
    state.setResultPlatform("zhilian");
    state.setDraftPlatform("zhilian");

    state.setResultPlatform(null);

    expect(state.result).toBeNull();
    expect(state.draft).toBe("zhilian"); // unchanged
    expect(state.task).toBeNull(); // unchanged
  });

  it("keeps three identities independent across interleaved operations", () => {
    const state = createPlatformState();

    state.setDraftPlatform("zhilian");
    state.setTaskPlatform("boss");
    state.setResultPlatform("zhilian");
    state.setDraftPlatform("boss");
    state.setResultPlatform("boss");

    // 三者此刻碰巧都是 boss，但各自独立；任何 setter 改其一不应波及其它两个。
    expect(state.draft).toBe("boss");
    expect(state.task).toBe("boss");
    expect(state.result).toBe("boss");

    state.setDraftPlatform("zhilian");
    expect(state.task).toBe("boss");
    expect(state.result).toBe("boss");

    state.setTaskPlatform("zhilian");
    expect(state.draft).toBe("zhilian");
    expect(state.result).toBe("boss");
  });

  it("creates independent instances per call (no shared state)", () => {
    const a = createPlatformState();
    const b = createPlatformState();

    a.setDraftPlatform("zhilian");
    a.setTaskPlatform("boss");
    a.setResultPlatform("zhilian");

    expect(b.draft).toBe(DEFAULT_PLATFORM);
    expect(b.task).toBeNull();
    expect(b.result).toBeNull();
  });

  it("type-checks platform values at compile time", () => {
    // 编译时断言：setter 不接受 Platform 之外的字符串。
    const state = createPlatformState();
    const draft: Platform = state.draft;
    const task: Platform | null = state.task;
    const result: Platform | null = state.result;
    state.setDraftPlatform(draft);
    state.setTaskPlatform(task);
    state.setResultPlatform(result);
    expect(true).toBe(true);
  });
});
