import {
  buildSearchScriptParams,
  classifyTaskSize,
  normalizeScopePreview,
  partitionPipelineResult,
  recoverSelectionSettings,
} from "../discovery";
import type { AdvancedSettingsState, ScopePreviewResponse } from "../types";

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
    [19, "medium"],
    [20, "large"],
    [30, "large"],
  ] as const)("classifies %i planned pages as %s", (pages, expected) => {
    expect(classifyTaskSize(pages)).toBe(expected);
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

  it("restores all nine recent custom fields without pages", () => {
    const settings = {
      inter_combo_delay: 10,
      detail_batch_size: 15,
      detail_interval: 2,
      detail_reset_every: 4,
      detail_batch_cooldown: 5,
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
