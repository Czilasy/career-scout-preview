import {
  continueTargets,
  deriveScreenPrimaryAction,
  isResumableStatus,
  normalizeRoundContext,
  primaryActionLabel,
  roundConditionsRestored,
} from "../screenFlow";
import type { RoundContext } from "../types";

describe("screenFlow", () => {
  it("normalizes a partial round context payload", () => {
    const ctx = normalizeRoundContext({
      platform: "zhilian",
      keywords: ["Python", "后端"],
      cities: ["上海"],
      screening_fields: { salary: ["20-30K"] },
      profile_summary: "3年Python后端",
      profile_facts: { years: 3 },
      scrape_task_id: "scrape-1",
      screen_run_id: "screen-1",
      status: "paused",
      resumable: true,
      has_frozen_filters: true,
    });
    expect(ctx).toMatchObject({
      platform: "zhilian",
      keywords: ["Python", "后端"],
      cities: ["上海"],
      status: "paused",
      resumable: true,
      has_frozen_filters: true,
    });
    expect(normalizeRoundContext(null)).toBeNull();
    expect(normalizeRoundContext(undefined)).toBeNull();
  });

  it("recognizes resumable statuses", () => {
    for (const status of ["paused", "failed", "interrupted", "partial"]) {
      expect(isResumableStatus(status)).toBe(true);
    }
    expect(isResumableStatus("succeeded")).toBe(false);
    expect(isResumableStatus("running")).toBe(false);
  });

  it("derives AI running as pause", () => {
    const action = deriveScreenPrimaryAction({
      screenStatus: "running", recrawlStatus: "", hasScreenRun: true, hasUncertain: false,
    });
    expect(action).toEqual({ kind: "pause", label: "暂停筛选" });
  });

  it("derives paused/failed/interrupted as continue", () => {
    for (const status of ["paused", "failed", "interrupted"]) {
      const action = deriveScreenPrimaryAction({
        screenStatus: status, recrawlStatus: "", hasScreenRun: true, hasUncertain: false,
      });
      expect(action).toEqual({ kind: "continue", label: "继续 AI 筛选" });
    }
  });

  it("derives completed-with-pending as recrawl and clean completion as none", () => {
    expect(deriveScreenPrimaryAction({
      screenStatus: "partial", recrawlStatus: "", hasScreenRun: true, hasUncertain: true,
    })).toEqual({ kind: "recrawl", label: "全部重抓" });
    expect(deriveScreenPrimaryAction({
      screenStatus: "succeeded", recrawlStatus: "", hasScreenRun: true, hasUncertain: true,
    })).toEqual({ kind: "recrawl", label: "全部重抓" });
    expect(deriveScreenPrimaryAction({
      screenStatus: "succeeded", recrawlStatus: "", hasScreenRun: true, hasUncertain: false,
    }).kind).toBe("none");
  });

  it("derives start when the round never ran AI", () => {
    expect(deriveScreenPrimaryAction({
      screenStatus: "", recrawlStatus: "", hasScreenRun: false, hasUncertain: false,
    })).toEqual({ kind: "start", label: "开始 AI 筛选" });
    expect(deriveScreenPrimaryAction({
      screenStatus: "scraped_only", recrawlStatus: "", hasScreenRun: false, hasUncertain: false,
    })).toEqual({ kind: "start", label: "开始 AI 筛选" });
  });

  it("recrawl status takes precedence over screen status", () => {
    expect(deriveScreenPrimaryAction({
      screenStatus: "paused", recrawlStatus: "running", hasScreenRun: true, hasUncertain: true,
    })).toEqual({ kind: "pause-recrawl", label: "暂停重抓" });
    expect(deriveScreenPrimaryAction({
      screenStatus: "paused", recrawlStatus: "failed", hasScreenRun: true, hasUncertain: true,
    })).toEqual({ kind: "continue-recrawl", label: "继续重抓" });
  });

  it("maps primary action labels", () => {
    expect(primaryActionLabel({ kind: "start", label: "开始 AI 筛选" })).toBe("开始 AI 筛选");
    expect(primaryActionLabel({ kind: "none" })).toBe("");
  });

  it("continueTargets returns both platforms on all filter", () => {
    const contexts: RoundContext[] = [
      { platform: "boss", keywords: [], cities: [], screening_fields: {}, profile_summary: "", profile_facts: {}, scrape_task_id: "a", screen_run_id: "1", status: "paused", resumable: true },
      { platform: "zhilian", keywords: [], cities: [], screening_fields: {}, profile_summary: "", profile_facts: {}, scrape_task_id: "b", screen_run_id: "2", status: "paused", resumable: true },
      { platform: "boss", keywords: [], cities: [], screening_fields: {}, profile_summary: "", profile_facts: {}, scrape_task_id: "c", screen_run_id: "3", status: "succeeded", resumable: false },
    ];
    expect(continueTargets(contexts, "all")).toEqual(["boss", "zhilian"]);
    expect(continueTargets(contexts, "boss")).toEqual(["boss"]);
    expect(continueTargets(contexts, "zhilian")).toEqual(["zhilian"]);
  });

  it("continueTargets returns a single platform when only one side is resumable", () => {
    const contexts: RoundContext[] = [
      { platform: "boss", keywords: [], cities: [], screening_fields: {}, profile_summary: "", profile_facts: {}, scrape_task_id: "a", screen_run_id: "1", status: "paused", resumable: true },
      { platform: "zhilian", keywords: [], cities: [], screening_fields: {}, profile_summary: "", profile_facts: {}, scrape_task_id: "b", screen_run_id: "2", status: "succeeded", resumable: false },
    ];
    expect(continueTargets(contexts, "all")).toEqual(["boss"]);
  });

  it("2993: missing frozen filters on an existing AI round blocks continuation", () => {
    expect(roundConditionsRestored({
      platform: "boss", keywords: [], cities: [], screening_fields: {}, profile_summary: "",
      profile_facts: {}, scrape_task_id: "a", screen_run_id: "1", status: "paused",
      resumable: true, has_frozen_filters: true,
    })).toBe(false);
    expect(roundConditionsRestored({
      platform: "boss", keywords: [], cities: [], screening_fields: { salary: ["20-30K"] },
      profile_summary: "", profile_facts: {}, scrape_task_id: "a", screen_run_id: "1",
      status: "paused", resumable: true, has_frozen_filters: true,
    })).toBe(true);
    expect(roundConditionsRestored(null)).toBe(true);
  });
});
