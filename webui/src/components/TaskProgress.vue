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
  execution_config?: Record<string, unknown> | null;
  // T510：任务自身平台，用于在 header 展示真实平台徽章（http-api.md L201）。
  // 由父组件从 /api/latest-running-task 或 /api/task-state 透传；草稿平台切换不影响此处。
  platform?: Platform;
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

// 记录每个阶段进入时间（epoch 毫秒），用于平台期匀速爬升。
// 任务重置时清空；新阶段首次出现时由 watch(stage) 记录。
const stageEnterTimes = ref<Record<string, number>>({});

// 进度条显示值；新任务开始时重置到真实锚点，避免同阶段新 run 沿用旧位置。
const displayPercent = ref(0);

function resetTimer() {
  startedAt.value = null;
  finishedAt.value = null;
  stageEnterTimes.value = {};
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
      // 新任务：清空阶段进入时间表，并立即记录当前阶段。
      // 同一 stage 续跑（如继续抓 JD）时 watch(stage) 不会触发，必须在这里重建时间。
      const initialStage = String(next.progress?.stage || next.stage || "");
      stageEnterTimes.value = initialStage ? { [initialStage]: Date.now() } : {};
      // 新 run 不沿用旧任务的显示位置；同步清零后立即回到当前阶段真实锚点。
      displayPercent.value = 0;
      queueMicrotask(() => { displayPercent.value = realAnchor.value; });
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
  resume: [0, 2],
  screen_a: [2, 20],
  screen_a_done: [20, 20],
  ensure_chrome: [20, 24],
  fetch_jd: [24, 65],
  screen_b: [65, 100],
  done: [100, 100],
};

// 阶段节奏表：每个阶段预估时长（秒），用于环境爬升分量。
// 重阶段区间大、预估时长长，进度条走得慢；轻阶段区间小、走得快。
// 0 表示该阶段不参与时间爬升（静态等待或瞬态）。
// waiting（防限流等待）必须配时长，否则等待阶段会停住——它是平台期最长的地方。
// 固定基础设施阶段时长（秒）：没有组合数/岗位数可换算的阶段。
const FIXED_STAGE_DURATIONS: Record<string, number> = {
  resume: 0,
  screen_a_done: 0,
  ensure_chrome: 15,
  preflight: 8,
  combo_done: 0,
  combo_failed: 0,
  risk_warning: 0,
  closing_chrome: 10,
  done: 0,
};

// 有工作量可换算的阶段：perUnit 秒/单位，base 秒兜底。
// 预估时长按组合数/岗位数换算，不使用固定墙钟值。
const WORK_STAGE_DURATIONS: Record<string, { perUnit: number; base: number }> = {
  screen_a: { perUnit: 1.5, base: 15 },
  fetch_jd: { perUnit: 5, base: 15 },
  screen_b: { perUnit: 5, base: 30 },
  searching: { perUnit: 20, base: 20 },
  waiting: { perUnit: 2, base: 8 },
};

const progress = computed(() => props.snapshot?.progress || {});
const current = computed(() => Number(progress.value.current || 0));
const total = computed(() => Number(progress.value.total || 0));
const stage = computed(() => String(progress.value.stage || props.snapshot?.stage || ""));

// 当前阶段对应的权重区间；无 stage 时返回 undefined。
const stageRange = computed<[number, number] | undefined>(() => {
  const weights = props.kind === "screen" ? SCREEN_WEIGHTS : SCRAPE_WEIGHTS;
  return stage.value ? weights[stage.value] : undefined;
});
const stageEnd = computed(() => stageRange.value?.[1] ?? 100);

// 阶段预估时长按当前工作量换算：progress.total 在抓取阶段是组合数，
// 在筛选阶段是岗位数；waiting 优先使用后端给出的真实等待秒数。
const stageDurationSeconds = computed(() => {
  if (stage.value === "waiting") {
    const wait = Number(progress.value.wait_seconds || 0);
    if (wait > 0) return wait + 3;
  }
  const spec = WORK_STAGE_DURATIONS[stage.value];
  const workUnits = Number(progress.value.total || props.snapshot?.total || 0);
  if (spec) return Math.max(spec.base, spec.perUnit * Math.max(1, workUnits));
  return FIXED_STAGE_DURATIONS[stage.value] ?? 0;
});

