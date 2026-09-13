import { computed, watch, type ComputedRef } from "vue";

import type { SceneIdentity } from "../types";
import type { DiscoveryProps } from "./discoveryDeps";
import type { DiscoveryState } from "./useDiscoveryState";
import { useDiscoverySceneState } from "./useDiscoverySceneState";

/**
 * Spec041 返工：步骤页现场身份（画像 + 稳定轮次 + 平台）。
 *
 * 轮次身份由 `useDiscoverySceneState` 生成并随会话存档持久化：同一轮从上传、抓取、
 * 筛选到结果完成期间它不随 task_id / 结果 run id 变化（旧实现拿任务 id 当身份，
 * 阶段一推进就换键，组件把"任务推进"误判成"换了新现场"并恢复成默认空现场）。
 * 只有切画像、开新一轮、切平台才会换身份。
 *
 * 同时把本轮已知的结果 run id 记进现场存档，供"开新一轮时把旧轮现场归档成
 * 该轮历史查看现场"使用（历史轮浏览按结果 run id 取现场）。
 */
export function useDiscoverySceneIdentity(
  state: DiscoveryState,
  props: DiscoveryProps,
): { identity: ComputedRef<SceneIdentity> } {
  const sceneStore = useDiscoverySceneState();

  watch(
    () => props.profileId,
    (profileId) => { sceneStore.ensureRoundEpoch(profileId); },
    { immediate: true },
  );

  watch(state.pipelineResultRunId, (runId) => {
    if (!runId) return;
    // 浏览历史时 pipelineResultRunId 存的是历史轮的 run id，不能登记成本轮结果 id。
    if (state.historyMode.value) return;
    sceneStore.noteRoundRunId(props.profileId, sceneStore.roundEpoch.value, runId);
  }, { immediate: true });

  const identity = computed<SceneIdentity>(() => ({
    profileId: props.profileId,
    // 轮次身份跨阶段稳定；空画像（未就绪）退化为固定字面量，不参与持久化。
    runEpoch: sceneStore.roundEpoch.value || "draft",
    platform: state.historyRound.value?.platform
      || state.platformState.result
      || state.screenSnapshot.value?.platform
      || state.scrapeSnapshot.value?.platform
      || state.draftPlatform.value,
  }));

  return { identity };
}
