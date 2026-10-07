<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { CircleCheck, CircleX, LoaderCircle, Octagon, PauseCircle } from "@lucide/vue";
import type { IntegritySnapshot, Platform } from "../types";
import {
  STAGE_COMPLETED_STATUSES,
  UNREADABLE_STAGE_STATUS,
  elapsedRunsLive,
  platformLabel as platformDisplayName,
  stageStatusLabel,
} from "../discovery";
import { ERROR_MESSAGES } from "../errorCodes";

interface PauseInfo {
  error_code?: string;
  error_reason?: string;
}

interface TaskClosureFact {
  kind: "finish" | "stop";
  phase: "pending" | "committed";
}

interface TaskSnapshot {
  status?: string;
  closure?: TaskClosureFact | null;
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
  // 016：软失败组合留痕（最近 20 条倒序）
  combo_issues?: Array<{
    combo_key: string; code: string; code_text: string; reason: string; ts: string;
  }> | null;
  execution_config?: Record<string, unknown> | null;
  // T510：任务自身平台，用于在 header 展示真实平台徽章（http-api.md L201）。
  // 由父组件从 /api/latest-running-task 或 /api/task-state 透传；草稿平台切换不影响此处。
  platform?: Platform;
  // 后端从 task_logs 的 pause/resume 事件推导的累计实际运行时长（排除暂停），
  // 单位毫秒；只有真有活 worker 在跑的状态包含当前段，其余状态都是定格累计。
  active_elapsed_ms?: number;
  integrity?: IntegritySnapshot | null;
}

const props = defineProps<{
  snapshot: TaskSnapshot | null;
  kind?: "scrape" | "screen" | "";
  taskId?: string;
  /** 本卡所属平台：轨道快照没带平台时由父组件下发，播报与徽章共用这一个主体。 */
  platform?: Platform | null;
  /** 本轮是用户主动「结束并保存」收尾的：不把 interrupted 当异常展示。 */
  userFinished?: boolean;
  /**
   * 本轮已收尾（轮次那一份事实：flows + flow_tracks 都说这一轮结束了）。
   * 状态词表里「中断」说的是「无活体 worker 且本轮未结束」，收尾后的 interrupted
   * 只是那段任务留下的残留，展示同样按轮次走——与 userFinished 共用下面那一份豁免，
   * 不在此另起一套判定。
   */
  roundClosed?: boolean;
  /**
   * 047 C3：本轨正常收尾的只读投影（后端推导）。finish pending 表示用户点了
   * 「结束并保存」但结果尚未提交绑定——显示进行中，不声称成功；committed 才是
   * 正常收尾完成。stop 与 finish 语义分开，不互相冒充。
   */
  closure?: { kind: "finish" | "stop"; phase: "pending" | "committed" } | null;
}>();

// 终态与完成态口径以后端 flow_tracks 状态白名单为唯一权威
//（webui/store_flow_core.py：终态 = done / succeeded / failed / stopped / cancelled）；
// completed / completed_with_pending 是任务状态接口对同一事实的公开别名。
// 完成族清单的唯一一份在 discovery.ts（状态词与轨道头部共用同一份计算）。
const COMPLETED_STATUSES = new Set<string>(STAGE_COMPLETED_STATUSES);
const TERMINAL_STATUSES = new Set([
  ...COMPLETED_STATUSES,
  "failed",
  "cancelled",
  "stopped",
  "paused",
  "unavailable",
]);

// 016：与后端 SYSTEMIC_BLOCK_CODES 对齐（统一正名 + 历史别名兼容）
const BLOCK_CODES = new Set([
  "ai_rate_limited", "ai_quota_exhausted", "ai_key_invalid",
  "ai_network_error", "internal_error",
  "source_verification_required", "source_login_required",
  "source_rate_limited", "source_account_restricted",
  "source_blocked", "source_cdp_unavailable",
  "source_request_limit_exceeded", "source_unreachable",
  // 历史别名（旧任务记录仍可能出现）
  "captcha_required", "login_expired", "ip_risk_control", "cdp_unavailable",
]);

