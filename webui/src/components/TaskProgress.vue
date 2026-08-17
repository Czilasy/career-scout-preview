<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { CircleCheck, CircleX, LoaderCircle, Octagon, PauseCircle } from "@lucide/vue";
import type { Platform } from "../types";

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
  scraped_count?: number;
  execution_config?: Record<string, unknown> | null;
  // T510：任务自身平台，用于在 header 展示真实平台徽章（http-api.md L201）。
  // 由父组件从 /api/latest-running-task 或 /api/task-state 透传；草稿平台切换不影响此处。
  platform?: Platform;
  // 后端从 task_logs 的 pause/resume 事件推导的累计实际运行时长（排除暂停），
  // 单位毫秒；运行中包含当前段，暂停/终态为定格累计。
  active_elapsed_ms?: number;
}

const props = defineProps<{
  snapshot: TaskSnapshot | null;
  kind?: "scrape" | "screen" | "";
  taskId?: string;
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
// 后端 active_elapsed_ms 提供"排除暂停的累计实际运行时长"时优先使用：
// 运行中显示"累计 + 当前段"（active_elapsed_ms 已含当前段，随轮询刷新），
// 暂停/终态显示定格累计；刷新页面后仍由后端事件推导，暂停时长不回流。
const startedAt = ref<number | null>(null);
const finishedAt = ref<number | null>(null);
const activeElapsedMs = ref<number | null>(null);
const activeElapsedAt = ref<number>(0);
const tickTok = ref(0); // 触发运行中秒数刷新
let intervalId: number | undefined;

// 进度条显示值；只向后端真实锚点平滑追赶，任何时刻不超前。
const displayPercent = ref(0);

function resetTimer() {
  startedAt.value = null;
  finishedAt.value = null;
  activeElapsedMs.value = null;
  activeElapsedAt.value = 0;
}

watch(
  () => props.snapshot,
  (next, prev) => {
    const nextStarted = typeof next?.started_at === "number" ? next.started_at : null;
    const prevStarted = typeof prev?.started_at === "number" ? prev.started_at : null;
    // 新任务：首次出现或后端时间戳变化时重置计时；暂停/中断续跑沿用原 started_at 不清零
    const isNewRun = Boolean(next && (
      !prev
      || (nextStarted !== null && nextStarted !== prevStarted)
    ));
    if (next && isNewRun) {
      startedAt.value = nextStarted ?? Date.now();
      finishedAt.value = typeof next.finished_at === "number" ? next.finished_at : null;
      // 新 run 不沿用旧任务的显示位置；同步清零后立即回到当前真实锚点。
      displayPercent.value = 0;
      queueMicrotask(() => { displayPercent.value = realAnchor.value; });
    }
    // 后端累计实际运行时长（排除暂停）：数值变化时刷新基准，供运行中叠加当前段。
    const nextActive = typeof next?.active_elapsed_ms === "number" ? next.active_elapsed_ms : null;
    if (next && (isNewRun || (nextActive !== null && nextActive !== activeElapsedMs.value))) {
      activeElapsedMs.value = nextActive;
      activeElapsedAt.value = Date.now();
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
      intervalId = window.setInterval(() => { tickTok.value++; }, 1000);
    }
  },
  { immediate: true },
);

const elapsedMs = computed(() => {
  void tickTok.value; // 每秒递增，强制 computed 重新求值
  // 后端提供排除暂停的累计实际运行时长时优先使用：
  // 暂停/终态为定格累计；运行中叠加当前运行段的本地增量。
  if (activeElapsedMs.value !== null) {
    if (isTerminalStatus(props.snapshot?.status)) return activeElapsedMs.value;
    return activeElapsedMs.value + Math.max(0, Date.now() - activeElapsedAt.value);
  }
  if (startedAt.value === null) return 0;
  const end = finishedAt.value ?? Date.now();
  return Math.max(0, end - startedAt.value);
});

function formatDuration(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}小时${m}分${s.toString().padStart(2, "0")}秒`;
  if (m === 0) return `${s}秒`;
  return `${m}分${s.toString().padStart(2, "0")}秒`;
}

const elapsedLabel = computed(() => formatDuration(elapsedMs.value));

const rawProgress = computed(() => props.snapshot?.progress);
const progress = computed<Record<string, unknown>>(() => {
  const raw = rawProgress.value;
  return raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
});
// 兼容旧快照把 progress 直接写成数字百分比（老 /api/task-state 形状）。
const progressPercent = computed(() => {
  const raw = rawProgress.value;
  if (typeof raw === "number") return raw;
  const overall = (raw as Record<string, unknown> | undefined)?.overall_percent;
  return typeof overall === "number" ? overall : Number.NaN;
});
const current = computed(() => Number(progress.value.current || 0));
const total = computed(() => Number(progress.value.total || 0));
const stage = computed(() => String(progress.value.stage || props.snapshot?.stage || ""));

// 真实锚点：后端 overall_percent 是唯一权威值；旧接口/测试快照缺省时
// 回退到 current/total 的真实完成比例，不再使用阶段权重或时间预估。
const realAnchor = computed(() => {
  if (isCompletedStatus(props.snapshot?.status)) return 100;
  const overall = progressPercent.value;
  if (!Number.isNaN(overall) && overall >= 0) {
    return Math.min(100, Math.max(0, overall));
  }
  if (["failed", "cancelled"].includes(props.snapshot?.status || "")) return 0;
  if (total.value > 0) {
    return Math.min(100, Math.max(0, (current.value * 100) / total.value));
  }
  return 0;
});

displayPercent.value = realAnchor.value;

let rafId: number | undefined;

function tick() {
  const status = props.snapshot?.status;

  // 暂停/终态：直接定格真实锚点，不再逐帧追赶。
  if (status === "paused" || isTerminalStatus(status)) {
    displayPercent.value = realAnchor.value;
    rafId = undefined;
    return;
  }

  // 运行态：只向真实锚点平滑追赶，真实值不变时显示值也保持不变。
  const delta = realAnchor.value - displayPercent.value;
  if (delta > 0.01) {
    displayPercent.value += Math.min(delta, Math.max(0.5, delta * 0.2));
    rafId = requestAnimationFrame(tick);
  } else {
    displayPercent.value = realAnchor.value;
    rafId = undefined;
  }
}

// 状态变化：paused/终态停 RAF；恢复运行时重启 RAF。
watch(() => props.snapshot?.status, (status) => {
  if (status === "paused" || isTerminalStatus(status)) {
    if (rafId !== undefined) cancelAnimationFrame(rafId);
    rafId = undefined;
    displayPercent.value = realAnchor.value;
    return;
  }
  if (rafId === undefined) {
    rafId = requestAnimationFrame(tick);
  }
}, { immediate: true });

// 真实锚点变化后若动画已停，重新启动追赶，保证新事件推进仍被显示。
watch(realAnchor, () => {
  const status = props.snapshot?.status;
  if (status === "paused" || isTerminalStatus(status)) {
    if (rafId !== undefined) cancelAnimationFrame(rafId);
    rafId = undefined;
    displayPercent.value = realAnchor.value;
    return;
  }
  if (rafId === undefined) rafId = requestAnimationFrame(tick);
});

onBeforeUnmount(() => {
  if (intervalId !== undefined) clearInterval(intervalId);
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
  if (props.snapshot?.status === "pausing") return "正在暂停";
  return "运行中";
});

// 无障碍播报只包含状态/阶段/关键计数，百分比、用时与进度文案不参与，
// 因此每秒 tick 不会触发 aria-live 重复播报。
const announcementText = computed(() => {
  const parts: string[] = [statusLabel.value];
  if (stageLabel.value && stageLabel.value !== "处理中") parts.push(stageLabel.value);
  if (scrapedCount.value > 0) parts.push(`已抓取 ${scrapedCount.value} 个岗位`);
  if (currentCompletedCount.value > 0) parts.push(`已完成 ${currentCompletedCount.value}`);
  if (keptCount.value > 0) parts.push(`保留 ${keptCount.value}`);
  if (droppedCount.value > 0) parts.push(`淘汰 ${droppedCount.value}`);
  if (pendingCount.value > 0) parts.push(`待确认 ${pendingCount.value}`);
  if (failCount.value > 0) parts.push(`失败 ${failCount.value}`);
  return parts.join("，");
});

const platformLabel = computed(() => {
  if (!props.snapshot?.platform) return "";
  return props.snapshot.platform === "boss" ? "BOSS" : "智联";
});

// 阶段中文标签：所有已知内部阶段都映射为中文，未知阶段使用中文兜底，
// 任何路径都不再把原始英文 stage 直接渲染到界面。
const STAGE_LABELS: Record<string, string> = {
  scrape: "列表抓取",
  ensure_chrome: "启动浏览器",
  preflight: "登录检查",
  searching: "列表抓取",
  combo_done: "列表抓取",
  page_done: "列表抓取",
  combo_failed: "组合失败",
  waiting: "防限流等待",
  risk_warning: "风险提示",
  closing_chrome: "关闭浏览器",
  hard_stop: "任务已暂停",
  jd_detail: "JD 详情抓取",
  fetch_jd: "JD 详情抓取",
  ai_rough: "AI 粗筛",
  screen_a: "AI 粗筛",
  screen_a_done: "粗筛完成",
  ai_fine: "AI 精筛",
  screen_b: "AI 精筛",
  recrawl_submit: "提交重抓",
  recrawl_fetch_jd: "重抓 JD 详情",
  recrawl_jd: "重抓 JD 详情",
  recrawl_ai: "AI 重新判定",
  resume: "恢复进度",
  done: "已完成",
  cancelled: "已停止",
  unknown: "处理中",
};

const stageLabel = computed(() => {
  const raw = props.snapshot?.stage || String(progress.value.stage || "");
  if (!raw) return "";
  return STAGE_LABELS[raw] || "处理中";
});

const failureVisible = computed(() => {
  const status = props.snapshot?.status;
  return status === "failed" || status === "paused";
});

// B052：暂停/失败统一内联展示「中文原因 · 错误字段」，错误字段红色。
const failureLine = computed(() => {
  const status = props.snapshot?.status;
  if (status === "paused") {
    const pi = props.snapshot?.pause_info;
    return {
      reason: pi?.error_reason || props.snapshot?.error || "任务已暂停，请处理后点继续",
      code: pi?.error_code || "",
    };
  }
  if (status === "failed") {
    const pi = props.snapshot?.pause_info;
    return {
      reason: props.snapshot?.error || message.value || "执行失败",
      code: pi?.error_code || "",
    };
  }
  return { reason: "", code: "" };
});


// 切片7：完整计数画面（FR-037）。total>0 时才显示
const scrapedCount = computed(() => Number(props.snapshot?.scraped_count || 0));
const showCounts = computed(() => {
  const t = Number(props.snapshot?.total || 0);
  if (["done", "completed"].includes(props.snapshot?.status || "")) return false;
  return t > 0 || sourceTotal.value > 0 || scrapedCount.value > 0;
});
const successCount = computed(() => Number(props.snapshot?.success_count || 0));
const failCount = computed(() => Number(props.snapshot?.fail_count || 0));
const unstartedCount = computed(() => Number(props.snapshot?.unstarted_count || 0));
const totalCount = computed(() => Number(props.snapshot?.total || 0));
// 抓取进度的 current/total 是真实已完成的组合数；后端的 success/unstarted 是
// AI 筛选维度，因此抓取时必须从组合事件本身派生，避免把岗位计数混进组合画面。
const scrapeCountState = computed(() => {
  const comboTotal = totalCount.value || total.value;
  if (props.kind !== "scrape" || comboTotal <= 0) {
    return { completed: successCount.value, running: 0, unstarted: unstartedCount.value };
  }
  const comboCurrent = Math.min(comboTotal, Math.max(0, current.value));
  if (stage.value === "combo_done") {
    return { completed: comboCurrent, running: 0, unstarted: Math.max(0, comboTotal - comboCurrent) };
  }
  if (["searching", "waiting", "page_done"].includes(stage.value)) {
    const completed = Math.min(comboTotal, comboCurrent);
    const running = completed < comboTotal ? 1 : 0;
    return { completed, running, unstarted: Math.max(0, comboTotal - completed - running) };
  }
  if (stage.value === "combo_failed") {
    return { completed: comboCurrent, running: 0, unstarted: Math.max(0, comboTotal - comboCurrent) };
  }
  return { completed: 0, running: 0, unstarted: comboTotal };
});
const currentCompletedCount = computed(() => scrapeCountState.value.completed);
const currentRunningCount = computed(() => scrapeCountState.value.running);
const currentUnstartedCount = computed(() => scrapeCountState.value.unstarted);
const pageInfo = computed(() => {
  const page = Number(progress.value.page || 0);
  const target = Number(progress.value.target_pages || 0);
  if (page <= 0 || target <= 0) return { show: false, page: 0, target: 0 };
  return { show: true, page, target };
});
const sourceTotal = computed(() => Number(props.snapshot?.source_total || 0));
const pendingCount = computed(() => Number(props.snapshot?.pending_count || 0));
const keptCount = computed(() => Number(props.snapshot?.kept_count || 0));
const droppedCount = computed(() => Number(props.snapshot?.dropped_count || 0));
// 来源组显示条件：来源数与总数不一致（说明经过了粗筛），否则来源数与总数重复。
const showSourceCounts = computed(() => sourceTotal.value > 0 && sourceTotal.value !== totalCount.value);
// 粗筛组显示条件：来源数与总数不一致，且粗筛已有结果（避免粗筛未完成时一排 0）。
const showRoughCounts = computed(() =>
  showSourceCounts.value && (keptCount.value > 0 || droppedCount.value > 0)
);
// 待确认：有待确认项时才显示。
const showPending = computed(() => pendingCount.value > 0);
// 失败：失败 > 0 且没有待确认时才显示（待确认是 fail 的子集，互斥显示避免重复）。
const showFailCount = computed(() => failCount.value > 0 && pendingCount.value === 0);

// 终态显示绝对用时；运行中显示"已用 X 秒"。
// 后端提供累计实际运行时长时，暂停/终态显示定格累计（不依赖 finished_at）。
const timeLabel = computed(() => {
  const terminal = isTerminalStatus(props.snapshot?.status);
  if (activeElapsedMs.value !== null) {
    return terminal ? `用时 ${elapsedLabel.value}` : `已用 ${elapsedLabel.value}`;
  }
  if (startedAt.value === null) return "";
  if (terminal && finishedAt.value === null) return "";
  return terminal ? `用时 ${elapsedLabel.value}` : `已用 ${elapsedLabel.value}`;
});
</script>

<template>
  <section v-if="snapshot" class="task-progress" :data-blocked="blocked || undefined">
    <p class="sr-only" aria-live="polite" data-testid="task-progress-announcement">{{ announcementText }}</p>
    <header>
      <span class="task-status" :data-status="snapshot.status || 'running'">
        <CircleCheck v-if="isCompletedStatus(snapshot.status)" :size="17" aria-hidden="true" />
        <CircleX v-else-if="snapshot.status === 'failed'" :size="17" aria-hidden="true" />
        <PauseCircle v-else-if="snapshot.status === 'paused'" :size="17" aria-hidden="true" />
        <Octagon v-else-if="snapshot.status === 'cancelled'" :size="17" aria-hidden="true" />
        <LoaderCircle v-else class="spin" :size="17" aria-hidden="true" />
        {{ statusLabel }}
      </span>
      <span
        v-if="platformLabel"
        class="task-platform"
        :data-platform="snapshot.platform"
        data-testid="task-platform-badge"
      >· {{ platformLabel }}</span>
      <span v-if="stageLabel" class="task-stage">· {{ stageLabel }}</span>
      <span v-if="timeLabel" class="task-elapsed">· {{ timeLabel }}</span>
      <span class="task-percentage">{{ percentage }}%</span>
    </header>
    <div class="progress-track" aria-hidden="true">
      <span :style="{ width: `${percentage}%` }" />
    </div>
    <!-- B052：暂停/失败统一内联原因 + 红色错误字段；无独立诊断盒、无复制按钮 -->
    <p v-if="failureVisible" class="task-message task-pause-reason" data-testid="pause-reason">
      <PauseCircle v-if="snapshot.status === 'paused'" :size="14" aria-hidden="true" />
      {{ failureLine.reason }}<span v-if="failureLine.code" class="error-field" data-testid="error-field"> · {{ failureLine.code }}</span>
    </p>
    <p v-else class="task-message">{{ snapshot.error || message }}</p>
    <!-- 切片7：完整计数画面（FR-037）。按语义分组：来源 / 粗筛 / 当前阶段 / 待确认 / 失败 -->
    <div v-if="showCounts" class="task-counts" data-testid="task-counts">
      <div v-if="scrapedCount > 0" class="count-group count-scraped">
        <span class="count-label">已抓</span>
        <span class="count-chip scraped" data-testid="scraped-count">{{ scrapedCount }} 个岗位</span>
      </div>
      <div v-if="showSourceCounts" class="count-group count-source">
        <span class="count-label">来源</span>
        <span class="count-chip source">列表 {{ sourceTotal }}</span>
      </div>
      <div v-if="showRoughCounts" class="count-group count-rough">
        <span class="count-label">粗筛</span>
        <span class="count-row">
          <span class="count-chip kept">保留 {{ keptCount }}</span>
          <span class="count-sep" aria-hidden="true">·</span>
          <span class="count-chip dropped">淘汰 {{ droppedCount }}</span>
        </span>
      </div>
      <div class="count-group count-current">
        <span class="count-label">当前</span>
        <span class="count-row">
          <span class="count-chip success">已完成 {{ currentCompletedCount }} / {{ totalCount }}</span>
          <span v-if="currentRunningCount" class="count-chip running">进行中 {{ currentRunningCount }}</span>
          <span class="count-chip unstarted">未开始 {{ currentUnstartedCount }}</span>
        </span>
      </div>
      <div v-if="props.kind === 'scrape' && pageInfo.show" class="count-group count-page">
        <span class="count-label">页</span>
        <span class="count-chip page" data-testid="page-progress">第 {{ pageInfo.page }} / {{ pageInfo.target }} 页</span>
      </div>
      <div v-if="showPending" class="count-group count-pending">
        <span class="count-label">待确认</span>
        <span class="count-chip pending">{{ pendingCount }}</span>
      </div>
      <div v-if="showFailCount" class="count-group count-fail">
        <span class="count-label">失败</span>
        <span class="count-chip fail">{{ failCount }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.error-field {
  color: var(--danger);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
</style>
