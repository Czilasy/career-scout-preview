import type { IslandNavTarget } from "../types";

export type CapsuleState = "idle" | "analyzing" | "running" | "completed" | "attention";
export type TaskPhase = "scraping" | "jd" | "screening" | "scraped" | "completed" | "none";
export type StuckAt = "scrape" | "screen" | "none";

export interface NavInput {
  capsuleState: CapsuleState;
  taskPhase: TaskPhase;
  stuckAt: StuckAt;
  enabledSteps: Set<string>;
  historyMode: boolean;
  bootstrapping: boolean;
}

export interface NavigationOptions {
  historyMode?: boolean;
  /**
   * 退出历史并返回"当前轮真实落点步骤"。返回空表示本次没有完成退出
   * （例如并发点击/用户又点了别的轮次），此时不得再改步骤。
   */
  onExitHistory: () => string | null | void | Promise<string | null | void>;
  onSetActiveStep: (step: string) => void;
}

function hasStep(enabledSteps: Set<string>, step: string): boolean {
  return enabledSteps.size === 0 || enabledSteps.has(step);
}

/**
 * 落点回落链（唯一一张表）：某一步不可进时按这条链落回"最近的可达页"，
 * 末位恒为第一页。步骤裁决（改步骤出口的守卫）与灵动岛落点派生共用它，
 * 不再"两处各有一套回落规则、守卫只挂在其中一处"。
 */
const STEP_FALLBACK_CHAIN: Record<string, readonly string[]> = {
  results: ["results", "screen", "search", "upload"],
  screen: ["screen", "search", "upload"],
  search: ["search", "screen", "upload"],
  upload: ["upload"],
};

/** 步骤裁决唯一实现：不可进的步骤一律落回最近的可达页（永远给得出落点）。 */
export function reachableStep(step: string, enabledSteps: Set<string>): string {
  for (const candidate of STEP_FALLBACK_CHAIN[step] ?? ["upload"]) {
    if (hasStep(enabledSteps, candidate)) return candidate;
  }
  return "upload";
}

function targetForStep(step: string): IslandNavTarget | null {
  if (step === "upload") return "home";
  if (step === "search") return "task-scrape";
  if (step === "screen") return "task-screen";
  if (step === "results") return "results";
  return null;
}

function fallBackToEnabled(target: IslandNavTarget, enabledSteps: Set<string>): IslandNavTarget {
  const step = stepForTarget(target);
  if (!step) return target;
  return targetForStep(reachableStep(step, enabledSteps)) ?? "home";
}

export function deriveTarget(input: NavInput): IslandNavTarget {
  let target: IslandNavTarget;
  if (input.capsuleState === "idle" || input.capsuleState === "analyzing") {
    target = "home";
  } else if (input.capsuleState === "completed") {
    target = "results";
  } else if (input.capsuleState === "attention") {
    target = input.stuckAt === "scrape" ? "task-scrape" : "task-screen";
  } else if (input.taskPhase === "scraping") {
    target = "task-scrape";
  } else if (
    input.taskPhase === "jd" ||
    input.taskPhase === "screening" ||
    input.taskPhase === "scraped"
  ) {
    target = "task-screen";
  } else {
    target = "task-scrape";
  }
  return fallBackToEnabled(target, input.enabledSteps);
}

function stepForTarget(target: IslandNavTarget): string | null {
  if (target === "home") return "upload";
  if (target === "task-scrape") return "search";
  if (target === "task-screen") return "screen";
  if (target === "results") return "results";
  return null;
}

export function useIslandNavigation() {
  let bootstrapping = false;
  let pendingTarget: IslandNavTarget | null = null;

  function setBootstrapping(value: boolean): void {
    bootstrapping = value;
  }

  function handleBootstrapClick(target: IslandNavTarget | null): void {
    if (!target) {
      pendingTarget = null;
      return;
    }
    if (bootstrapping) pendingTarget = target;
  }

  async function execute(target: IslandNavTarget, options: NavigationOptions): Promise<void> {
    if (options.historyMode) {
      // Spec041 返工（真实验收失败项二）：正在浏览历史时，落点必须来自
      // 「退出历史后的当前轮真实状态」（任务真实进度 / 当前结果 / 干净 01），
      // 不能用历史轮胶囊派生的目标；退出没完成时也不能改步骤，否则会落到
      // 空结果页或错的平台。
      const landed = await options.onExitHistory();
      if (typeof landed === "string" && landed) options.onSetActiveStep(landed);
      return;
    }
    const step = stepForTarget(target);
    if (step) options.onSetActiveStep(step);
  }

  async function navigate(target: IslandNavTarget, options: NavigationOptions): Promise<void> {
    if (bootstrapping) {
      handleBootstrapClick(target);
      return;
    }
    await execute(target, options);
  }

  async function flushBootstrap(options: NavigationOptions): Promise<void> {
    if (bootstrapping || !pendingTarget) return;
    const target = pendingTarget;
    pendingTarget = null;
    await execute(target, options);
  }

  return {
    deriveTarget,
    navigate,
    handleBootstrapClick,
    setBootstrapping,
    flushBootstrap,
  };
}