const blocked = computed(() => {
  if (["failed", "unverifiable"].includes(integrityConclusion.value)) return true;
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

const closure = computed(() => props.closure ?? props.snapshot?.closure ?? null);

const integrity = computed<IntegritySnapshot | null>(() => {
  const raw = props.snapshot?.integrity ?? null;
  const status = String(props.snapshot?.status || "");
  // 047 C3：正常收尾的 closure 是后端推导的正面证据。真实失败/显式取消仍然
  // 优先：只有非硬失败路径才按 closure 说明收尾，避免迟到回调把错误盖掉。
  const hardFailure = status === "failed" || status === "unavailable" || status === "cancelled";
  if (closure.value?.kind === "finish" && !hardFailure) {
    if (closure.value.phase === "pending") {
      // 结束保存进行中：不显示硬失败，也不声称成功。
      return {
        ...(raw || {}),
        conclusion: "partial",
        label: "正在结束保存",
        primary_code: "user_finished",
        primary_reason: "正在结束保存，请稍候",
      } as IntegritySnapshot;
    }
    // 用户主动「结束并保存」：正常收尾口径（部分完成 / 已结束保存），不报中断。
    return {
      ...(raw || {}),
      conclusion: "partial",
      label: "部分完成",
      primary_code: "user_finished",
      primary_reason: "已结束保存部分结果",
    } as IntegritySnapshot;
  }
  if (!raw || raw.conclusion !== "interrupted") return raw;
  // 用户主动「结束并保存」的轮次：白箱如实记 interrupted（任务确实被停止），
  // 但那是用户自己的收尾动作，展示口径按轮次走（部分完成 / 已结束保存），
  // 不再出现"任务因取消或停止而中断"。
  if (props.userFinished) {
    return {
      ...raw,
      conclusion: "partial",
      label: "部分完成",
      primary_code: "user_finished",
      primary_reason: "已结束保存部分结果",
    };
  }
  // 本轮已收尾（不是用户主动收尾的那一种）：同一份豁免、同一个口径，只是收尾的
  // 主体不是用户，所以不改写错误字段，只把「中断」换成轮次说法。证据没全部落地
  // 就仍然说「部分完成」，不把它谎报成完整成功（046 D-09：已收尾的轮不报中断）。
  if (props.roundClosed) {
    return {
      ...raw,
      conclusion: "partial",
      label: "部分完成",
      primary_reason: "本轮已结束，部分结果未能确认",
    };
  }
  return raw;
});
const integrityConclusion = computed(() => integrity.value?.conclusion || "");
// 显式状态优先于完整性结论：轨道失败而抓取证据「完整成功」时，
// 文案与图标都必须跟状态一致，否则朗读会连成「失败完整成功」。
const explicitStatus = computed(() => String(props.snapshot?.status || ""));
const statusOverridesIntegrity = computed(() => ["failed", "interrupted", "cancelled", "stopped", "unavailable", "paused"]
  .includes(explicitStatus.value));
const iconConclusion = computed(() => (
  statusOverridesIntegrity.value ? explicitStatus.value : (integrityConclusion.value || explicitStatus.value)
));
const integrityStatus = computed(() => {
  switch (integrityConclusion.value) {
    case "succeeded":
    case "empty": return "completed";
    case "partial": return "completed_with_pending";
    case "unverifiable": return "unverifiable";
    case "failed": return "failed";
    case "interrupted": return "interrupted";
    default: return props.snapshot?.status || "running";
  }
});
const integrityStyle = computed(() => {
  if (integrityConclusion.value === "partial") return { color: "var(--unsure)" };
  if (["failed", "unverifiable", "interrupted"].includes(integrityConclusion.value)) {
    return { color: "var(--danger)" };
  }
  return undefined;
});
const integritySuccess = computed(() => integrityConclusion.value
  ? ["succeeded", "empty"].includes(integrityConclusion.value)
  : isCompletedStatus(props.snapshot?.status));

// ---- 用时计时 ----
// snapshot 从 null→非 null 时记开始时间；status 进入终态（done/failed/cancelled）时定格。
// 完成后显示绝对用时；运行中每秒刷新显示"已用 X 秒"。
// 后端 active_elapsed_ms 提供"排除暂停的累计实际运行时长"时优先使用：
// 走活表的状态显示"累计 + 当前段"（active_elapsed_ms 已含当前段，随轮询刷新），
// 其余状态（已中断／已暂停／尚未开始／终态）显示定格累计，不再叠本地增量。
// 刷新页面后仍由后端事件推导，暂停时长不回流。
// 后端两个计时字段都没有时，本地回退钟只在树干判定「真有活 worker 在跑」的状态下才成立
//（见 elapsedRunsLive）；没有证据的段不造起点、不显示时间。
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
    } else if (next && startedAt.value !== null && finishedAt.value === null && elapsedRunsLive(next.status)) {
      // 只有走活表的状态需要每秒 tick；定格的时间没有可走的东西，不空转。
      if (intervalId === undefined) {
        intervalId = window.setInterval(() => { tickTok.value++; }, 1000);
      }
    } else if (intervalId !== undefined) {
      clearInterval(intervalId);
      intervalId = undefined;
    }
  },
  { immediate: true },
);