// current/total 语义因阶段而异：searching/waiting/combo_* 是关键词维度（第几个关键词），
// 不是岗位进度，不能拿来做阶段内插值——否则关键词一开始 current 就 = total，
// 进度条会瞬间跳到阶段末尾。这类阶段只返回起点，进度交给 ambientTargetAt 时间爬升。
// combo_done 是组合抓取完成后的真实事件，current 是已完成组合数，应作为真实锚点。
// searching/waiting 的 current 是“当前第几个关键词”或等待前计数，不代表已完成进度。
const NON_PROGRESS_STAGES = new Set(["searching", "waiting", "combo_failed"]);

// 真实锚点：stage + current/total 在阶段区间内插值。
// 只在批处理完成时（current/total 变化）变化；暂停态直接用它定格。
// 终态返回 100。无 stage 时用后端 overall_percent 兜底（旧接口）。
const realAnchor = computed(() => {
  if (isCompletedStatus(props.snapshot?.status)) return 100;
  // 暂停态：后端 overall_percent 是权威定格值，优先使用，避免 stage 兜底后误用阶段起点。
  if (props.snapshot?.status === "paused") {
    const overall = Number(progress.value.overall_percent);
    if (!Number.isNaN(overall)) return Math.min(100, overall);
  }
  const range = stageRange.value;
  if (!range) {
    const overall = Number(progress.value.overall_percent);
    if (!Number.isNaN(overall)) return Math.min(100, overall);
    return total.value > 0 ? Math.min(100, (current.value * 100) / total.value) : 0;
  }
  const [start, end] = range;
  // 关键词/等待类阶段：current/total 不代表岗位进度，只返回起点
  if (NON_PROGRESS_STAGES.has(stage.value)) return start;
  if (total.value <= 0) return start;
  const ratio = Math.min(1, Math.max(0, current.value / total.value));
  return start + (end - start) * ratio;
});

// 挂载时以当前阶段真实锚点为初始显示值；后续新 run 由 snapshot watcher 重置。
displayPercent.value = realAnchor.value;

// 软上限常量：环境爬升最多到 阶段起点 + 区间宽度 × SOFT_CAP_RATIO
const SOFT_CAP_RATIO = 0.88;
const CHASE_WINDOW_MS = 600;              // 真实事件后 600ms 内快速追赶
const REAL_CHANGE_THRESHOLD = 0.5;        // 真实锚点增加超过 0.5% 视为真实事件
const PLATFORM_EPSILON = 0.002;           // 平台期 delta 低于此值视为已追上，不动
const PLATFORM_PAUSE_PROB = 0.002;        // 平台期每帧随机插入停顿的概率
const PLATFORM_PAUSE_MIN_MS = 350;        // 平台期随机停顿下限
const PLATFORM_PAUSE_MAX_MS = 900;        // 平台期随机停顿上限
const FRAME_MS = 16;                      // requestAnimationFrame 假定帧间隔
const MICRO_OVER_RATIO_PER_SEC = 0.004;   // 超过预估时长后，每秒再爬区间宽度的 0.4%

// 环境爬升每帧步长（%）= 软上限宽度 ÷ 预估时长 × 帧间隔。与 ambientTargetAt 涨幅同步。
const ambientSpeedPerFrame = computed(() => {
  const range = stageRange.value;
  if (!range) return 0;
  const duration = stageDurationSeconds.value;
  if (duration <= 0) return 0; // 与 ambientTargetAt 对齐：无预估时长的阶段不爬升
  const width = range[1] - range[0];
  const normalPerFrame = (width * SOFT_CAP_RATIO) / (duration * 1000) * FRAME_MS;
  // 超过预估时长后的微动速度也要让 display 追得上 ambientTargetAt。
  const microPerFrame = width * MICRO_OVER_RATIO_PER_SEC * (FRAME_MS / 1000);
  return Math.max(normalPerFrame, microPerFrame);
});

// 逐帧动画状态（非响应式，只在 tick 内部用）
let rafId: number | undefined;
let lastRealAnchor = realAnchor.value;   // 上次真实锚点值，用于检测变化 >0.5%
let lastRealChangeAt = 0;                 // 最近一次真实锚点明显变化的时间戳
let holdUntil = 0;                        // 短停顿结束时间戳；期间 displayPercent 不动


