<script setup lang="ts">
// ---------------------------------------------------------------------------
// 岗位详情生命周期控件（Task 007，T055-T062）
//
// 冻结合同来源：specs/002-job-feedback-reminders/contracts/http-api.md
// （job-feedback-v1）、contracts/ui-interaction.md `JobLifecycleActions` 节。
//
// 边界（禁止行为）：
// - 详情初始化只 GET /api/profile-jobs/state，绝不自动 mark_read（无副作用读取）。
// - 不做乐观更新：成功只采用响应 state；低 revision 陈旧响应不覆盖较新 state。
// - 身份不完整时阻断写操作，不用 UI 当前平台补齐身份。
// - 只使用后端已入库 canonical_url 并复验平台 host/path；不用裸 ID 拼 URL。
// - 不确定网络重试复用同一 request_id；用户再次确认生成新 request_id。
// ---------------------------------------------------------------------------
import { computed, nextTick, ref, watch } from "vue";
import type { JobItem } from "../types";
import {
  JOB_LIFECYCLE_STATUS_LABELS,
  JobFeedbackClientError,
  JobFeedbackRequestContext,
  buildJobIdentity,
  getJobLifecycleEvents,
  getJobLifecycleState,
  mapJobFeedbackError,
  mergeJobLifecycleState,
  submitJobFeedbackAction,
} from "../jobFeedback";
import type {
  JobFeedbackAction,
  JobFeedbackEvent,
  JobIdentity,
  JobLifecycleStateSnapshot,
  JobLifecycleStatus,
} from "../jobFeedback";

const props = defineProps<{
  profileId: string;
  job: JobItem;
}>();

const emit = defineEmits<{
  "job-feedback-changed": [payload: { profileId: string; jobId: string }];
}>();

// ---------------------------------------------------------------------------
// 身份：内部 job_id 或完整权威三元组；缺一阻断，不从 UI platform 猜身份
// ---------------------------------------------------------------------------

/** 岗位稳定键：详情岗位变化时重新读取 state。 */
const jobKey = computed(() => {
  const job = props.job;
  if (job.platform && job.platform_job_id) return `${job.platform}:${job.platform_job_id}`;
  return String(job.job_id || job.id || job.canonical_url || job.title || "unknown");
});

/** 成功响应后缓存的服务端内部 job_id（后续写入与 events 读取优先使用）。 */
const cachedJobId = ref("");

const identity = computed<JobIdentity | null>(() => {
  try {
    return buildJobIdentity({
      job_id: cachedJobId.value || props.job.job_id || null,
      platform: props.job.platform ?? null,
      platform_job_id: props.job.platform_job_id ?? null,
      canonical_url: props.job.canonical_url ?? null,
      title: props.job.title ?? null,
      company: props.job.company || props.job.boss_name || null,
      salary: props.job.salary ?? null,
      location: props.job.location ?? null,
      experience: props.job.experience ?? null,
      degree: props.job.degree ?? null,
      jd: props.job.jd ?? null,
      extra: props.job.extra ?? null,
    });
  } catch {
    return null;
  }
});

const identityIncomplete = computed(() => identity.value === null);

// ---------------------------------------------------------------------------
// 只读初始加载：详情岗位/画像变化时 GET state；只查看不得标记已读
// ---------------------------------------------------------------------------

const loading = ref(false);
const loadError = ref("");
/** 岗位尚未入库（读取 404）：展示“暂无轨迹”中性文案，不用红色警示误导用户。 */
const notFound = ref(false);
const lifecycle = ref<JobLifecycleStateSnapshot | null>(null);
const exists = ref(false);
const busy = ref(false);
const actionError = ref("");

let readSequence = 0;

