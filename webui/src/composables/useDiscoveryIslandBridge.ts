import { watch } from "vue";

import type { IslandNavTarget } from "../types";
import {
  capsuleNavigationTarget,
  deriveLiveTaskStep,
  type CapsuleNavigationTarget,
  type DiscoveryState,
  type StepId,
} from "./useDiscoveryState";
import { reachableStep, useIslandNavigation } from "./useIslandNavigation";

/**
 * Spec041：灵动岛点击 → 步骤页落点的桥接。
 *
 * 消费 App 侧派发的模块级导航信号（`requestCapsuleNavigation`），把旧的两态目标
 * 归一成真实落点，再交给 `useIslandNavigation` 统一处理历史退出与启动占位。
 * 这里不清结果、不重置流程（硬红线：任何点击路径都不许把页面清成空白上传页）。
 *
 * 由视图在 setup 内构造一次；导航信号本身仍由 useDiscoveryState 持有（App 侧
 * 导入面不变）。
 */

function normalizeCapsuleTarget(
  target: CapsuleNavigationTarget,
  probe: { stuckAt: "scrape" | "screen" | "none"; liveStep: StepId | "" },
): IslandNavTarget {
  if (target === "task-scrape" || target === "task-screen" || target === "results"
    || target === "home" || target === "reminders") {
    return target;
  }
  if (target === "attention") {
    if (probe.stuckAt === "screen") return "task-screen";
    if (probe.stuckAt === "scrape") return "task-scrape";
  }
  return probe.liveStep === "screen" ? "task-screen" : "task-scrape";
}

export function useDiscoveryIslandBridge(state: DiscoveryState): void {
  const islandNavigation = useIslandNavigation();

  function liveProbe() {
    return {
      stuckAt: state.roundStatusPayload.value?.stuckAt || "none",
      liveStep: deriveLiveTaskStep({
        scrapeBusy: state.scrapeBusy.value,
        scrapeSnapshot: state.scrapeSnapshot.value,
        screenBusy: state.screenBusy.value,
        screenSnapshot: state.screenSnapshot.value,
        recrawlBusy: state.recrawlBusy.value,
        recrawlSnapshot: state.recrawlSnapshot.value,
        pausedRunId: state.pausedRunId.value,
        interruptedRunId: state.interruptedRunId.value,
      }),
    };
  }

  function navigationOptions() {
    return {
      historyMode: state.historyMode.value,
      // Spec041 返工：退出历史要真正 await 完整的回最新恢复（含平台还原与结果
      // 加载），并把"当前轮真实落点步骤"交给导航层当最终落点。
      onExitHistory: () => (state.capsuleReturnToLatest.value || state.historyBackToLatest)(),
      // Spec041 返工（复审收口）：改步骤的唯一出口。灵动岛各条点击路径
      //（胶囊本体 / 面板通知行「已完成·出错·暂停·打断」/ 回最新落点）最终都
      // 在这里写 activeStep——统一在这里做一次可达性核对：活任务所在的页直接
      // 放行（它来自真实状态，核实了 035「刷新后回真实进度页」的契约）；其余
      // 不可进的页一律落回最近的可达页，既不越级，也不静默吞掉点击。
      onSetActiveStep: (step: string) => {
        const live = liveProbe().liveStep;
        const landing = step === live
          ? step
          : reachableStep(step, new Set(state.enabledSteps.value));
        state.activeStep.value = landing as StepId;
      },
    };
  }

  function applyCapsuleNavigation(target: CapsuleNavigationTarget): void {
    islandNavigation.navigate(normalizeCapsuleTarget(target, liveProbe()), navigationOptions());
  }

  islandNavigation.setBootstrapping(!state.workflowStateRestored.value);
  watch(state.workflowStateRestored, (ready) => {
    islandNavigation.setBootstrapping(!ready);
    if (ready) islandNavigation.flushBootstrap(navigationOptions());
  });

  watch(capsuleNavigationTarget, (target) => {
    if (!target) return;
    capsuleNavigationTarget.value = null;
    applyCapsuleNavigation(target);
  });
}
