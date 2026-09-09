<script setup lang="ts">
import { LoaderCircle } from "@lucide/vue";
import type { ScreenPrimaryAction, ScrapePrimaryAction } from "../screenFlow";

type SharedPrimaryAction = ScreenPrimaryAction | ScrapePrimaryAction;

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
      :disabled="anyActionBusy()"
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
      :disabled="anyActionBusy()"
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
