import { computed, type Ref } from "vue";

import type { SceneIdentity } from "../types";
import { useAutoGrowTextarea } from "./useAutoGrowTextarea";
import type { DiscoveryState } from "./useDiscoveryState";
import { useDiscoverySceneState } from "./useDiscoverySceneState";

/**
 * Spec041 返工：画像文字框高度现场（页面二「已撑开的高度」）。
 *
 * 现场存在 `PageScene.profileInputHeight`（连同计算时的窗口宽度与内容），
 * 由自动高度承担计算与恢复：同轮同宽同内容接回存档，其余情况重算并回写；
 * 身份键（切画像 / 开新轮 / 切平台）变了就落到新身份的空现场，不沿用旧高度。
 */
export function useProfileInputScene(state: DiscoveryState, identity: Ref<SceneIdentity>): void {
  const sceneStore = useDiscoverySceneState();
  useAutoGrowTextarea(
    state.profileInputEl,
    state.profileSummary,
    96,
    {
      visible: computed(() => state.activeStep.value === "search"),
      readScene: () => {
        const scene = sceneStore.getCurrent(identity.value);
        return {
          height: scene.profileInputHeight,
          width: scene.profileInputWidth,
          content: scene.profileInputContent,
        };
      },
      writeScene: (height, width, content) => {
        sceneStore.saveCurrent(identity.value, {
          profileInputHeight: height,
          profileInputWidth: width,
          profileInputContent: content,
        });
      },
    },
  );
}
