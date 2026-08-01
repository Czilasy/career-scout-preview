<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { CircleCheck, CircleX, LoaderCircle, Octagon, PauseCircle } from "@lucide/vue";

interface PauseInfo {
  error_code?: string;
  error_reason?: string;
}

interface TaskSnapshot {
  status?: string;
  progress?: Record<string, unknown>;
  logs?: string[];
  error?: string;
  // 后端记录的真实起止时间戳（epoch 毫秒）；缺省时前端退化成本地时钟
  started_at?: number;
  finished_at?: number;
  // 切片7：统一状态接口字段（FR-037/SC-006）
  stage?: string;
  success_count?: number;
  fail_count?: number;
  unstarted_count?: number;
  total?: number;
  kept_count?: number;
  dropped_count?: number;
  pause_info?: PauseInfo | null;
  pending_count?: number;
  source_total?: number;
  execution_config?: Record<string, unknown> | null;
}

const props = defineProps<{
  snapshot: TaskSnapshot | null;
  kind?: "scrape" | "screen" | "";
}>();

const COMPLETED_STATUSES = new Set(["done", "completed", "completed_with_pending"]);
const TERMINAL_STATUSES = new Set([
  ...COMPLETED_STATUSES,
  "failed",
  "cancelled",
  "paused",
]);

const BLOCK_CODES = new Set([
  "captcha_required", "login_expired", "ai_rate_limited",
  "ai_quota_exhausted", "ai_key_invalid", "ai_network_error",
  "ip_risk_control", "cdp_unavailable", "internal_error",
  "source_verification_required", "source_login_required",
  "source_rate_limited", "source_blocked", "source_cdp_unavailable",
]);

const blocked = computed(() => {
  const status = props.snapshot?.status;
  if (status === "failed") return true;
  if (status !== "paused") return false;
  const code = props.snapshot?.pause_info?.error_code || "";
  return BLOCK_CODES.has(code);
});

function isCompletedStatus(status?: string) {
  return Boolean(status && COMPLETED_STATUSES.has(status));
}

