<script setup lang="ts">
import { LoaderCircle } from "@lucide/vue";
import type { SharedPrimaryAction } from "../screenFlow";

const props = defineProps<{
  action: SharedPrimaryAction;
  busy?: boolean;
  busyAction?: string;
  busyLabel?: string;
  finishBusy?: boolean;
  showFinishSave?: boolean;
  showCancel?: boolean;
  cancelBusy?: boolean;
  cancelLabel?: string;
  finishTestId?: string;
  cancelTestId?: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  pause: [];
  continue: [];
  start: [];
  recrawl: [];
  "pause-recrawl": [];
  "continue-recrawl": [];
  "pause-scrape": [];
  "continue-scrape": [];
  "finish-save": [];
  cancel: [];
}>();

const ACTION_TEST_IDS: Record<string, string> = {
  pause: "pause-ai-screen",
  continue: "continue-ai-screen",
  start: "start-ai-screen",
  recrawl: "recrawl-uncertain-flow",
  "pause-recrawl": "pause-recrawl",
  "continue-recrawl": "continue-recrawl",
  "pause-scrape": "pause-scrape",
  "continue-scrape": "continue-scrape",
};

function actionTestId(): string {
  return ACTION_TEST_IDS[props.action.kind] || "";
}

function emitAction() {
  const kind = props.action.kind;
  if (kind === "pause") emit("pause");
  else if (kind === "continue") emit("continue");
  else if (kind === "start") emit("start");
  else if (kind === "recrawl") emit("recrawl");
  else if (kind === "pause-recrawl") emit("pause-recrawl");
  else if (kind === "continue-recrawl") emit("continue-recrawl");
  else if (kind === "pause-scrape") emit("pause-scrape");
  else if (kind === "continue-scrape") emit("continue-scrape");
}

function finishTestId(): string {
  return props.finishTestId || "finish-save-results";
}

function cancelTestId(): string {
  if (props.cancelTestId) return props.cancelTestId;
  return props.action.kind === "continue-scrape" ? "cancel-paused-scrape" : "cancel-scrape";
}

function primaryBusy(): boolean {
  return Boolean(props.busy && (!props.busyAction || props.busyAction === props.action.kind));
}

function finishBusyState(): boolean {
  return Boolean(props.finishBusy || (props.busy && props.busyAction === "finish"));
}

function cancelBusyState(): boolean {
  return Boolean(props.cancelBusy || (props.busy && props.busyAction === "cancel"));
}

function anyActionBusy(): boolean {
  return Boolean(props.busy || props.finishBusy || props.cancelBusy);
}

function cancelDisabled(): boolean {
  // 暂停等待期间（暂停按钮已变灰、任务仍在收尾）必须保留「放弃本轮/终止」
  // 这条出路，否则任务长时间不收尾时整个操作区被锁死、用户只能刷新页面。
  if (props.busy && props.busyAction === "pause-scrape") {
    return Boolean(props.cancelBusy || props.finishBusy);
  }
  return anyActionBusy();
}

// disabled 说的是「这一整片现场此刻不可操作」（例如流程状态读不到、只能看），
// 因此三个按钮一起锁；只锁主动作会让收尾与终止在不可确认的状态下仍然可点。
function sharedDisabled(): boolean {
  return anyActionBusy() || Boolean(props.disabled);
}
</script>

<template>
  <div class="screen-round-actions">
    <button
      v-if="action.kind !== 'none'"
      class="button primary"
      type="button"
      :data-testid="actionTestId()"
      :disabled="anyActionBusy() || disabled"
      @click="emitAction()"
    >
      <LoaderCircle
        v-if="primaryBusy()"
        class="spin"
        :size="15"
        aria-hidden="true"
      />
      {{ primaryBusy() ? busyLabel || action.label : action.label }}
    </button>
    <button
      v-if="showFinishSave"
      class="button danger"
      type="button"
      :data-testid="finishTestId()"
      :disabled="sharedDisabled()"
      @click="emit('finish-save')"
    >
      <LoaderCircle
        v-if="finishBusyState()"
        class="spin"
        :size="15"
        aria-hidden="true"
      />
      {{ finishBusyState() ? '正在保存…' : '结束并保存结果' }}
    </button>
    <button
      v-if="showCancel"
      class="button danger"
      type="button"
      :data-testid="cancelTestId()"
      :disabled="cancelDisabled() || Boolean(disabled)"
      @click="emit('cancel')"
    >
      <LoaderCircle
        v-if="cancelBusyState()"
        class="spin"
        :size="15"
        aria-hidden="true"
      />
      {{ cancelBusyState() ? '终止中…' : cancelLabel || '终止' }}
    </button>
  </div>
</template>

<style scoped>
.screen-round-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
</style>
