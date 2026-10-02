import { reactive, ref, type Ref } from "vue";
import { errorMessage } from "../api";
import type { Platform } from "../types";
import type { useDiscoveryExecution } from "./useDiscoveryExecution";
import type { FlowProgressItem } from "./useDiscoveryFlowPresentation";
import { TRACK_ACTION_OPERATIONS, type TrackActionKind, type useDiscoveryParallelFlow } from "./useDiscoveryParallelFlow";
import type { useScreenRoundFlow } from "./useScreenRoundFlow";

interface InstanceActionDeps {
  parallel: ReturnType<typeof useDiscoveryParallelFlow>;
  round: Pick<ReturnType<typeof useScreenRoundFlow>, "requestInstanceAction">;
  finish: ReturnType<typeof useDiscoveryExecution>["finishPausedTask"];
  items: () => FlowProgressItem[];
  refreshResults: (options: { preservePresentation: boolean }) => Promise<unknown>;
  notify: (message: string, tone?: "error" | "warning") => void;
}

/** 只绑定实例身份、忙态和合流；批次选择、保存和生命周期仍由共享入口执行。 */
export function useDiscoveryInstanceActions(deps: InstanceActionDeps) {
  const busy = reactive<Partial<Record<Platform, string>>>({});
  const finishBusy: Partial<Record<Platform, Ref<boolean>>> = {};

  function target(platform: Platform, runId: string, kind?: "scrape" | "screen") {
    const flowId = deps.parallel.flow.value?.id;
    const item = deps.items().find((entry) => entry.platform === platform
      && (!kind || entry.kind === kind) && (entry.runId === runId || entry.finishRunId === runId));
    const trackId = item?.trackId;
    const isCurrent = () => {
      const flow = deps.parallel.flow.value;
      const track = flow?.tracks.find((entry) => entry.platform === platform);
      return Boolean(flowId && item && !deps.parallel.stale.value
        && flow?.id === flowId && track?.id === trackId
        && String(track?.screen_run_id || track?.scrape_run_id || "") === runId);
    };
    return { item, isCurrent, snapshot: () => deps.items().find((entry) => entry.platform === platform
      && entry.trackId === trackId && entry.kind === item?.kind)?.snapshot };
  }

  async function operate(platform: Platform, action: TrackActionKind, runId: string) {
    const context = target(platform, runId);
    if (busy[platform] || !context.isCurrent()) return;
    const operation = TRACK_ACTION_OPERATIONS[action];
    const pause = operation === "pause";
    const execute = async (mode: "immediate" | "graceful") => {
      if (!context.isCurrent() || busy[platform]) return;
      busy[platform] = action;
      try {
        await deps.parallel.operate(platform, operation, { runId, mode });
        await deps.parallel.refresh();
      } catch {
        // operate 已给一次错误提示并刷新权威 Flow；不再重复提示。
      } finally { delete busy[platform]; }
    };
    if (pause) await deps.round.requestInstanceAction("pause", { runId, ...context, execute });
    else await execute("graceful");
  }

  async function finish(runId: string, platform: Platform, kind: "scrape" | "screen") {
    const context = target(platform, runId, kind);
    if (busy[platform] || !context.item?.showFinishSave || !context.isCurrent()) return;
    const instanceBusy = finishBusy[platform] ||= ref(false);
    const execute = async (mode: "immediate" | "graceful") => {
      if (busy[platform] || !context.isCurrent()) return;
      busy[platform] = "finish";
      const flowId = deps.parallel.flow.value?.id;
      try {
        await deps.finish(runId, { waitForBatch: mode === "graceful", instance: {
          busy: instanceBusy,
          onSaved: async () => {
            if (deps.parallel.flow.value?.id !== flowId) return;
            await deps.parallel.refresh();
            if (deps.parallel.flow.value?.id !== flowId) return;
            // 同一 Flow 的完整结果原地合入，单轨响应永不写入共同结果。
            await deps.refreshResults({ preservePresentation: true });
          },
        } });
      } catch (error) {
        deps.notify(errorMessage(error, "结束保存后的流程状态读取失败，请刷新确认"), "error");
      } finally { delete busy[platform]; }
    };
    await deps.round.requestInstanceAction("finish", { runId, ...context, execute });
  }

  return { busy, operate, finish };
}
