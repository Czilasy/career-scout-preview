import {
  continueTargets,
  canFinishRunByStatus,
  deriveScrapePrimaryAction,
  deriveScreenPrimaryAction,
  deriveTrackActionBar,
  isRoundClosedSaved,
  isResumableStatus,
  normalizeRoundContext,
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

  it("isRoundClosedSaved only matches interrupted + non-resumable", () => {
    expect(isRoundClosedSaved({ status: "interrupted", resumable: false })).toBe(true);
    expect(isRoundClosedSaved({ status: "interrupted", resumable: true })).toBe(false);
    expect(isRoundClosedSaved({ status: "paused", resumable: false })).toBe(false);
    expect(isRoundClosedSaved(null)).toBe(false);
    expect(isRoundClosedSaved(undefined)).toBe(false);
  });

  it("derives AI running as pause", () => {
    const action = deriveScreenPrimaryAction({
      screenStatus: "running", recrawlStatus: "", hasScreenRun: true, hasUncertain: false,
    });
    expect(action).toEqual({ kind: "pause", label: "暂停筛选" });
  });

  // 状态词表（v2 规格「状态词表」）：中断＝服务重启或人为停止，没有活体 worker，
  // 只能开新一轮。「继续」这条动作对中断轨道注定 503，树干不得再给它继续出口。
  it("derives paused/failed as continue and gives interrupted no continuation", () => {
    for (const status of ["paused", "failed"]) {
      const action = deriveScreenPrimaryAction({
        screenStatus: status, recrawlStatus: "", hasScreenRun: true, hasUncertain: false,
      });
      expect(action).toEqual({ kind: "continue", label: "继续 AI 筛选" });
    }
    expect(deriveScreenPrimaryAction({
      screenStatus: "interrupted", recrawlStatus: "", hasScreenRun: true, hasUncertain: false,
    }).kind).toBe("none");
  });

  // 重抓同理：中断的重抓没有活体批次可续，只能由新一轮重新发起。
  it("gives an interrupted recrawl no continuation while paused/failed still continue", () => {
    expect(deriveScreenPrimaryAction({
      screenStatus: "paused", recrawlStatus: "interrupted", hasScreenRun: true, hasUncertain: true,
    }).kind).toBe("none");
    for (const recrawlStatus of ["paused", "failed"]) {
      expect(deriveScreenPrimaryAction({
        screenStatus: "paused", recrawlStatus, hasScreenRun: true, hasUncertain: true,
      })).toEqual({ kind: "continue-recrawl", label: "继续重抓" });
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

  // 抓取主动作从 useDiscoveryExecution 的现场里抽出来：单平台与 Flow 轨道
  // 调用同一份，不再有第二套「进行中给暂停」的判定。
  it("derives the scrape primary action once for both the single-platform scene and a Track", () => {
    expect(deriveScrapePrimaryAction({ status: "running", hasTask: true, hasPausedRun: false, isBusy: false })).toEqual({ kind: "pause-scrape", label: "暂停" });
    expect(deriveScrapePrimaryAction({ status: "queued", hasTask: true, hasPausedRun: false, isBusy: false })).toEqual({ kind: "pause-scrape", label: "暂停" });
    expect(deriveScrapePrimaryAction({ status: "paused", hasTask: true, hasPausedRun: false, isBusy: false })).toEqual({ kind: "continue-scrape", label: "继续" });
    expect(deriveScrapePrimaryAction({ status: "interrupted", hasTask: true, hasPausedRun: false, isBusy: false }).kind).toBe("none");
    expect(deriveScrapePrimaryAction({ status: "succeeded", hasTask: true, hasPausedRun: false, isBusy: false }).kind).toBe("none");
    // 单平台现场没有 scrapeTaskId 时不给动作；轨道行的主体是轨道本身，见 deriveTrackActionBar。
    expect(deriveScrapePrimaryAction({ status: "queued", hasTask: false, hasPausedRun: false, isBusy: false }).kind).toBe("none");
    // 点下去的那一瞬保持原动作，不把人问成「再暂停一次」。
    expect(deriveScrapePrimaryAction({ status: "running", hasTask: true, hasPausedRun: false, isBusy: true, busyAction: "continue-scrape" })).toEqual({ kind: "continue-scrape", label: "继续" });
  });

  it("canFinishRunByStatus keeps the failed/interrupted close-out exit only for a real run", () => {
    expect(canFinishRunByStatus("run-1", "failed")).toBe(true);
    expect(canFinishRunByStatus("run-1", "interrupted")).toBe(true);
    expect(canFinishRunByStatus("", "interrupted")).toBe(false);
    expect(canFinishRunByStatus("run-1", "succeeded")).toBe(false);
    expect(canFinishRunByStatus("run-1", "running")).toBe(false);
  });

  // 轨道动作条 = 一平台一份：动作、结束并保存与终止显隐都只按这条线自己的事实算，
  // 轨道没有「开始 AI 筛选」和重抓这两个出口，终止本轨说清它只管这一条线。
  it("derives one action bar per Track stage from that Track's own facts", () => {
    expect(deriveTrackActionBar({ stage: "scrape", status: "running", runId: "scrape-a" })).toMatchObject({
      action: { kind: "pause-scrape", label: "暂停" },
      showFinishSave: true,
      showCancel: true,
      cancelLabel: "终止本轨",
    });
    expect(deriveTrackActionBar({ stage: "scrape", status: "paused", runId: "scrape-a" })).toMatchObject({
      action: { kind: "continue-scrape", label: "继续" },
      showFinishSave: true,
      showCancel: true,
    });
    expect(deriveTrackActionBar({ stage: "scrape", status: "interrupted", runId: "scrape-a" })).toMatchObject({
      action: { kind: "none" },
      showFinishSave: true,
      showCancel: false,
    });
    expect(deriveTrackActionBar({ stage: "scrape", status: "stopped", runId: "scrape-a" })).toMatchObject({
      action: { kind: "none" },
      showFinishSave: false,
      showCancel: false,
    });
    // 刚提交的排队轨：线还活着，暂停与终止必须在；这一轮还没有 run 可保存，
    // 收尾按钮不许做成点不动的假按钮。
    expect(deriveTrackActionBar({ stage: "scrape", status: "queued", runId: "" })).toMatchObject({
      action: { kind: "pause-scrape", label: "暂停" },
      showFinishSave: false,
      showCancel: true,
    });
    expect(deriveTrackActionBar({ stage: "screen", status: "running", runId: "screen-a" })).toMatchObject({
      action: { kind: "pause", label: "暂停筛选" },
      showFinishSave: true,
      showCancel: true,
    });
    expect(deriveTrackActionBar({ stage: "screen", status: "paused", runId: "screen-a" })).toMatchObject({
      action: { kind: "continue", label: "继续 AI 筛选" },
      showFinishSave: true,
      showCancel: true,
    });
    // 中断的 AI 段：没有活体 worker，既不给继续；但已判定的岗位还能保存，
    // 这一条线也还能终止——摘掉主动作不许顺手砍掉用户原有的两条出口。
    expect(deriveTrackActionBar({ stage: "screen", status: "interrupted", runId: "screen-a" })).toMatchObject({
      action: { kind: "none" },
      showFinishSave: true,
      showCancel: true,
    });
    expect(deriveTrackActionBar({ stage: "screen", status: "stopped", runId: "screen-a" })).toMatchObject({
      action: { kind: "none" },
      showFinishSave: false,
      showCancel: false,
    });
    // 03 里的轨道不发起筛选：「开始 AI 筛选」属于整轮入口，不出现在轨道行上。
    expect(deriveTrackActionBar({ stage: "screen", status: "", runId: "" }).action.kind).toBe("none");
    expect(deriveTrackActionBar({ stage: "screen", status: "succeeded", runId: "screen-a" }).action.kind).toBe("none");
  });

  it("continueTargets returns both platforms on all filter", () => {    const contexts: RoundContext[] = [
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