// 环境爬升目标：由阶段进入时间和预估时长驱动，平台期即使 current/total 没变也持续爬。
// 软上限 = 阶段起点 + 区间宽度 × 0.88；超过预估时长后继续极慢微动，避免长时间冻结。
function ambientTargetAt(now: number): number {
  const range = stageRange.value;
  if (!range) return 0;
  const [start, end] = range;
  const width = end - start;
  if (width <= 0) return start;
  const enterTime = stageEnterTimes.value[stage.value];
  const duration = stageDurationSeconds.value;
  if (!enterTime || duration <= 0) return start;
  const elapsedMs = Math.max(0, now - enterTime);
  const durationMs = duration * 1000;
  if (elapsedMs <= durationMs) {
    return start + width * SOFT_CAP_RATIO * (elapsedMs / durationMs);
  }
  // 超过预估时长后不硬停：以极慢速度继续朝阶段终点微动，避免平台期长时间冻结。
  const overshootSec = (elapsedMs - durationMs) / 1000;
  const micro = overshootSec * MICRO_OVER_RATIO_PER_SEC * width;
  return Math.min(end, start + width * SOFT_CAP_RATIO + micro);
}

// 阶段首次出现时记录进入时间；同阶段多次推送不覆盖。
// immediate 保证组件挂载时如果已有 stage，也会记录，用于环境爬升。
// stage 切换时重置追赶/停顿状态，避免上一阶段的 holdUntil/lastRealChangeAt 污染新阶段。
watch(stage, (next, prev) => {
  if (!next) return;
  // 同阶段新 run 由 snapshot watcher 重建时间表；这里负责正常进入、重入和缺失兜底。
  if (next !== prev || !stageEnterTimes.value[next]) {
    stageEnterTimes.value = { ...stageEnterTimes.value, [next]: Date.now() };
  }
  // 阶段切换：重置追赶/停顿状态，保留 displayPercent（不归零，新阶段起点高于显示值就继续追）
  if (next !== prev) {
    lastRealAnchor = realAnchor.value;
    lastRealChangeAt = 0;
    holdUntil = 0;
  }
}, { immediate: true });

function tick() {
  const now = Date.now();
  const status = props.snapshot?.status;

  // 终态：追到 100 后停 RAF
  if (isCompletedStatus(status)) {
    const delta = 100 - displayPercent.value;
    if (Math.abs(delta) < 0.1) {
      displayPercent.value = 100;
      rafId = undefined;
      return;
    }
    displayPercent.value += Math.sign(delta) * Math.min(Math.abs(delta), 0.5);
    rafId = requestAnimationFrame(tick);
    return;
  }

  // 暂停态：定格在真实锚点，停 RAF（恢复时由 watch(status) 重新启动）
  if (status === "paused") {
    displayPercent.value = realAnchor.value;
    rafId = undefined;
    return;
  }

  // 运行态：逐帧实时计算 target = max(realAnchor, ambient)，不超过阶段 end
  const ambient = ambientTargetAt(now);
  const target = Math.min(stageEnd.value, Math.max(realAnchor.value, ambient));
  const delta = target - displayPercent.value;

  // 真实事件刚发生（CHASE_WINDOW_MS 内）：快速追赶，大步走
  if (lastRealChangeAt > 0 && now - lastRealChangeAt < CHASE_WINDOW_MS) {
    if (delta > 0) {
      const speed = Math.min(1.0, 0.05 + Math.abs(delta) * 0.12);
      displayPercent.value += Math.min(Math.abs(delta), speed);
    }
  }
  // 短停顿期：不动（追赶完成后的"消化"停顿，或平台期随机插入的停顿）
  else if (now < holdUntil) {
    // no-op
  }
  // 平台期：按阶段节奏慢爬，只前进不后退
  else if (delta > PLATFORM_EPSILON) {
    const realLead = realAnchor.value - displayPercent.value;
    let step: number;
    if (realLead > PLATFORM_EPSILON) {
      // 真实锚点领先（追赶期 600ms 未追完）：继续较快追赶，避免大跳变后卡在半路
      step = Math.min(realLead, 0.05);
    } else {
      // 追 ambient：按环境爬升速率慢爬，与软上限涨幅同步
      step = Math.min(delta, Math.max(ambientSpeedPerFrame.value, 0.001));
    }
    displayPercent.value += step;

    // 平台期随机插入短停顿（模拟处理卡顿），约 PLATFORM_PAUSE_PROB/帧概率
    if (Math.random() < PLATFORM_PAUSE_PROB) {
      holdUntil = now + PLATFORM_PAUSE_MIN_MS + Math.random() * (PLATFORM_PAUSE_MAX_MS - PLATFORM_PAUSE_MIN_MS);
    }
  }

  rafId = requestAnimationFrame(tick);
}