const elapsedMs = computed(() => {
  void tickTok.value; // 每秒递增，强制 computed 重新求值
  // 后端提供排除暂停的累计实际运行时长时优先使用：
  // 只有走活表的状态（真有活 worker 在跑）才叠加当前段的本地增量；
  // 已中断／已暂停／尚未开始／终态一律停在后端定格值，状态口径来自 discovery.ts。
  if (activeElapsedMs.value !== null) {
    if (!elapsedRunsLive(props.snapshot?.status)) return activeElapsedMs.value;
    return activeElapsedMs.value + Math.max(0, Date.now() - activeElapsedAt.value);
  }
  if (startedAt.value === null) return 0;
  if (finishedAt.value !== null) return Math.max(0, finishedAt.value - startedAt.value);
  // 后端没给任何计时字段、这一段又没有活 worker：不拿本地时钟造一个会走的时间，
  // 门用树干的 elapsedRunsLive，与 timeLabel 同一道判定，不在组件里另抄状态清单。
  if (!elapsedRunsLive(props.snapshot?.status)) return 0;
  return Math.max(0, Date.now() - startedAt.value);
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
  if (["failed", "cancelled", "stopped", "unavailable"].includes(props.snapshot?.status || "")) return 0;
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
const integrityMessage = computed(() => {
  const conclusion = integrityConclusion.value;
  if (!conclusion) return "";
  if (conclusion === "empty") return "已完成，没有找到岗位";
  return String(integrity.value?.primary_reason || integrity.value?.recommendation || "");
});
// 状态词出自树干的那一份计算（discovery.ts）：轨道头部徽章与卡体共用同一个函数，
// 同一张卡不会头部说「完成，但有待确认」、卡体说「无法确认是否完成」。
// 白箱完整性结论仍然由这里说话，头部跟着说同一句，不丢信号。
const statusLabel = computed(() => {
  const status = explicitStatus.value;
  // 047 C3：结束保存进行中的 pending 是正面证据，说明这句话而不是「已暂停」。
  if (closure.value?.kind === "finish" && closure.value.phase === "pending"
      && !["failed", "unavailable", "cancelled"].includes(status)) {
    return "正在结束保存";
  }
  return stageStatusLabel(status, integrityConclusion.value);
});

// 没有证据的段不演成在跑：排队（等待开始）与读不到状态（状态更新中）都用静止图标。
const idleStageStatus = computed(() =>
  explicitStatus.value === UNREADABLE_STAGE_STATUS || explicitStatus.value === "queued",
);

// 本段跑过但这一轮读不到状态：不编「正在准备任务…」这种没依据的进行中旁白，
// 徽章已经说了「状态更新中」，这一行不再重复一句。
const cardMessage = computed(() => {
  if (explicitStatus.value === UNREADABLE_STAGE_STATUS) return "";
  return String(props.snapshot?.error || integrityMessage.value || message.value || "");
});

// 播报主体与可见徽章同源：平台显示名唯一权威是 discovery.ts 的 platformLabel。
const platformLabel = computed(() => platformDisplayName(props.platform || props.snapshot?.platform));

// 无障碍播报只包含平台主体/状态/阶段/关键计数，百分比、用时与进度文案不参与，
// 因此每秒 tick 不会触发 aria-live 重复播报。并行页每张卡各挂一个 aria-live，
// 少了主体就是两句没有主语的「进行中」，听者分不清是哪条平台的线。
const announcementText = computed(() => {
  const parts: string[] = [];
  if (platformLabel.value) parts.push(platformLabel.value);
  parts.push(statusLabel.value);
  if (stageLabel.value && stageLabel.value !== "处理中") parts.push(stageLabel.value);
  if (scrapedCount.value > 0) parts.push(`已抓取 ${scrapedCount.value} 个岗位`);
  if (currentCompletedCount.value > 0) parts.push(`已完成 ${currentCompletedCount.value}`);
  if (keptCount.value > 0) parts.push(`保留 ${keptCount.value}`);
  if (droppedCount.value > 0) parts.push(`淘汰 ${droppedCount.value}`);
  if (pendingCount.value > 0) parts.push(`待确认 ${pendingCount.value}`);
  if (failCount.value > 0) parts.push(`失败 ${failCount.value}`);
  return parts.join("，");
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
  // 047 C3：结束保存进行中不显示暂停/失败原因；真实失败（failed/unavailable）
  // 与显式取消仍然显示。
  if (closure.value?.kind === "finish" && closure.value.phase === "pending"
      && !["failed", "unavailable", "cancelled"].includes(String(status || ""))) {
    return false;
  }
  return status === "failed" || status === "paused" || status === "unavailable"
    || ["failed", "unverifiable", "interrupted"].includes(integrityConclusion.value);
});

// 039：失败细节不再成排展示；用户悬停失败数量时才逐条给出「组合名：简单原因」。
// 空结果是中性留痕（不是失败），不进失败浮窗；展示上限 5 条，超出提示共多少条。
const comboIssues = computed(() =>
  (props.snapshot?.combo_issues || []).filter((issue) => issue.code !== "combo_empty"),
);
const failTooltipRows = computed(() => comboIssues.value.slice(0, 5));
const failTooltipMore = computed(() => Math.max(0, comboIssues.value.length - 5));
const failTooltipVisible = ref(false);
function issueReason(issue: { code_text?: string; reason?: string }): string {
  // 统一注册表的简短名称优先；缺失时回落诊断原因，不用「已跳过」这类词代替原因。
  return String(issue.code_text || issue.reason || "抓取未完成");
}

// B052：暂停/失败统一内联展示「中文原因 · 错误字段」，错误字段红色。
const failureLine = computed(() => {
  const status = props.snapshot?.status;
  if (status === "paused") {
    const pi = props.snapshot?.pause_info;
    const code = pi?.error_code || "";
    return {
      reason: pi?.error_reason || props.snapshot?.error || ERROR_MESSAGES[code] || "任务已暂停，请处理后点继续",
      code,
    };
  }
  if (["failed", "unverifiable", "interrupted"].includes(integrityConclusion.value)) {
    const reason = integrity.value?.primary_reason || integrityMessage.value || "无法确认是否完成";
    const recommendation = integrityConclusion.value === "unverifiable"
      ? integrity.value?.recommendation
      : "";
    return {
      reason: recommendation ? `${reason}；${recommendation}` : reason,
      code: integrity.value?.primary_code || "",
    };
  }
  if (status === "failed") {
    const pi = props.snapshot?.pause_info;
    const code = pi?.error_code || "";
    return {
      reason: pi?.error_reason || props.snapshot?.error || ERROR_MESSAGES[code] || message.value || "执行失败",
      code,
    };
  }
  if (status === "unavailable") {
    const pi = props.snapshot?.pause_info;
    const code = pi?.error_code || "";
    return {
      reason: pi?.error_reason || props.snapshot?.error || ERROR_MESSAGES[code] || "平台运行线暂不可用",
      code,
    };
  }
  return { reason: "", code: "" };
});


// 切片7：完整计数画面（FR-037）。total>0 时才显示
const scrapedCount = computed(() => Number(props.snapshot?.scraped_count || 0));
const showCounts = computed(() => {
  // 039（FR-016 修订，用户拍板）：收尾后 02/03 两个面板都显示各自口径的计数，
  // 不再按「已完成」隐藏。只要该轮还有可展示的数字（总数 / 来源数 / 已抓岗位）
  // 就展示计数行；只有真正没有任何数字（空白/刚起步快照）才整行不渲染。
  // 禁止因缺少某一个口径的 total 把「已抓 N 个岗位」等真实数字一起收起。
  return Number(props.snapshot?.total || 0) > 0
    || sourceTotal.value > 0
    || scrapedCount.value > 0;
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
  // 暂停快照中的 current/total 是后端已经完成的组合数，直接展示断点，
  // 不把未知或暂停阶段误当成“尚未开始”。
  if (props.snapshot?.status === "paused") {
    return { completed: comboCurrent, running: 0, unstarted: Math.max(0, comboTotal - comboCurrent) };
  }
  // 039 收尾/未知阶段：以后端真实结论为准（抓完 + 跳过 + 未开始 = 总数），
  // 不再写死“0 完成、全部未开始”这类与失败数量并存的矛盾计数。
  const settledCompleted = Math.min(comboTotal, Math.max(0, successCount.value));
  const settledSkipped = Math.min(
    comboTotal - settledCompleted, Math.max(0, failCount.value));
  return {
    completed: settledCompleted,
    running: 0,
    unstarted: Math.max(0, comboTotal - settledCompleted - settledSkipped),
  };
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
  // 后端两个计时字段都没有时，本地回退钟只有两种拿得住的证据：后端真实结束时间
  //（定格成用时），或树干判定这段真有活 worker 在跑（可以往下走）。
  // 都没有就是没有任何时间可说：整行收起，不凭空长出一个会走的钟。
  // 没有真实结束时间的历史数据同样不伪造，避免出现"用时 0秒"。
  if (finishedAt.value === null && !elapsedRunsLive(props.snapshot?.status)) return "";
  return terminal ? `用时 ${elapsedLabel.value}` : `已用 ${elapsedLabel.value}`;
});
</script>

<template>
  <section v-if="snapshot" class="task-progress" :data-blocked="blocked || undefined" :data-integrity="integrityConclusion || undefined">
    <p class="sr-only" aria-live="polite" data-testid="task-progress-announcement">{{ announcementText }}</p>
    <header>
      <span class="task-status" :data-status="integrityStatus" :style="integrityStyle">
        <CircleCheck v-if="!statusOverridesIntegrity && integritySuccess" :size="17" aria-hidden="true" />
        <CircleX v-else-if="['failed', 'unverifiable', 'unavailable'].includes(iconConclusion)" :size="17" aria-hidden="true" />
        <PauseCircle v-else-if="explicitStatus === 'paused' || explicitStatus === 'interrupted' || integrityConclusion === 'interrupted'" :size="17" aria-hidden="true" />
        <Octagon v-else-if="snapshot.status === 'cancelled' || snapshot.status === 'stopped'" :size="17" aria-hidden="true" />
        <!-- 排队 / 读不到状态：这一段没有证据可说，图标静止，不用转圈把它演成在跑。 -->
        <!-- 状态已进终态同样静止：本轮收尾后按轮次口径改写的「部分完成」也是一张已停的卡，
             转圈是把已收尾的轮演成还在跑（与「用时/已用」同一个进行时口径）。 -->
        <LoaderCircle v-else-if="idleStageStatus || isTerminalStatus(snapshot.status)" :size="17" aria-hidden="true" />
        <LoaderCircle v-else class="spin" :size="17" aria-hidden="true" />
        {{ statusLabel }}
      </span>
      <span
        v-if="platformLabel"
        class="task-platform"
        :data-platform="snapshot.platform || platform"
        data-testid="task-platform-badge"
      >· {{ platformLabel }}</span>
      <span v-if="stageLabel" class="task-stage">· {{ stageLabel }}</span>
      <span v-if="timeLabel" class="task-elapsed">· {{ timeLabel }}</span>
      <span v-if="snapshot.status !== 'unavailable'" class="task-percentage">{{ percentage }}%</span>
    </header>
    <div v-if="snapshot.status !== 'unavailable'" class="progress-track" aria-hidden="true">
      <span :style="{ width: `${percentage}%` }" />
    </div>
    <!-- B052：暂停/失败统一内联原因 + 红色错误字段；无独立诊断盒、无复制按钮 -->
    <p v-if="failureVisible" class="task-message task-pause-reason" data-testid="pause-reason">
      <PauseCircle v-if="snapshot.status === 'paused'" :size="14" aria-hidden="true" />
      {{ failureLine.reason }}<span v-if="failureLine.code" class="error-field" data-testid="error-field"> · {{ failureLine.code }}</span>
    </p>
    <p v-else-if="cardMessage" class="task-message">{{ cardMessage }}</p>
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
      <!-- 039：完成/未开始需要真实分母；分母未知（0）时不渲染「已完成 0 / 0」空数字，
           但该轮其它真实数字（已抓/来源/失败）照常展示，不整行收起。 -->
      <div v-if="totalCount > 0" class="count-group count-current">
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
      <div
        v-if="showFailCount"
        class="count-group count-fail"
        data-testid="fail-count-group"
        @mouseenter="failTooltipVisible = true"
        @mouseleave="failTooltipVisible = false"
      >
        <span class="count-label">失败</span>
        <span
          class="count-chip fail"
          tabindex="0"
          data-testid="fail-count"
          @focus="failTooltipVisible = true"
          @blur="failTooltipVisible = false"
        >{{ failCount }}</span>
        <div
          v-if="failTooltipVisible && failTooltipRows.length"
          class="fail-tooltip"
          role="tooltip"
          data-testid="fail-tooltip"
        >
          <p
            v-for="issue in failTooltipRows"
            :key="`${issue.combo_key}:${issue.ts}`"
            class="fail-tooltip-row"
          >{{ issue.combo_key || "组合" }}：{{ issueReason(issue) }}</p>
          <p v-if="failTooltipMore" class="fail-tooltip-more">另有 {{ failTooltipMore }} 条</p>
        </div>
      </div>
    </div>
    <!-- 可选落点：并行轨道把这一条线的动作条交进来，于是按钮落在卡边框内、
         左缘随卡内 padding 对齐；单平台调用点不传子节点，这里什么都不会出现。 -->
    <slot />
  </section>
</template>

<style scoped>
/* 卡内落点里的动作行：与计数行同一个上边距节奏（边框与背景仍然只有 .task-progress 一份）。 */
.task-progress :deep(.screen-round-actions) {
  margin-top: 10px;
}
/* 039：失败只是一个数字；用户主动悬停时才出现逐条原因的小浮窗（弱化样式，不抢暂停原因焦点）。 */
.count-fail {
  position: relative;
}
.fail-tooltip {
  position: absolute;
  right: 0;
  bottom: calc(100% + 6px);
  z-index: 5;
  display: grid;
  gap: 4px;
  min-width: 180px;
  max-width: 320px;
  padding: 6px 9px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: var(--shadow);
}
.fail-tooltip-row {
  margin: 0;
  font-size: 12px;
  line-height: 1.45;
  color: var(--text);
  word-break: break-all;
}
.fail-tooltip-more {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}
.error-field {
  color: var(--danger);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
</style>
