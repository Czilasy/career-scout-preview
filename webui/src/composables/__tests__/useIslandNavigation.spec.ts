import { describe, expect, it } from "vitest";
import { deriveTarget, reachableStep, useIslandNavigation } from "../useIslandNavigation";

const baseInput = {
  taskPhase: "none" as const,
  stuckAt: "none" as const,
  enabledSteps: new Set(["upload", "search", "screen", "results"]),
  historyMode: false,
  bootstrapping: false,
};

describe("useIslandNavigation", () => {
  it("按灵动岛状态落到正确的页面", () => {
    expect(deriveTarget({ ...baseInput, capsuleState: "idle" })).toBe("home");
    expect(deriveTarget({ ...baseInput, capsuleState: "analyzing" })).toBe("home");
    expect(
      deriveTarget({ ...baseInput, capsuleState: "running", taskPhase: "scraping" }),
    ).toBe("task-scrape");
    expect(
      deriveTarget({ ...baseInput, capsuleState: "running", taskPhase: "screening" }),
    ).toBe("task-screen");
    expect(deriveTarget({ ...baseInput, capsuleState: "completed" })).toBe("results");
    expect(
      deriveTarget({ ...baseInput, capsuleState: "attention", stuckAt: "scrape" }),
    ).toBe("task-scrape");
    expect(
      deriveTarget({ ...baseInput, capsuleState: "attention", stuckAt: "screen" }),
    ).toBe("task-screen");
  });

  it("等待异步退出历史完成后再按真实落点切页", async () => {
    const events: string[] = [];
    const navigation = useIslandNavigation();

    await navigation.navigate("task-screen", {
      historyMode: true,
      onExitHistory: async () => {
        events.push("exit-start");
        await Promise.resolve();
        events.push("exit-finished");
        // 退出历史返回当前轮真实落点（不由历史轮的胶囊目标决定）。
        return "search";
      },
      onSetActiveStep: (step) => events.push(`step:${step}`),
    });

    expect(events).toEqual(["exit-start", "exit-finished", "step:search"]);
  });

  it("退出历史没完成（返回空）时不得提前设置目标步骤", async () => {
    const events: string[] = [];
    const navigation = useIslandNavigation();

    await navigation.navigate("results", {
      historyMode: true,
      onExitHistory: () => {
        events.push("exit-skipped");
        return null;
      },
      onSetActiveStep: (step) => events.push(`step:${step}`),
    });

    // 退出未完成：既不落历史轮目标（results），也不制造空结果页。
    expect(events).toEqual(["exit-skipped"]);
  });

  it("启动恢复期间只排队一次点击，恢复后再执行", () => {
    const events: string[] = [];
    const navigation = useIslandNavigation();

    navigation.setBootstrapping(true);
    navigation.navigate("results", {
      onExitHistory: () => undefined,
      onSetActiveStep: (step) => events.push(`step:${step}`),
    });
    navigation.navigate("task-scrape", {
      onExitHistory: () => undefined,
      onSetActiveStep: (step) => events.push(`step:${step}`),
    });
    expect(events).toEqual([]);

    navigation.setBootstrapping(false);
    navigation.flushBootstrap({
      onExitHistory: () => undefined,
      onSetActiveStep: (step) => events.push(`step:${step}`),
    });
    expect(events).toEqual(["step:search"]);
  });
});

describe("useIslandNavigation 落点可达性（唯一回落规则）", () => {
  it("步骤不可进时按回落链落回最近的可达页，末位恒为第一页", () => {
    expect(reachableStep("results", new Set(["upload", "search", "screen", "results"]))).toBe("results");
    expect(reachableStep("results", new Set(["upload", "search"]))).toBe("search");
    expect(reachableStep("results", new Set(["upload"]))).toBe("upload");
    expect(reachableStep("screen", new Set(["upload"]))).toBe("upload");
    expect(reachableStep("search", new Set(["upload", "screen"]))).toBe("screen");
    // 拿不到清单时不越权替用户改落点（历史口径：空清单=不拦）。
    expect(reachableStep("results", new Set())).toBe("results");
  });

  it("灵动岛落点派生与回落链同源（同一张表，不各写一套）", () => {
    const completed = { ...baseInput, capsuleState: "completed" as const };
    expect(deriveTarget(completed)).toBe("results");
    expect(deriveTarget({ ...completed, enabledSteps: new Set(["upload", "screen"]) })).toBe("task-screen");
    expect(deriveTarget({ ...completed, enabledSteps: new Set(["upload", "search"]) })).toBe("task-scrape");
    expect(deriveTarget({ ...completed, enabledSteps: new Set(["upload"]) })).toBe("home");
  });
});
