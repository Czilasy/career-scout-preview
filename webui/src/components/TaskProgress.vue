<script setup lang="ts">
import { computed } from "vue";
import { CircleCheck, CircleX, LoaderCircle } from "@lucide/vue";

interface TaskSnapshot {
  status?: string;
  progress?: Record<string, unknown>;
  logs?: string[];
  error?: string;
}

const props = defineProps<{ snapshot: TaskSnapshot | null }>();

const progress = computed(() => props.snapshot?.progress || {});
const current = computed(() => Number(progress.value.current || 0));
const total = computed(() => Number(progress.value.total || 0));
const percentage = computed(() => total.value > 0
  ? Math.min(100, Math.round(current.value * 100 / total.value))
  : 0);
const message = computed(() => String(progress.value.message || "正在准备任务…"));
const statusLabel = computed(() => {
  if (props.snapshot?.status === "done") return "已完成";
  if (props.snapshot?.status === "failed") return "执行失败";
  return "运行中";
});
</script>

<template>
  <section v-if="snapshot" class="task-progress" aria-live="polite">
    <header>
      <span class="task-status" :data-status="snapshot.status || 'running'">
        <CircleCheck v-if="snapshot.status === 'done'" :size="17" aria-hidden="true" />
        <CircleX v-else-if="snapshot.status === 'failed'" :size="17" aria-hidden="true" />
        <LoaderCircle v-else class="spin" :size="17" aria-hidden="true" />
        {{ statusLabel }}
      </span>
      <span v-if="total">{{ current }} / {{ total }}</span>
    </header>
    <div v-if="total" class="progress-track" aria-hidden="true">
      <span :style="{ width: `${percentage}%` }" />
    </div>
    <p>{{ snapshot.error || message }}</p>
    <ol v-if="snapshot.logs?.length" class="task-log">
      <li v-for="(log, index) in snapshot.logs.slice(-6)" :key="`${index}-${log}`">{{ log }}</li>
    </ol>
  </section>
</template>
