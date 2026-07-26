<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { CircleCheck, CircleX, LoaderCircle, Octagon } from "@lucide/vue";

interface TaskSnapshot {
  status?: string;
  progress?: Record<string, unknown>;
  logs?: string[];
  error?: string;
}

const props = defineProps<{
  snapshot: TaskSnapshot | null;
  kind?: "scrape" | "screen" | "";
}>();

// ---- 用时计时 ----
// snapshot 从 null→非 null 时记开始时间；status 进入终态（done/failed/cancelled）时定格。
// 完成后显示绝对用时；运行中每秒刷新显示"已用 X 秒"。
const startedAt = ref<number | null>(null);
const finishedAt = ref<number | null>(null);
const tickTok = ref(0); // 触发运行中秒数刷新
let intervalId: number | undefined;

function resetTimer() {
  startedAt.value = null;
  finishedAt.value = null;
}

watch(
  () => props.snapshot,
  (next, prev) => {
    // 任务出现（null→非null，或 immediate 首触发时已有值）
    if (next && !prev) {
      startedAt.value = Date.now();
      finishedAt.value = null;
    }
    // 任务消失（非null→null）：重置
    if (!next && prev) {
      resetTimer();
      if (intervalId !== undefined) {
        clearInterval(intervalId);
        intervalId = undefined;
      }
      return;
    }
    // 终态：定格用时，停止刷新
    if (next && next.status && ["done", "failed", "cancelled"].includes(next.status)) {
      if (finishedAt.value === null) finishedAt.value = Date.now();
      if (intervalId !== undefined) {
        clearInterval(intervalId);
        intervalId = undefined;
      }
    } else if (next && startedAt.value !== null && finishedAt.value === null && intervalId === undefined) {
      // 运行中：确保 interval 在跑（覆盖 immediate 首触发 / 组件重新挂载的场景）
      intervalId = window.setInterval(() => { tickTok.value++; }, 1000);
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  if (intervalId !== undefined) clearInterval(intervalId);
});

const elapsedMs = computed(() => {
  void tickTok.value; // 每秒递增，强制 computed 重新求值
  if (startedAt.value === null) return 0;
  const end = finishedAt.value ?? Date.now();
  return Math.max(0, end - startedAt.value);
});

function formatDuration(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  if (m === 0) return `${s}秒`;
  return `${m}分${s.toString().padStart(2, "0")}秒`;
}

const elapsedLabel = computed(() => formatDuration(elapsedMs.value));

// 每个阶段映射到 [该阶段起始百分比, 该阶段结束百分比]
// 阶段内的 current/total 会在该区间内线性插值。
const SCRAPE_WEIGHTS: Record<string, [number, number]> = {
  ensure_chrome: [0, 5],
  preflight: [5, 10],
  searching: [10, 90],
  combo_done: [10, 90],
  combo_failed: [10, 90],
  waiting: [10, 90],
  risk_warning: [90, 95],
  closing_chrome: [95, 100],
  done: [100, 100],
};

const SCREEN_WEIGHTS: Record<string, [number, number]> = {
  resume: [0, 0],
  screen_a: [0, 35],
  screen_a_done: [35, 35],
  ensure_chrome: [35, 40],
  fetch_jd: [40, 75],
  screen_b: [75, 100],
  done: [100, 100],
};

const progress = computed(() => props.snapshot?.progress || {});
const current = computed(() => Number(progress.value.current || 0));
const total = computed(() => Number(progress.value.total || 0));
const stage = computed(() => String(progress.value.stage || ""));

const rawPercentage = computed(() => {
  if (props.snapshot?.status === "done") return 100;
  // 后端直接给出整体百分比时优先使用。
  const overall = Number(progress.value.overall_percent);
  if (!Number.isNaN(overall)) {
    return Math.min(100, Math.round(overall));
  }
  // 旧数据兼容：没有 overall_percent 时按阶段权重估算。
  const weights = props.kind === "screen" ? SCREEN_WEIGHTS : SCRAPE_WEIGHTS;
  const range = stage.value ? weights[stage.value] : undefined;
  if (!range) {
    return total.value > 0
      ? Math.min(100, Math.round((current.value * 100) / total.value))
      : 0;
  }
  const [start, end] = range;
  if (total.value <= 0) return start;
  const ratio = Math.min(1, Math.max(0, current.value / total.value));
  return Math.min(100, Math.round(start + (end - start) * ratio));
});

// 平滑动画：把显示值从当前值慢慢追到目标值，并带一点随机小卡顿。
const displayPercent = ref(rawPercentage.value);
let rafId: number | undefined;

function easeStep(delta: number) {
  // 大步时走得快，小步时走得慢；加上一点随机抖动制造“不匀速”感。
  const jitter = (Math.random() - 0.5) * 0.6;
  return Math.max(0.25, Math.abs(delta) * 0.08 + jitter);
}

function tick() {
  const target = rawPercentage.value;
  const delta = target - displayPercent.value;
  if (Math.abs(delta) < 0.25) {
    displayPercent.value = target;
    rafId = undefined;
    return;
  }
  // 小概率随机“卡顿”一帧，模拟真实网络/AI 调用时的停顿。
  if (Math.random() < 0.08) {
    rafId = requestAnimationFrame(tick);
    return;
  }
  displayPercent.value += Math.sign(delta) * easeStep(delta);
  rafId = requestAnimationFrame(tick);
}

watch(rawPercentage, () => {
  if (rafId === undefined) {
    rafId = requestAnimationFrame(tick);
  }
});

onBeforeUnmount(() => {
  if (rafId !== undefined) cancelAnimationFrame(rafId);
});

const percentage = computed(() => Math.round(displayPercent.value));

const message = computed(() => String(progress.value.message || "正在准备任务…"));
const statusLabel = computed(() => {
  if (props.snapshot?.status === "done") return "已完成";
  if (props.snapshot?.status === "failed") return "执行失败";
  if (props.snapshot?.status === "cancelled") return "已停止";
  return "运行中";
});

// 终态显示绝对用时；运行中显示"已用 X 秒"
const timeLabel = computed(() => {
  if (startedAt.value === null) return "";
  const terminal = props.snapshot?.status && ["done", "failed", "cancelled"].includes(props.snapshot.status);
  return terminal ? `用时 ${elapsedLabel.value}` : `已用 ${elapsedLabel.value}`;
});
</script>

<template>
  <section v-if="snapshot" class="task-progress" aria-live="polite">
    <header>
      <span class="task-status" :data-status="snapshot.status || 'running'">
        <CircleCheck v-if="snapshot.status === 'done'" :size="17" aria-hidden="true" />
        <CircleX v-else-if="snapshot.status === 'failed'" :size="17" aria-hidden="true" />
        <Octagon v-else-if="snapshot.status === 'cancelled'" :size="17" aria-hidden="true" />
        <LoaderCircle v-else class="spin" :size="17" aria-hidden="true" />
        {{ statusLabel }}
      </span>
      <span v-if="timeLabel" class="task-elapsed">· {{ timeLabel }}</span>
      <span class="task-percentage">{{ percentage }}%</span>
    </header>
    <div class="progress-track" aria-hidden="true">
      <span :style="{ width: `${percentage}%` }" />
    </div>
    <p class="task-message">{{ snapshot.error || message }}</p>
  </section>
</template>