async function loadState() {
  const sequence = ++readSequence;
  const ident = identity.value;
  loadError.value = "";
  notFound.value = false;
  actionError.value = "";
  if (!props.profileId || !ident) return;
  loading.value = true;
  try {
    // 只读端点：读取请求不会创建岗位，也不产生任何副作用。
    const response = await getJobLifecycleState(props.profileId, ident);
    if (sequence !== readSequence) return;
    exists.value = response.exists;
    if (response.state) {
      lifecycle.value = mergeJobLifecycleState(lifecycle.value, response.state);
      cachedJobId.value = response.state.job_id;
    } else if (response.exists && typeof response.job_id === "string" && response.job_id) {
      cachedJobId.value = response.job_id;
    }
    // 浮窗本身就是轨迹视图：已入库岗位直接加载事件轨迹，不再需要手动展开。
    if (cachedJobId.value) void loadEventsPage(0);
  } catch (error) {
    if (sequence !== readSequence) return;
    const info = mapJobFeedbackError(error);
    if (info.status === 404 || info.errorCode === "not_found" || info.errorCode === "job_not_found") {
      // 还没记录过任何操作：不报警，展示“暂无轨迹”。
      notFound.value = true;
    } else {
      loadError.value = info.userMessage;
    }
  } finally {
    if (sequence === readSequence) loading.value = false;
  }
}

function resetOnJobChange() {
  // 世代号递增：在途写/事件响应的晚到结果不得覆盖切换后的新岗位/新 profile
  // （ui-interaction.md 当前画像切换：旧响应丢弃）。
  generation += 1;
  lifecycle.value = null;
  exists.value = false;
  notFound.value = false;
  cachedJobId.value = "";
  correctionOpen.value = false;
  events.value = [];
  eventsExhausted.value = false;
  nextAfterSequence.value = 0;
  void loadState();
}

let generation = 0;

// ---------------------------------------------------------------------------
// 唯一当前状态 + 按状态选择的主命令
// ---------------------------------------------------------------------------

const statusLabel = computed(() => {
  if (identityIncomplete.value) return "身份未确认";
  if (loading.value) return "读取中…";
  if (lifecycle.value) return JOB_LIFECYCLE_STATUS_LABELS[lifecycle.value.status];
  if (notFound.value) return "暂无轨迹";
  if (loadError.value) return "未知";
  return "未记录";
});

interface PrimaryCommand {
  action: JobFeedbackAction;
  label: string;
}

const primaryCommands = computed<PrimaryCommand[]>(() => {
  if (identityIncomplete.value || loading.value) return [];
  const commands: PrimaryCommand[] = [];
  const status = lifecycle.value?.status ?? null;
  if (!lifecycle.value) {
    // 尚未入库的 pipeline 岗位：写命令携带权威三元组，成功后缓存内部 ID。
    commands.push({ action: "mark_read", label: "标记已读" });
    commands.push({ action: "mark_applied", label: "记录已投递" });
    return commands;
  }
  if (status === "applied") {
    commands.push({ action: "follow_up", label: "记录跟进" });
    commands.push({ action: "mark_stale", label: "标记已荒废" });
  } else if (status === "stale") {
    commands.push({ action: "restore_applied", label: "恢复已投递" });
  } else {
    if (status !== "read") commands.push({ action: "mark_read", label: "标记已读" });
    commands.push({ action: "mark_applied", label: "记录已投递" });
  }
  return commands;
});

/** 已投递但缺失投递时间：显示补录入口，提醒资格不可计算。 */
const missingAppliedAt = computed(() =>
  Boolean(lifecycle.value && lifecycle.value.status === "applied" && !lifecycle.value.applied_at));

function formatDateTime(iso: string | null): string {
  if (!iso) return "";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleString();
}

// ---------------------------------------------------------------------------
// 写命令：写锁 + request ID 重试复用 + 无乐观更新 + revision 防倒退
// ---------------------------------------------------------------------------