// 真实锚点变化检测：增加超过 REAL_CHANGE_THRESHOLD 视为真实事件，记 lastRealChangeAt，
// 并在追赶窗口（CHASE_WINDOW_MS）结束后随机停顿 400-1000ms 模拟"处理完成后的消化"。
// 注意：holdUntil 必须排在 chase 窗口之后，否则 chase 分支优先级更高会吃掉停顿。
watch(realAnchor, (next, prev) => {
  if (next - prev > REAL_CHANGE_THRESHOLD) {
    lastRealChangeAt = Date.now();
    holdUntil = lastRealChangeAt + CHASE_WINDOW_MS + 400 + Math.random() * 600;
  }
  lastRealAnchor = next;
});

// 状态变化：paused/终态停 RAF；恢复运行时重启 RAF。
// immediate：组件挂载时若已是 running 态，立即启动 RAF（第一版靠 watch(rawPercentage) 触发，已删）。
watch(() => props.snapshot?.status, (status) => {
  if (status === "paused" || isTerminalStatus(status)) {
    if (rafId !== undefined) cancelAnimationFrame(rafId);
    rafId = undefined;
    // paused 与终态都必须把显示值校正到真实锚点：后端常在几毫秒内先后推送
    // stage=done 和 status=done，轮询一次性收到两者时 RAF 被立即取消，
    // 不校正就会把进度条永久冻结在最后一帧（如抓取完成卡在 9x%）。
    displayPercent.value = realAnchor.value;
    return;
  }
  if (rafId === undefined) {
    rafId = requestAnimationFrame(tick);
  }
}, { immediate: true });

onBeforeUnmount(() => {
  // 集中清理：用时计时的 interval + 进度条的 RAF，避免分散漏清理
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
  return "运行中";
});

// T510：任务自身平台徽章（http-api.md L201）。仅当 snapshot.platform 存在时显示；
// 草稿平台切换不影响此处 — 这里展示的是任务自身平台，与 .platform-segment 草稿徽章独立。
const platformLabel = computed(() => {
  if (!props.snapshot?.platform) return "";
  return props.snapshot.platform === "boss" ? "BOSS" : "智联";
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
// 抓取进度的 current/total 是组合维度；后端的 success/unstarted 是 AI 筛选维度，
// 因此抓取时必须从组合事件本身派生，避免显示“正在第 2/2 组，但已完成 0、未开始 2”。
const scrapeCountState = computed(() => {
  const comboTotal = totalCount.value || total.value;
  if (props.kind !== "scrape" || comboTotal <= 0) {
    return { completed: successCount.value, running: 0, unstarted: unstartedCount.value };
  }
  const comboCurrent = Math.min(comboTotal, Math.max(0, current.value));
  if (stage.value === "combo_done") {
    return { completed: comboCurrent, running: 0, unstarted: Math.max(0, comboTotal - comboCurrent) };
  }
  if (["searching", "waiting"].includes(stage.value) && comboCurrent > 0) {
    const completed = Math.max(0, comboCurrent - 1);
    return { completed, running: 1, unstarted: Math.max(0, comboTotal - completed - 1) };
  }
  return { completed: 0, running: 0, unstarted: comboTotal };
});
const currentCompletedCount = computed(() => scrapeCountState.value.completed);
const currentRunningCount = computed(() => scrapeCountState.value.running);
const currentUnstartedCount = computed(() => scrapeCountState.value.unstarted);
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
    <!-- 切片7：暂停时显示具体原因（SC-006）；其他状态显示 message/error -->
    <p v-if="snapshot.status === 'paused'" class="task-message task-pause-reason" data-testid="pause-reason">
      <PauseCircle :size="14" aria-hidden="true" />{{ pauseReason }}
    </p>
    <p v-else class="task-message">{{ snapshot.error || message }}</p>
    <!-- 切片7：完整计数画面（FR-037）。按语义分组：来源 / 粗筛 / 当前阶段 / 待确认 / 失败 -->
    <div v-if="showCounts" class="task-counts" data-testid="task-counts">
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