function isTerminalStatus(status?: string) {
  return Boolean(status && TERMINAL_STATUSES.has(status));
}

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
    const nextStarted = typeof next?.started_at === "number" ? next.started_at : null;
    const prevStarted = typeof prev?.started_at === "number" ? prev.started_at : null;
    // 新任务：首次出现、后端时间戳变化，或终态后重新开始运行时，重置计时
    const isNewRun = Boolean(next && (
      !prev
      || (nextStarted !== null && nextStarted !== prevStarted)
      || (prev && isTerminalStatus(prev.status) && !isTerminalStatus(next.status))
    ));
    if (next && isNewRun) {
      // 优先用后端真实时间戳；老后端没带则退化成本地时钟（组件重建后不再归零）
      startedAt.value = nextStarted ?? Date.now();
      finishedAt.value = typeof next.finished_at === "number" ? next.finished_at : null;
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
    if (next && isTerminalStatus(next.status)) {
      if (finishedAt.value === null) {
        // 没有真实结束时间的历史数据不伪造，避免出现“用时 0秒”
        finishedAt.value = typeof next.finished_at === "number" ? next.finished_at : null;
      }
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
  if (isCompletedStatus(props.snapshot?.status)) return 100;
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

watch(rawPercentage, (target) => {
  if (props.snapshot?.status === "paused") {
    if (rafId !== undefined) cancelAnimationFrame(rafId);
    rafId = undefined;
    displayPercent.value = target;
    return;
  }
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
  if (props.snapshot?.status === "completed_with_pending") return "完成，但有待确认";
  if (isCompletedStatus(props.snapshot?.status)) return "已完成";
  if (props.snapshot?.status === "failed") return "执行失败";
  if (props.snapshot?.status === "cancelled") return "已停止";
  if (props.snapshot?.status === "paused") return "已暂停";
  return "运行中";
});

// 切片7：阶段中文标签（FR-037/SC-006）
const STAGE_LABELS: Record<string, string> = {
  scrape: "列表抓取",
  ensure_chrome: "启动浏览器",
  preflight: "登录检查",
  searching: "列表抓取",
  jd_detail: "JD 详情抓取",
  fetch_jd: "JD 详情抓取",
  ai_rough: "AI 粗筛",
  screen_a: "AI 粗筛",
  ai_fine: "AI 精筛",
  screen_b: "AI 精筛",
  done: "已完成",
};

const stageLabel = computed(() => {
  const stage = props.snapshot?.stage || String(progress.value.stage || "");
  if (!stage) return "";
  return STAGE_LABELS[stage] || stage;
});

// 切片7：具体暂停原因（SC-006）。优先 pause_info.error_reason，其次 error 字段
const pauseReason = computed(() => {
  if (props.snapshot?.status !== "paused") return "";
  const pi = props.snapshot?.pause_info;
  if (pi?.error_reason) return pi.error_reason;
  return props.snapshot?.error || "任务已暂停，请处理后点继续";
});

// 切片7：完整计数画面（FR-037）。total>0 时才显示
const showCounts = computed(() => {
  const t = Number(props.snapshot?.total || 0);
  return t > 0 && !["done", "completed"].includes(props.snapshot?.status || "");
});
const successCount = computed(() => Number(props.snapshot?.success_count || 0));
const failCount = computed(() => Number(props.snapshot?.fail_count || 0));
const unstartedCount = computed(() => Number(props.snapshot?.unstarted_count || 0));
const totalCount = computed(() => Number(props.snapshot?.total || 0));
const sourceTotal = computed(() => Number(props.snapshot?.source_total || 0));
const pendingCount = computed(() => Number(props.snapshot?.pending_count || 0));
const failLabel = computed(() => pendingCount.value > 0 ? "待确认" : "失败");
const showSourceCounts = computed(() => sourceTotal.value > 0 && sourceTotal.value !== totalCount.value);


// 终态显示绝对用时；运行中显示"已用 X 秒"
const timeLabel = computed(() => {
  if (startedAt.value === null) return "";
  const terminal = isTerminalStatus(props.snapshot?.status);
  if (terminal && finishedAt.value === null) return "";
  return terminal ? `用时 ${elapsedLabel.value}` : `已用 ${elapsedLabel.value}`;
});
</script>

<template>
  <section v-if="snapshot" class="task-progress" aria-live="polite" :data-blocked="blocked || undefined">
    <header>
      <span class="task-status" :data-status="snapshot.status || 'running'">
        <CircleCheck v-if="isCompletedStatus(snapshot.status)" :size="17" aria-hidden="true" />
        <CircleX v-else-if="snapshot.status === 'failed'" :size="17" aria-hidden="true" />
        <PauseCircle v-else-if="snapshot.status === 'paused'" :size="17" aria-hidden="true" />
        <Octagon v-else-if="snapshot.status === 'cancelled'" :size="17" aria-hidden="true" />
        <LoaderCircle v-else class="spin" :size="17" aria-hidden="true" />
        {{ statusLabel }}
      </span>
      <span v-if="stageLabel" class="task-stage">· {{ stageLabel }}</span>
      <span v-if="timeLabel" class="task-elapsed">· {{ timeLabel }}</span>
      <span class="task-percentage">{{ percentage }}%</span>
    </header>
    <div class="progress-track" aria-hidden="true">
      <span :style="{ width: `${percentage}%` }" />
    </div>
    <!-- 切片7：暂停时显示具体原因（SC-006）；其他状态显示 message/error -->
    <p v-if="snapshot.status === 'paused'" class="task-message task-pause-reason" data-testid="pause-reason">
      <PauseCircle :size="14" aria-hidden="true" />{{ pauseReason }}
    </p>
    <p v-else class="task-message">{{ snapshot.error || message }}</p>
    <!-- 切片7：完整计数画面（FR-037） -->
    <div v-if="showCounts" class="task-counts" data-testid="task-counts">
      <span class="count-chip success">成功 {{ successCount }}</span>
      <span class="count-chip fail">{{ failLabel }} {{ failCount }}</span>
      <template v-if="showSourceCounts">
        <span class="count-chip kept">保留 {{ Number(snapshot.kept_count || 0) }}</span>
        <span class="count-chip dropped">淘汰 {{ Number(snapshot.dropped_count || 0) }}</span>
        <span class="count-chip source">列表共 {{ sourceTotal }}</span>
      </template>
      <span class="count-chip unstarted">未开始 {{ unstartedCount }}</span>
      <span class="count-chip total">共 {{ totalCount }}</span>
    </div>
  </section>
</template>