async function perform(
  action: JobFeedbackAction,
  options: { appliedAt?: string | null; targetStatus?: JobLifecycleStatus | null } = {},
) {
  if (busy.value || identityIncomplete.value || !props.profileId) return;
  const ident = identity.value;
  if (!ident) return;
  busy.value = true;
  actionError.value = "";
  // 确认时刻捕获 profile/generation：响应晚到时判断是否已切换，切换则丢弃。
  const profileId = props.profileId;
  const startGeneration = generation;
  // 每次用户确认生成新的 request_id；不确定重试在上下文内复用同一 ID。
  const context = new JobFeedbackRequestContext();
  let retried = false;
  for (;;) {
    try {
      const response = await submitJobFeedbackAction({
        requestId: context.requestId,
        profileId,
        job: ident,
        action,
        appliedAt: options.appliedAt ?? null,
        targetStatus: options.targetStatus ?? null,
      });
      // 岗位/画像已切换：丢弃旧 action 的刷新结果，不覆盖新 state、不发事件。
      if (startGeneration !== generation) break;
      // 成功只采用响应 state；响应缺 state 视为合同违规，保留原状态。
      if (!response.state || typeof response.state.job_id !== "string") {
        actionError.value = "服务端响应缺少生命周期快照，已保留当前状态";
        break;
      }
      cachedJobId.value = response.state.job_id;
      exists.value = true;
      lifecycle.value = mergeJobLifecycleState(lifecycle.value, response.state);
      emit("job-feedback-changed", { profileId, jobId: response.state.job_id });
      void loadEventsPage(0);
      break;
    } catch (error) {
      if (error instanceof JobFeedbackClientError) {
        // 客户端合同违规（字段组合等）：不重试，直接展示。
        actionError.value = error.message;
        break;
      }
      const info = mapJobFeedbackError(error);
      // 岗位/画像已切换：静默丢弃旧 action 的结果，不向新岗位展示旧错误。
      if (startGeneration !== generation) break;
      // 不确定失败（网络层 / 服务端持久化失败零副作用）：复用同一 request_id 重试一次。
      const uncertain = info.status === 0 || info.status >= 500;
      if (uncertain && !retried) {
        retried = true;
        context.retry();
        continue;
      }
      // 确定性失败：保留原状态/时间，只显示 API 文案。
      actionError.value = info.userMessage;
      break;
    }
  }
  busy.value = false;
}

// ---------------------------------------------------------------------------
// 纠正表单：目标状态 + 带时区投递时间；未来时间客户端预警
// ---------------------------------------------------------------------------

const correctionOpen = ref(false);
const correctionTarget = ref<JobLifecycleStatus>("read");
const correctionTime = ref("");
const correctionFormError = ref("");
const correctionStatusEl = ref<HTMLSelectElement | null>(null);
const correctionTimeEl = ref<HTMLInputElement | null>(null);

const STATUS_OPTIONS = (Object.keys(JOB_LIFECYCLE_STATUS_LABELS) as JobLifecycleStatus[])
  .map((value) => ({ value, label: JOB_LIFECYCLE_STATUS_LABELS[value] }));

function openCorrection(focus: "status" | "time" = "status") {
  correctionOpen.value = true;
  correctionFormError.value = "";
  correctionTarget.value = lifecycle.value?.status ?? "read";
  correctionTime.value = "";
  nextTick(() => {
    if (focus === "time") correctionTimeEl.value?.focus();
    else correctionStatusEl.value?.focus();
  });
}

function closeCorrection() {
  correctionOpen.value = false;
  correctionFormError.value = "";
}

function parseLocalDateTime(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value.trim());
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match;
  const parsed = new Date(
    Number(year), Number(month) - 1, Number(day),
    Number(hour), Number(minute), Number(second || 0),
  );
  if (Number.isNaN(parsed.getTime())) return null;
  // 拒绝 2026-02-31 这类被 Date 归一化的无效日期。
  if (
    parsed.getFullYear() !== Number(year)
    || parsed.getMonth() !== Number(month) - 1
    || parsed.getDate() !== Number(day)
  ) return null;
  return parsed;
}

