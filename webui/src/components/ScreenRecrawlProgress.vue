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
  busyLabel?: string;
  showViewResults?: boolean;
  showFinishSave?: boolean;
}>();

const emit = defineEmits<{
  "pause-recrawl": [];
  "continue-recrawl": [];
  "finish-save": [];
  "view-results": [];
}>();
</script>

<template>
  <div class="screen-recrawl-progress" data-testid="screen-recrawl-progress">
    <TaskProgress :snapshot="snapshot" kind="screen" :task-id="taskId" />
    <ScreenRoundActions
      :action="action"
      :busy="busy"
      :busy-label="busyLabel"
      :show-view-results="showViewResults"
      :show-finish-save="showFinishSave"
      @pause-recrawl="emit('pause-recrawl')"
      @continue-recrawl="emit('continue-recrawl')"
      @finish-save="emit('finish-save')"
      @view-results="emit('view-results')"
    />
  </div>
</template>
