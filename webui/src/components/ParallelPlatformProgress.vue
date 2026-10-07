<script setup lang="ts">
import ScreenRoundActions from "./ScreenRoundActions.vue";
import TaskProgress from "./TaskProgress.vue";
import type { FlowProgressItem } from "../composables/useDiscoveryFlowPresentation";
import {
  TRACK_ACTION_OPERATIONS,
  type TrackActionKind,
} from "../composables/useDiscoveryParallelFlow";
import type { Platform } from "../types";

// 轨道行 = 一平台一行的复用现场：进度卡与动作条都是既有组件，动作条就放进这张卡里。
// 这一层只做两件事——把呈现层按轨道各算好的事实摆出来，把点击原样往上传；
// 状态口径、平台名、阶段名、动作与显隐判定都不在这里（判定在 useDiscoveryFlowPresentation + screenFlow）。
const props = defineProps<{
  items: FlowProgressItem[];
  busyPlatform?: Platform | null;
  stale?: boolean;
  finishBusy?: boolean;
  busyActions?: Partial<Record<Platform, string>>;
}>();

const emit = defineEmits(["action", "finish"]);

// 上传的是「哪条线的哪一个动作」，不解释这个动作该不该出现。
// 可绑的 kind 只有轨道落点表那一份：表里有了结方式的才绑，表外的（重抓、开始 AI 筛选）
// 这条线根本做不到，绑了就是一颗点了没反应的假按钮（046 D-03）。
const ACTION_KINDS = Object.keys(TRACK_ACTION_OPERATIONS) as TrackActionKind[];

function actionHandlers(item: FlowProgressItem): Record<string, () => void> {
  const handlers: Record<string, () => void> = {};
  for (const kind of ACTION_KINDS) {
    // 047 C2：失败重试与暂停/继续/终止一样走既有 action 通道，
    // 身份（含 updated_at）由下游按当前轨道解析，平台壳只转发。
    handlers[kind] = () => emit("action", item.platform, kind, item.runId);
  }
  return handlers;
}
</script>

<template>
  <section
    class="parallel-platform-progress"
    data-testid="parallel-platform-progress"
    aria-label="平台并行进度"
  >
    <article
      v-for="item in props.items"
      :key="`${item.platform}:${item.trackId}`"
      class="parallel-platform-item"
      :data-testid="`parallel-track-${item.platform}`"
    >
      <TaskProgress
        :snapshot="item.snapshot"
        :kind="item.kind"
        :task-id="item.runId || undefined"
        :platform="item.platform"
        :round-closed="item.roundClosed"
        :closure="item.snapshot?.closure ?? null"
      >
        <!-- 动作条是这张卡的一部分：交给卡的落点，不再另起一行掉在卡外。 -->
        <ScreenRoundActions
          v-on="actionHandlers(item)"
          :action="item.action"
          :busy="props.busyActions ? Boolean(props.busyActions[item.platform]) : props.busyPlatform === item.platform"
          :finish-busy="props.busyActions ? props.busyActions[item.platform] === 'finish' : Boolean(props.finishBusy)"
          :cancel-busy="props.busyActions?.[item.platform] === 'cancel'"
          :show-finish-save="item.showFinishSave"
          :show-cancel="item.showCancel"
          :cancel-label="item.cancelLabel"
          :finish-test-id="item.finishTestId"
          :cancel-test-id="item.cancelTestId"
          :disabled="props.stale === true"
          @finish-save="emit('finish', item.finishRunId, item.platform, item.kind)"
        />
      </TaskProgress>
    </article>
  </section>
</template>

<style scoped>
.parallel-platform-progress {
  display: grid;
  gap: 14px;
}
.parallel-platform-item {
  min-width: 0;
}
</style>
