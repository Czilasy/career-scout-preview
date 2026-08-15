<script setup lang="ts">
import { LoaderCircle } from "@lucide/vue";
import type { ScreenPrimaryAction } from "../screenFlow";

const props = defineProps<{
  action: ScreenPrimaryAction;
  busy?: boolean;
  busyLabel?: string;
  showViewResults?: boolean;
  showFinishSave?: boolean;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  pause: [];
  continue: [];
  start: [];
  recrawl: [];
  "pause-recrawl": [];
  "continue-recrawl": [];
  "finish-save": [];
  "view-results": [];
}>();

const ACTION_TEST_IDS: Record<string, string> = {
  pause: "pause-ai-screen",
  continue: "continue-ai-screen",
  start: "start-ai-screen",
  recrawl: "recrawl-uncertain-flow",
  "pause-recrawl": "pause-recrawl",
  "continue-recrawl": "continue-recrawl",
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
}
</script>

<template>
  <div class="screen-round-actions">
    <button
      v-if="action.kind !== 'none'"
      class="button primary"
      type="button"
      :data-testid="actionTestId()"
      :disabled="busy || disabled"
      @click="emitAction()"
    >
      <LoaderCircle v-if="busy" class="spin" :size="15" aria-hidden="true" />
      {{ busy ? busyLabel || action.label : action.label }}
    </button>
    <button
      v-if="showViewResults"
      class="button secondary"
      type="button"
      data-testid="view-screen-results"
      :disabled="busy"
      @click="emit('view-results')"
    >
      查看结果
    </button>
    <button
      v-if="showFinishSave"
      class="button danger"
      type="button"
      data-testid="finish-save-results"
      :disabled="busy"
      @click="emit('finish-save')"
    >
      结束并保存结果
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