/** datetime-local 没有时区信息：按本地时区补 offset，产出 RFC 3339 带时区时刻。 */
function formatRfc3339WithOffset(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMinutes);
  const zone = `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    + `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${zone}`;
}

function submitCorrection() {
  correctionFormError.value = "";
  const target = correctionTarget.value;
  // 投递时间只对目标 applied 有意义；其它目标携带 applied_at 会被服务端
  // 拒绝为 invalid_action_payload，因此非 applied 目标不解析也不发送时间。
  let appliedAt: string | null = null;
  if (target === "applied") {
    const rawTime = correctionTime.value.trim();
    if (rawTime) {
      const parsed = parseLocalDateTime(rawTime);
      if (!parsed) {
        correctionFormError.value = "投递时间无法解析，请重新选择日期和时间";
        return;
      }
      if (parsed.getTime() > Date.now()) {
        // 客户端预警；服务端仍是权威验证方。
        correctionFormError.value = "投递时间不能晚于当前时刻";
        return;
      }
      appliedAt = formatRfc3339WithOffset(parsed);
    }
    const hadAppliedAt = Boolean(lifecycle.value?.applied_at);
    if (!hadAppliedAt && !appliedAt) {
      correctionFormError.value = "目标状态为已投递且缺少历史投递时间，必须填写投递时间";
      return;
    }
  }
  correctionOpen.value = false;
  const currentStatus = lifecycle.value?.status ?? null;
  if (appliedAt && currentStatus === "applied") {
    // 目标状态未变、只纠正时间：走 correct_applied_at。
    void perform("correct_applied_at", { appliedAt });
    return;
  }
  void perform("correct_status", { targetStatus: target, appliedAt });
}

// ---------------------------------------------------------------------------
// 事件轨迹：按需展开 + sequence 分页；只含真实变化，不加载偏好事件
// ---------------------------------------------------------------------------

const EVENTS_PAGE_LIMIT = 20;
const eventsBusy = ref(false);
const eventsError = ref("");
const events = ref<JobFeedbackEvent[]>([]);
const eventsExhausted = ref(false);
const nextAfterSequence = ref(0);

const EVENT_ACTION_LABELS: Record<string, string> = {
  mark_read: "标记已读",
  mark_applied: "记录已投递",
  correct_applied_at: "纠正投递时间",
  follow_up: "记录跟进",
  mark_stale: "标记已荒废",
  restore_applied: "恢复已投递",
  correct_status: "纠正状态",
};

function eventActionLabel(event: JobFeedbackEvent): string {
  return EVENT_ACTION_LABELS[event.action] || event.action;
}

async function loadEventsPage(afterSequence: number) {
  const jobId = cachedJobId.value;
  if (!props.profileId || !jobId) {
    eventsError.value = "岗位尚未可靠入库，无法加载轨迹";
    return;
  }
  const profileId = props.profileId;
  const startGeneration = generation;
  eventsBusy.value = true;
  eventsError.value = "";
  try {
    const response = await getJobLifecycleEvents(profileId, jobId, {
      afterSequence,
      limit: EVENTS_PAGE_LIMIT,
    });
    // 岗位/画像已切换：旧岗位的轨迹不得写入新岗位的显示。
    if (startGeneration !== generation) return;
    events.value = afterSequence === 0
      ? [...response.events]
      : [...events.value, ...response.events];
    nextAfterSequence.value = response.next_after_sequence;
    eventsExhausted.value = response.events.length < EVENTS_PAGE_LIMIT;
  } catch (error) {
    if (startGeneration !== generation) return;
    eventsError.value = mapJobFeedbackError(error).userMessage;
  } finally {
    eventsBusy.value = false;
  }
}

function loadMoreEvents() {
  void loadEventsPage(nextAfterSequence.value);
}

// 详情岗位/画像变化时重置并重新只读加载。
// 放在全部 ref 声明之后，避免 immediate watch 触发 TDZ。
watch([() => props.profileId, jobKey], resetOnJobChange, { immediate: true });
</script>

<template>
  <section
    class="job-lifecycle-actions"
    aria-label="岗位生命周期"
    data-testid="job-lifecycle-actions"
  >
    <div
      v-if="identityIncomplete"
      class="lca-blocked"
      role="status"
      data-testid="lca-identity-blocked"
    >
      岗位身份未可靠入库，生命周期操作暂不可用
    </div>

    <template v-else>
      <div v-if="lifecycle || loading" class="lca-header">
        <span
          class="lca-status"
          :data-status="lifecycle?.status ?? (loading ? 'loading' : 'none')"
          data-testid="lca-current-status"
        >{{ statusLabel }}</span>
        <span v-if="loading" class="lca-loading" role="status" data-testid="lca-loading">读取状态中…</span>
        <span
          v-else-if="lifecycle?.reminder?.eligible"
          class="lca-reminder"
          data-testid="lca-reminder-eligible"
        >已 {{ lifecycle.reminder.elapsed_days }} 天未活动</span>
      </div>

      <div v-if="lifecycle" class="lca-meta" data-testid="lca-meta">
        <span v-if="lifecycle.applied_at">投递于 {{ formatDateTime(lifecycle.applied_at) }}</span>
        <span v-if="lifecycle.last_follow_up_at">最近跟进 {{ formatDateTime(lifecycle.last_follow_up_at) }}</span>
      </div>

      <p
        v-if="missingAppliedAt"
        class="lca-missing-time"
        role="status"
        data-testid="lca-missing-applied-at"
      >
        缺少投递时间，提醒资格无法计算。
        <button
          class="lca-inline-link"
          type="button"
          data-testid="lca-fill-applied-at"
          :disabled="busy"
          @click="openCorrection('time')"
        >补录投递时间</button>
      </p>

      <p v-if="loadError" class="lca-error" role="alert" data-testid="lca-load-error">
        {{ loadError }}
        <button class="lca-inline-link" type="button" data-testid="lca-load-retry" @click="loadState">重试</button>
      </p>

      <p v-if="notFound" class="lca-empty" role="status" data-testid="lca-empty-track">
        暂无轨迹。还没有记录过任何操作，可用下方按钮开始记录。
      </p>

      <div class="lca-commands" data-testid="lca-commands">
        <button
          v-for="command in primaryCommands"
          :key="command.action"
          class="button secondary lca-btn"
          type="button"
          :data-testid="`lca-action-${command.action}`"
          :disabled="busy || loading"
          @click="perform(command.action)"
        >{{ command.label }}</button>
        <button
          class="button ghost lca-btn"
          type="button"
          data-testid="lca-open-correction"
          :disabled="busy || loading"
          @click="openCorrection('status')"
        >纠正</button>
      </div>

      <p v-if="actionError" class="lca-error" role="alert" data-testid="lca-action-error">{{ actionError }}</p>

      <form
        v-if="correctionOpen"
        class="lca-correction"
        data-testid="lca-correction-form"
        @submit.prevent="submitCorrection"
      >
        <label class="lca-field">
          <span>目标状态</span>
          <select ref="correctionStatusEl" v-model="correctionTarget" data-testid="lca-correction-status">
            <option v-for="option in STATUS_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label v-if="correctionTarget === 'applied'" class="lca-field">
          <span>投递时间（按本机时区提交）</span>
          <input
            ref="correctionTimeEl"
            v-model="correctionTime"
            type="datetime-local"
            data-testid="lca-correction-time"
          >
        </label>
        <p v-if="correctionFormError" class="lca-error" role="alert" data-testid="lca-correction-error">
          {{ correctionFormError }}
        </p>
        <div class="lca-correction-actions">
          <button class="button primary lca-btn" type="submit" data-testid="lca-correction-submit" :disabled="busy">
            保存纠正
          </button>
          <button class="button ghost lca-btn" type="button" data-testid="lca-correction-cancel" @click="closeCorrection">
            取消
          </button>
        </div>
      </form>

      <div v-if="cachedJobId || !notFound" class="lca-events" data-testid="lca-events">
        <p v-if="!cachedJobId && !notFound" class="lca-events-empty" role="status">还没有任何操作记录，可用上方按钮开始记录。</p>
        <p v-else-if="eventsBusy && !events.length" class="lca-events-empty" role="status" data-testid="lca-events-loading">
          轨迹加载中…
        </p>
        <p v-else-if="!events.length && !eventsError" class="lca-events-empty" role="status" data-testid="lca-events-empty">
          暂无生命周期变化记录。
        </p>
        <p v-if="eventsError" class="lca-error" role="alert" data-testid="lca-events-error">{{ eventsError }}</p>
        <ol v-if="events.length" class="lca-event-list" data-testid="lca-event-list">
          <li v-for="event in events" :key="event.sequence" :data-sequence="event.sequence">
            <strong>#{{ event.sequence }} {{ eventActionLabel(event) }}</strong>
            <span v-if="event.from_status || event.to_status">
              {{ event.from_status ? JOB_LIFECYCLE_STATUS_LABELS[event.from_status] : "—" }}
              → {{ event.to_status ? JOB_LIFECYCLE_STATUS_LABELS[event.to_status] : "—" }}
            </span>
            <span>{{ formatDateTime(event.occurred_at) }}</span>
          </li>
        </ol>
        <button
          v-if="events.length && !eventsExhausted"
          class="button secondary lca-btn"
          type="button"
          data-testid="lca-load-more-events"
          :disabled="eventsBusy"
          @click="loadMoreEvents"
        >{{ eventsBusy ? "加载中…" : "加载更多" }}</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* 生命周期状态块：独立卡片容器，与详情其它区块形成清晰层级，
   避免状态标签/错误/按钮裸堆在一起像未上样式。 */
.job-lifecycle-actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 100%;
  min-width: 0;
  margin-top: 4px;
  padding: 14px 16px;
  border: 1px solid var(--hair);
  border-radius: 12px;
  background: var(--panel-3);
}

.lca-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

/* 当前状态胶囊：中性底为默认，按 data-status 上语义色 */
.lca-status {
  font-weight: 700;
  font-size: 0.85rem;
  padding: 3px 12px;
  border-radius: 999px;
  background: var(--hair-2);
  color: var(--ink-2);
}
.lca-status[data-status="applied"] {
  background: var(--match-wash);
  color: var(--match-deep);
}
.lca-status[data-status="read"],
.lca-status[data-status="interested"],
.lca-status[data-status="new"] {
  background: var(--brand-wash);
  color: var(--brand-ink);
}
.lca-status[data-status="stale"],
.lca-status[data-status="deleted"] {
  background: var(--reject-wash);
  color: var(--reject-deep);
}

.lca-loading,
.lca-reminder,
.lca-url-disabled {
  font-size: 0.9rem;
  color: var(--ink-3);
}

.lca-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 0.9rem;
  color: var(--ink-3);
}

.lca-commands,
.lca-correction-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-width: 100%;
}

.lca-btn {
  min-height: 36px;
  overflow-wrap: anywhere;
}
/* 卡片内的 ghost 按钮补上描边，避免退化成裸文字 */
.lca-commands .button.ghost,
.lca-correction-actions .button.ghost {
  border-color: var(--hair);
  background: var(--panel);
}

/* 错误与提醒统一为警示条：底色 + 描边 + 圆角，不再是一行裸文字 */
.lca-error,
.lca-missing-time {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border: 1px solid var(--reject-edge);
  border-radius: 8px;
  background: var(--reject-wash);
  color: var(--reject-deep);
  font-size: 0.9rem;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.lca-missing-time {
  border-color: color-mix(in srgb, var(--unsure) 45%, transparent);
  background: var(--unsure-wash);
  color: var(--unsure);
}

/* 暂无轨迹：中性提示条，不使用错误红，避免误导用户以为出了故障。 */
.lca-empty {
  margin: 0;
  padding: 8px 12px;
  border: 1px solid var(--hair-2);
  border-radius: 8px;
  background: var(--panel-3);
  color: var(--ink-2);
  font-size: .85rem;
}

.lca-blocked {
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--reject-wash);
  color: var(--reject-deep);
  word-break: break-word;
}

.lca-inline-link {
  background: none;
  border: none;
  padding: 0;
  color: inherit;
  text-decoration: underline;
  cursor: pointer;
  font: inherit;
}

.lca-correction {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 100%;
  padding: 12px;
  border: 1px solid var(--hair);
  border-radius: 10px;
}

.lca-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 100%;
}

.lca-field select,
.lca-field input {
  max-width: 100%;
}

.lca-events {
  max-width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lca-event-list {
  margin: 4px 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-wrap: anywhere;
}

.lca-event-list li {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* 窄屏：动作至少 44px 可点击区域，按钮换行但不横向溢出 */
@media (max-width: 640px) {
  .lca-btn {
    min-height: 44px;
    flex: 1 1 auto;
  }

  .lca-correction {
    padding: 10px;
  }
}
</style>
