<script setup lang="ts">
// ---------------------------------------------------------------------------
// 提醒抽屉（Task 006）：当前画像逾期岗位列表、快捷动作与按需建议。
//
// 合同来源：specs/002-job-feedback-reminders/contracts/ui-interaction.md
// （ReminderDrawer 节）、http-api.md（GET /api/job-reminders、
// POST /api/profile-jobs/actions、POST .../advice）。
//
// 边界（禁止行为）：
// - 不本地删除提醒项模拟成功；操作成功后由服务端刷新结果决定该项去留。
// - 不全列表锁定；只锁目标岗位的目标动作，advice loading 不锁跟进/荒废。
// - 不跳转未通过 can_open + 所属平台安全复验的 URL，不拼裸平台 ID。
// - 不在客户端计算提醒资格/天数；elapsed_days 等全部来自服务端投影。
// ---------------------------------------------------------------------------
import { nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";
import { X } from "@lucide/vue";
import {
  JOB_REMINDER_LIST_LIMIT_MAX,
  JobFeedbackRequestContext,
  buildJobIdentity,
  getJobReminders,
  mapJobFeedbackError,
  requestJobAdvice,
  safeCanonicalUrl,
  submitJobFeedbackAction,
} from "../jobFeedback";
import type { JobAdvice, JobReminderItem } from "../jobFeedback";

const props = defineProps<{
  /** 抽屉是否打开；打开时才加载列表。 */
  open: boolean;
  /** 当前求职画像 ID；为空时不请求（App 层也保证不打开）。 */
  profileId: string | null;
}>();

const emit = defineEmits<{
  close: [];
  /** 任一生命周期动作成功后发出，父层据此刷新当前 profile 的 count/list。 */
  jobFeedbackChanged: [];
}>();

// ---------------------------------------------------------------------------
// 列表加载状态
// ---------------------------------------------------------------------------

type LoadPhase = "idle" | "loading" | "error" | "ready";

const items = ref<JobReminderItem[]>([]);
const total = ref(0);
const phase = ref<LoadPhase>("idle");
const loadError = ref("");
/** 加载失败但保留上次成功数据时的旧数据标记。 */
const staleData = ref(false);
/** 动作成功后静默刷新失败时的软提示（保留旧列表）。 */
const notice = ref("");

/** 请求序号：profile 切换或重新打开后，旧响应不得覆盖新状态。 */
let loadSeq = 0;

async function loadReminders(options: { silent?: boolean } = {}): Promise<void> {
  const profileId = props.profileId;
  if (!profileId) return;
  const seq = ++loadSeq;
  if (!options.silent) {
    // 已有旧数据时保留列表、不闪回 loading（抽屉尺寸始终稳定）。
    if (!items.value.length) phase.value = "loading";
    loadError.value = "";
    staleData.value = false;
  }
  try {
    const response = await getJobReminders(profileId);
    if (seq !== loadSeq || props.profileId !== profileId || !props.open) return;
    items.value = response.items;
    total.value = response.total;
    phase.value = "ready";
    staleData.value = false;
    loadError.value = "";
    notice.value = "";
  } catch (error) {
    if (seq !== loadSeq || props.profileId !== profileId || !props.open) return;
    const info = mapJobFeedbackError(error);
    if (options.silent) {
      // 动作已成功，仅刷新失败：保留列表并给出真实原因。
      notice.value = `提醒列表刷新失败：${info.userMessage}`;
      return;
    }
    loadError.value = info.userMessage;
    if (items.value.length && phase.value === "ready") {
      staleData.value = true;
    } else {
      phase.value = "error";
    }
  }
}

// ---------------------------------------------------------------------------
// 逐项 UI 状态：只锁目标项的目标动作；advice 独立不锁动作
// ---------------------------------------------------------------------------

interface ReminderItemUiState {
  busyAction: "" | "follow_up" | "mark_stale";
  actionError: string;
  adviceStatus: "idle" | "loading" | "done" | "error";
  advice: JobAdvice | null;
  adviceError: string;
}

const itemUi = reactive<Record<string, ReminderItemUiState>>({});

function uiFor(jobId: string): ReminderItemUiState {
  let state = itemUi[jobId];
  if (!state) {
    state = {
      busyAction: "",
      actionError: "",
      adviceStatus: "idle",
      advice: null,
      adviceError: "",
    };
    itemUi[jobId] = state;
  }
  return state;
}

async function runAction(item: JobReminderItem, action: "follow_up" | "mark_stale"): Promise<void> {
  const ui = uiFor(item.job_id);
  if (ui.busyAction) return;
  const profileId = props.profileId;
  if (!profileId) return;
  ui.busyAction = action;
  ui.actionError = "";
  // 每次用户确认生成新 request_id；合同层客户端只阻断明显违规，服务端权威验证。
  const context = new JobFeedbackRequestContext();
  try {
    await submitJobFeedbackAction({
      requestId: context.requestId,
      profileId,
      job: buildJobIdentity({ job_id: item.job_id }),
      action,
    });
    // profile 切换后丢弃旧响应；动作已成功则必须通知父层刷新徽标（与抽屉开闭无关）。
    if (profileId !== props.profileId) return;
    emit("jobFeedbackChanged");
    // 该项是否退出提醒由服务端刷新结果决定；不做本地删除。
    if (props.open) await loadReminders({ silent: true });
  } catch (error) {
    if (profileId !== props.profileId) return;
    // 失败保留原项并显示真实 API 文案。
    ui.actionError = mapJobFeedbackError(error).userMessage;
  } finally {
    // 无条件解锁：响应到达时抽屉可能已关闭，不能留下永久禁用锁。
    ui.busyAction = "";
  }
}

async function runAdvice(item: JobReminderItem): Promise<void> {
  const ui = uiFor(item.job_id);
  if (ui.adviceStatus === "loading") return;
  const profileId = props.profileId;
  if (!profileId) return;
  ui.adviceStatus = "loading";
  ui.adviceError = "";
  try {
    const advice = await requestJobAdvice(profileId, item.job_id);
    // 只按 profile 守卫；关闭期间完成也落结果，避免 adviceStatus 永久停留 loading。
    if (profileId !== props.profileId) return;
    // 建议只展示不执行：不提交任何 action，不改动岗位状态。
    ui.advice = advice;
    ui.adviceStatus = "done";
  } catch (error) {
    if (profileId !== props.profileId) return;
    ui.adviceError = mapJobFeedbackError(error).userMessage;
    ui.adviceStatus = "error";
  }
}

// ---------------------------------------------------------------------------
// 跳转安全：can_open（后端投影）+ 所属平台 host/path 复验（纵深防护）
// ---------------------------------------------------------------------------

function openUrl(item: JobReminderItem): string {
  if (!item.can_open) return "";
  return safeCanonicalUrl(item.platform, item.canonical_url) ?? "";
}

function platformLabel(platform: JobReminderItem["platform"]): string {
  return platform === "boss" ? "BOSS" : "智联";
}

function adviceActionLabel(action: JobAdvice["action"]): string {
  return action === "follow_up" ? "建议跟进" : "建议复核";
}

// ---------------------------------------------------------------------------
// 打开/关闭：焦点进入抽屉、Escape 关闭、关闭后焦点恢复
// ---------------------------------------------------------------------------

const panelEl = ref<HTMLElement | null>(null);
const titleEl = ref<HTMLElement | null>(null);
const closeEl = ref<HTMLButtonElement | null>(null);
let previousFocus: HTMLElement | null = null;

const focusableSelector = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    emit("close");
    return;
  }
  if (event.key !== "Tab" || !panelEl.value) return;
  const candidates = Array.from(panelEl.value.querySelectorAll<HTMLElement>(focusableSelector));
  if (!candidates.length) {
    event.preventDefault();
    panelEl.value.focus();
    return;
  }
  const first = candidates[0];
  const last = candidates[candidates.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function resetDrawerState(): void {
  items.value = [];
  total.value = 0;
  phase.value = "idle";
  loadError.value = "";
  staleData.value = false;
  notice.value = "";
  for (const key of Object.keys(itemUi)) delete itemUi[key];
}

/**
 * 重新打开时清理残留锁：若上次关闭前发出的请求长时间未到达，
 * 不应继续禁用按钮；晚到的响应仍会正常落入（profile 守卫仍生效）。
 */
function clearStaleLocks(): void {
  for (const key of Object.keys(itemUi)) {
    const state = itemUi[key];
    state.busyAction = "";
    if (state.adviceStatus === "loading") state.adviceStatus = "idle";
  }
}

watch(() => props.open, (open) => {
  if (open) {
    previousFocus = document.activeElement as HTMLElement | null;
    clearStaleLocks();
    loadReminders();
    nextTick(() => closeEl.value?.focus());
  } else {
    previousFocus?.focus();
  }
}, { immediate: true });

// profile 切换：重置旧数据后重新加载，旧响应被 loadSeq 丢弃。
watch(() => props.profileId, (profileId, oldProfileId) => {
  if (profileId === oldProfileId || !props.open) return;
  resetDrawerState();
  loadReminders();
});

onBeforeUnmount(() => {
  if (props.open) previousFocus?.focus();
});
</script>

<template>
  <div
    v-if="open"
    class="reminder-drawer-backdrop"
    data-testid="reminder-drawer"
    @mousedown.self="emit('close')"
  >
    <aside
      ref="panelEl"
      class="reminder-drawer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="reminder-drawer-title"
      tabindex="-1"
      @keydown="handleKeydown"
    >
      <header class="reminder-drawer-header">
        <div class="reminder-drawer-heading">
          <h2 id="reminder-drawer-title" ref="titleEl" tabindex="-1">投递提醒</h2>
          <p v-if="phase === 'ready'" class="reminder-total" data-testid="reminder-total">
            共 {{ total }} 个逾期岗位
          </p>
        </div>
        <button
          ref="closeEl"
          class="icon-button"
          type="button"
          aria-label="关闭提醒抽屉"
          data-testid="reminder-close"
          @click="emit('close')"
        >
          <X :size="20" aria-hidden="true" />
        </button>
      </header>

      <div class="reminder-drawer-body">
        <div v-if="phase === 'loading'" class="reminder-loading" role="status" data-testid="reminder-loading">
          正在加载提醒列表…
        </div>

        <div v-if="loadError" class="reminder-error" role="alert" data-testid="reminder-error">
          <p>{{ loadError }}</p>
          <p v-if="staleData" class="reminder-stale-note" data-testid="reminder-stale-note">
            以下为上次成功加载的旧数据。
          </p>
          <button class="button secondary" type="button" data-testid="reminder-retry" @click="loadReminders()">
            重试
          </button>
        </div>

        <template v-if="phase === 'ready'">
          <div v-if="!items.length" class="reminder-empty" data-testid="reminder-empty">
            当前没有需要跟进的岗位
          </div>
          <template v-else>
            <p v-if="notice" class="reminder-notice" role="status" data-testid="reminder-notice">
              {{ notice }}
            </p>
            <ul class="reminder-list" data-testid="reminder-list">
              <li
                v-for="item in items"
                :key="item.job_id"
                class="reminder-item"
                data-testid="reminder-item"
                :data-job-id="item.job_id"
              >
                <div class="reminder-item-head">
                  <h3 class="reminder-item-title">{{ item.title || "未知岗位" }}</h3>
                  <span class="reminder-platform-pill" :data-platform="item.platform">
                    {{ platformLabel(item.platform) }}
                  </span>
                </div>
                <p v-if="item.company" class="reminder-item-company">{{ item.company }}</p>
                <p class="reminder-item-meta">
                  <span v-if="item.salary">{{ item.salary }}</span>
                  <span v-if="item.location">{{ item.location }}</span>
                  <span class="reminder-item-days" data-testid="reminder-days">
                    已 {{ item.elapsed_days }} 天未活动
                  </span>
                </p>

                <div class="reminder-item-actions">
                  <a
                    v-if="openUrl(item)"
                    class="button secondary"
                    :href="openUrl(item)"
                    target="_blank"
                    rel="noopener noreferrer"
                    data-testid="btn-open-job"
                  >查看岗位</a>
                  <button
                    v-else
                    class="button secondary"
                    type="button"
                    disabled
                    data-testid="btn-open-job-disabled"
                  >查看岗位</button>
                  <button
                    class="button secondary"
                    type="button"
                    data-testid="btn-follow-up"
                    :disabled="uiFor(item.job_id).busyAction !== ''"
                    @click="runAction(item, 'follow_up')"
                  >{{ uiFor(item.job_id).busyAction === "follow_up" ? "记录中…" : "记录跟进" }}</button>
                  <button
                    class="button secondary"
                    type="button"
                    data-testid="btn-mark-stale"
                    :disabled="uiFor(item.job_id).busyAction !== ''"
                    @click="runAction(item, 'mark_stale')"
                  >{{ uiFor(item.job_id).busyAction === "mark_stale" ? "标记中…" : "标记已荒废" }}</button>
                  <button
                    class="button ghost"
                    type="button"
                    data-testid="btn-advice"
                    :disabled="uiFor(item.job_id).adviceStatus === 'loading'"
                    @click="runAdvice(item)"
                  >{{ uiFor(item.job_id).adviceStatus === "loading" ? "生成建议中…" : "请求建议" }}</button>
                </div>

                <p v-if="!item.can_open" class="reminder-link-note">
                  来源链接不可用，无法直接跳转；仍可记录跟进或标记荒废。
                </p>
                <p v-else-if="!openUrl(item)" class="reminder-link-note">
                  来源链接未通过安全校验，已禁用跳转。
                </p>

                <p
                  v-if="uiFor(item.job_id).actionError"
                  class="reminder-item-error"
                  role="alert"
                  data-testid="item-action-error"
                >{{ uiFor(item.job_id).actionError }}</p>

                <div v-if="uiFor(item.job_id).adviceStatus !== 'idle'" class="reminder-advice">
                  <p v-if="uiFor(item.job_id).adviceStatus === 'loading'" role="status" data-testid="advice-loading">
                    正在生成建议…
                  </p>
                  <template v-else-if="uiFor(item.job_id).adviceStatus === 'done' && uiFor(item.job_id).advice">
                    <p class="reminder-advice-head" data-testid="advice-result">
                      <strong>{{ adviceActionLabel(uiFor(item.job_id).advice!.action) }}</strong>
                      <span class="reminder-advice-source" data-testid="advice-source">
                        {{ uiFor(item.job_id).advice!.source === "ai" ? "AI" : "规则" }}
                      </span>
                    </p>
                    <p class="reminder-advice-reason">{{ uiFor(item.job_id).advice!.reason }}</p>
                  </template>
                  <p
                    v-else-if="uiFor(item.job_id).adviceStatus === 'error'"
                    class="reminder-item-error"
                    role="alert"
                    data-testid="advice-error"
                  >{{ uiFor(item.job_id).adviceError }}</p>
                </div>
              </li>
            </ul>
            <p v-if="total > items.length" class="reminder-limit-note" data-testid="reminder-limit-note">
              已显示前 {{ items.length }} 条（单次最多 {{ JOB_REMINDER_LIST_LIMIT_MAX }} 条，共 {{ total }} 条逾期）。
            </p>
          </template>
        </template>
      </div>
    </aside>
  </div>
</template>

<style scoped>
/* 与收藏面板一致的圆润浮窗：右上悬浮、透明遮罩保留点击外部关闭。 */
.reminder-drawer-backdrop {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: transparent;
}

.reminder-drawer {
  position: fixed;
  top: 80px;
  right: 16px;
  bottom: 16px;
  z-index: 61;
  display: flex;
  flex-direction: column;
  width: min(380px, calc(100vw - 32px));
  overflow-x: hidden;
  border: 1px solid var(--hair);
  border-radius: 13px;
  background: var(--panel);
  box-shadow: var(--shadow);
}

.reminder-drawer-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 16px 18px 12px;
  border-bottom: 1px solid var(--hair-2);
}

