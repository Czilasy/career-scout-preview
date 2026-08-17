<script setup lang="ts">
import ScreenRoundActions from "./ScreenRoundActions.vue";
import TaskProgress from "./TaskProgress.vue";
import type { ScreenPrimaryAction } from "../screenFlow";
import type { TaskSnapshot } from "../types";

defineProps<{
  snapshot: TaskSnapshot | null;
  taskId: string;
  action: ScreenPrimaryAction;
  busy: boolean;
  busyAction?: string;
  busyLabel?: string;
  showFinishSave?: boolean;
}>();

const emit = defineEmits<{
  "pause-recrawl": [];
  "continue-recrawl": [];
  "finish-save": [];
}>();
</script>

<template>
  <div class="screen-recrawl-progress" data-testid="screen-recrawl-progress">
    <TaskProgress :snapshot="snapshot" kind="screen" :task-id="taskId" />
    <ScreenRoundActions
      :action="action"
      :busy="busy"
      :busy-action="busyAction"
      :busy-label="busyLabel"
      :finish-busy="busyAction === 'finish'"
      :show-finish-save="showFinishSave"
      @pause-recrawl="emit('pause-recrawl')"
      @continue-recrawl="emit('continue-recrawl')"
      @finish-save="emit('finish-save')"
    />
  </div>
</template>