.reminder-drawer-heading h2 {
  margin: 0;
  font-size: 1.05rem;
  overflow-wrap: anywhere;
}

.reminder-total {
  margin: 2px 0 0;
  color: var(--text-soft, #9fb0c3);
  font-size: .85rem;
}

/* 唯一内部滚动区：列表在抽屉内部滚动，页面 body 不产生第二个滚动条。 */
.reminder-drawer-body {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 8px 16px 16px;
}

.reminder-loading,
.reminder-empty {
  padding: 32px 8px;
  color: var(--text-soft, #9fb0c3);
  text-align: center;
}

.reminder-error {
  padding: 12px;
  margin-bottom: 8px;
  border: 1px solid var(--reject-edge);
  border-radius: 8px;
  background: var(--reject-wash);
  overflow-wrap: anywhere;
}

.reminder-error p {
  margin: 0 0 8px;
}

.reminder-stale-note {
  color: var(--unsure-deep);
  font-size: .85rem;
}

.reminder-notice {
  margin: 0 0 8px;
  padding: 8px 10px;
  border: 1px solid var(--unsure-edge);
  background: var(--unsure-wash);
  border-radius: 8px;
  font-size: .85rem;
  overflow-wrap: anywhere;
}

.reminder-list {
  flex: 1;
  min-height: 0;
  margin: 0;
  padding: 0 4px 0 0;
  list-style: none;
  overflow-y: auto;
  overflow-x: hidden;
}

.reminder-item {
  min-width: 0;
  padding: 12px 0;
  border-bottom: 1px solid var(--hair-2);
  overflow-wrap: anywhere;
}

.reminder-item-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.reminder-item-title {
  margin: 0;
  min-width: 0;
  font-size: .98rem;
  overflow-wrap: anywhere;
}

.reminder-platform-pill {
  flex: none;
  padding: 2px 8px;
  border: 1px solid var(--brand-edge);
  border-radius: 999px;
  font-size: .72rem;
  color: var(--brand-ink);
  background: var(--brand-wash);
  white-space: nowrap;
}

.reminder-item-company {
  margin: 4px 0 0;
  color: var(--text-soft, #9fb0c3);
  font-size: .85rem;
}

.reminder-item-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 4px 0 8px;
  color: var(--text-soft, #9fb0c3);
  font-size: .85rem;
}

.reminder-item-days {
  color: var(--unsure-deep);
}

/* 操作可换行但不逐字竖排；全部按钮保持 ≥44px 点击区。 */
.reminder-item-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.reminder-item-actions :deep(.button),
.reminder-item-actions .button {
  min-height: 44px;
  min-width: 44px;
  padding: 8px 12px;
  font-size: .85rem;
  white-space: normal;
  overflow-wrap: anywhere;
}

.reminder-link-note {
  margin: 8px 0 0;
  color: var(--text-soft, #9fb0c3);
  font-size: .8rem;
}

.reminder-item-error {
  margin: 8px 0 0;
  color: var(--reject);
  font-size: .85rem;
  overflow-wrap: anywhere;
}

.reminder-advice {
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid var(--hair);
  border-radius: 8px;
  background: var(--panel-2);
  font-size: .85rem;
  overflow-wrap: anywhere;
}

.reminder-advice-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
}

.reminder-advice-source {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--hair);
  font-size: .72rem;
  color: var(--text-soft);
}

.reminder-advice-reason {
  margin: 6px 0 0;
  color: var(--text-soft, #9fb0c3);
}

.reminder-limit-note {
  margin: 8px 0 0;
  color: var(--text-soft, #9fb0c3);
  font-size: .8rem;
}

/* 窄屏（390×844 验收）：抽屉全屏，关闭按钮固定可达，无横向溢出。 */
@media (max-width: 720px) {
  .reminder-drawer {
    width: calc(100vw - 32px);
  }

  .reminder-item-actions .button {
    flex: 1 1 auto;
  }
}
</style>
