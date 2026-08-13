// ---------------------------------------------------------------------------
// 岗位反馈闭环与投递过期提醒：前端合同 client（Task 004）
//
// 冻结合同来源：specs/002-job-feedback-reminders/contracts/http-api.md
// （job-feedback-v1）、contracts/ui-interaction.md、data-model.md。
//
// 边界（禁止行为）：
// - 不向 reminder count/list 传 platform；platform 只用于身份/URL 安全校验。
// - 不在客户端计算提醒资格；eligible/elapsed 全部来自服务端投影。
// - 不做乐观状态更新：成功只采用响应 state，且低 revision 不覆盖较新 state。
// - 不确定网络重试复用同一 request_id；用户明确再次确认生成新 request_id。
// ---------------------------------------------------------------------------
import { apiRequest } from "./api";
import type { Platform } from "./types";

// ---------------------------------------------------------------------------
// 生命周期类型
// ---------------------------------------------------------------------------

/** 生命周期状态集合（data-model.md 不变式 2）。 */
export type JobLifecycleStatus =
  | "new"
  | "interested"
  | "read"
  | "applied"
  | "stale"
  | "deleted";

/** 状态展示标签（ui-interaction.md：显示唯一当前状态）。 */
export const JOB_LIFECYCLE_STATUS_LABELS: Record<JobLifecycleStatus, string> = {
  new: "新岗位",
  interested: "感兴趣",
  read: "已读",
  applied: "已投递",
  stale: "已荒废",
  deleted: "不感兴趣",
};

/** 生命周期命令 allowlist（http-api.md POST /api/profile-jobs/actions）。 */
export const JOB_FEEDBACK_ACTIONS = [
  "mark_read",
  "mark_applied",
  "correct_applied_at",
  "follow_up",
  "mark_stale",
  "restore_applied",
  "correct_status",
] as const;
export type JobFeedbackAction = typeof JOB_FEEDBACK_ACTIONS[number];

/** AI 建议只允许两类行动方向（FR-022）。 */
export type JobAdviceAction = "follow_up" | "review";
export type JobAdviceSource = "ai" | "rule";

/** 合同冻结的稳定错误码（http-api.md 业务错误表 + 提醒/列表端点）。 */
export type JobFeedbackErrorCode =
  | "invalid_action"
  | "invalid_action_payload"
  | "profile_not_found"
  | "job_not_found"
  | "idempotency_conflict"
  | "job_identity_conflict"
  | "state_precondition_failed"
  | "job_identity_incomplete"
  | "platform_url_mismatch"
  | "applied_at_required"
  | "applied_at_invalid"
  | "applied_at_in_future"
  | "follow_up_before_application"
  | "persistence_failed"
  | "not_found"
  | "invalid_limit"
  | "reminder_not_eligible"
  | "unknown_error";

/** 服务端响应投影：提醒资格是响应时投影，不是持久状态。 */
export interface JobReminderProjection {
  eligible: boolean;
  baseline_at: string;
  elapsed_seconds: number;
  elapsed_days: number;
}

/** 生命周期快照（http-api.md 生命周期快照）。 */
export interface JobLifecycleStateSnapshot {
  profile_id: string;
  job_id: string;
  status: JobLifecycleStatus;
  applied_at: string | null;
  last_follow_up_at: string | null;
  revision: number;
  reminder: JobReminderProjection;
}

export interface JobLifecycleStateQueryResponse {
  ok: true;
  exists: boolean;
  state?: JobLifecycleStateSnapshot;
  job_id?: string | null;
}

export interface JobFeedbackActionResponse {
  ok: true;
  replayed: boolean;
  changed: boolean;
  event_id: string | null;
  event_sequence: number | null;
  state: JobLifecycleStateSnapshot;
}

/** 生命周期事件（只包含真实变化；不返回 request ID/fingerprint/receipt）。 */
export interface JobFeedbackEvent {
  sequence: number;
  id: string;
  action: JobFeedbackAction;
  from_status: JobLifecycleStatus | null;
  to_status: JobLifecycleStatus | null;
  from_applied_at: string | null;
  to_applied_at: string | null;
  from_last_follow_up_at: string | null;
  to_last_follow_up_at: string | null;
  occurred_at: string;
}

export interface JobLifecycleEventsResponse {
  ok: true;
  events: JobFeedbackEvent[];
  next_after_sequence: number;
}

export interface JobReminderCountResponse {
  ok: true;
  profile_id: string;
  threshold_hours: number;
  total: number;
}

/** 提醒列表单项安全投影；URL 无效时 can_open=false（跳转禁用）。 */
export interface JobReminderItem {
  job_id: string;
  platform: Platform;
  platform_job_id: string;
  title: string;
  company: string | null;
  salary: string | null;
  location: string | null;
  canonical_url: string | null;
  status: JobLifecycleStatus;
  applied_at: string;
  last_follow_up_at: string | null;
  baseline_at: string;
  elapsed_seconds: number;
  elapsed_days: number;
  can_open: boolean;
}

export interface JobReminderListResponse {
  ok: true;
  profile_id: string;
  threshold_hours: number;
  /** 完整逾期数量，不受列表 100 条上限影响（FR-016）。 */
  total: number;
  items: JobReminderItem[];
}

export interface JobAdvice {
  action: JobAdviceAction;
  reason: string;
  source: JobAdviceSource;
}

// ---------------------------------------------------------------------------
// 错误类与错误映射
// ---------------------------------------------------------------------------

/** 客户端合同违规（身份不完整、字段组合非法、越界建议值等）。 */
export class JobFeedbackClientError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "JobFeedbackClientError";
  }
}

/** 权威身份三元组不完整：阻断写操作，不得猜测身份（data-model.md）。 */
export class JobIdentityIncompleteError extends JobFeedbackClientError {
  constructor(message: string) {
    super(message);
    this.name = "JobIdentityIncompleteError";
  }
}

/** 稳定错误体投影：status + error_code + user_message。 */
export interface JobFeedbackErrorInfo {
  status: number;
  errorCode: JobFeedbackErrorCode | string;
  userMessage: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * 把任意失败映射为结构化错误信息。
 * 服务端稳定错误体优先使用 user_message；网络/未知错误标记 unknown_error，
 * 不伪造服务端错误码。
 */
export function mapJobFeedbackError(error: unknown): JobFeedbackErrorInfo {
  if (isRecord(error) && typeof (error as { status?: unknown }).status === "number") {
    const payload = isRecord((error as { payload?: unknown }).payload)
      ? (error as { payload: Record<string, unknown> }).payload
      : {};
    const errorCode = typeof payload.error_code === "string" ? payload.error_code : "unknown_error";
    const userMessage = typeof payload.user_message === "string" && payload.user_message
      ? payload.user_message
      : (error instanceof Error && error.message) || `请求失败（${(error as { status: number }).status}）`;
    return { status: (error as { status: number }).status, errorCode, userMessage };
  }
  const message = error instanceof Error && error.message ? error.message : "网络请求失败";
  return { status: 0, errorCode: "unknown_error", userMessage: message };
}

// ---------------------------------------------------------------------------
// 岗位身份：内部 ID 或完整权威三元组
// ---------------------------------------------------------------------------

export interface JobIdentityByInternalId {
  job_id: string;
}

export interface JobIdentityByTriple {
  platform: Platform;
  platform_job_id: string;
  canonical_url: string;
  title?: string;
  company?: string;
  salary?: string;
  location?: string;
  jd?: string;
  experience?: string;
  degree?: string;
  extra?: Record<string, unknown>;
}

/** 生命周期写入只接受这两种身份（data-model.md 岗位身份解析）。 */
export type JobIdentity = JobIdentityByInternalId | JobIdentityByTriple;

export interface JobIdentityInput {
  job_id?: string | null;
  platform?: Platform | string | null;
  platform_job_id?: string | null;
  canonical_url?: string | null;
  title?: string | null;
  company?: string | null;
  salary?: string | null;
  location?: string | null;
  jd?: string | null;
  experience?: string | null;
  degree?: string | null;
  extra?: Record<string, unknown> | null;
}

function nonEmpty(value: string | null | undefined): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

/**
 * 构造权威岗位身份。
 * - 有内部 job_id 时直接使用内部 ID（服务端会校验附带三元组一致性）。
 * - 否则要求完整三元组 platform + platform_job_id + canonical_url，缺一阻断。
 * 禁止使用裸 platform_job_id、只带 URL 猜 platform 或界面草稿 platform。
 */
export function buildJobIdentity(input: JobIdentityInput): JobIdentity {
  if (nonEmpty(input.job_id)) {
    return { job_id: input.job_id.trim() };
  }
  const platform = input.platform;
  if (platform !== "boss" && platform !== "zhilian") {
    throw new JobIdentityIncompleteError("岗位身份不完整：缺少受支持的来源平台");
  }
  if (!nonEmpty(input.platform_job_id)) {
    throw new JobIdentityIncompleteError("岗位身份不完整：缺少平台岗位身份");
  }
  if (!nonEmpty(input.canonical_url)) {
    throw new JobIdentityIncompleteError("岗位身份不完整：缺少规范链接");
  }
  const identity: JobIdentityByTriple = {
    platform,
    platform_job_id: input.platform_job_id.trim(),
    canonical_url: input.canonical_url.trim(),
  };
  if (nonEmpty(input.title)) identity.title = input.title.trim();
  if (nonEmpty(input.company)) identity.company = input.company.trim();
  if (nonEmpty(input.salary)) identity.salary = input.salary.trim();
  if (nonEmpty(input.location)) identity.location = input.location.trim();
  if (nonEmpty(input.jd)) identity.jd = input.jd;
  if (nonEmpty(input.experience)) identity.experience = input.experience.trim();
  if (nonEmpty(input.degree)) identity.degree = input.degree.trim();
  if (input.extra) identity.extra = input.extra;
  return identity;
}

// ---------------------------------------------------------------------------
// 跳转安全：canonical URL 按所属平台复验（纵深防护，ui-interaction.md）
// ---------------------------------------------------------------------------

/**
 * 按岗位所属平台复验 canonical URL；不合法返回 null。
 * 前端继续执行 host/path 校验作为纵深防护；不按 URL 猜 platform。
 */
export function safeCanonicalUrl(platform: Platform, rawUrl: string | null | undefined): string | null {
  const raw = typeof rawUrl === "string" ? rawUrl.trim() : "";
  if (!raw) return null;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return null;
  }
  if (parsed.username || parsed.password) return null;
  const port = parsed.port;
  if (port && port !== "443") return null;
  const host = parsed.hostname.toLowerCase();
  if (platform === "zhilian") {
    // 智联：host 恰为 zhaopin.com / www.zhaopin.com；path 为 jobdetail/<id>.htm；
    // http 升级 https；剥离 query 与 fragment。
    if (host !== "zhaopin.com" && host !== "www.zhaopin.com") return null;
    if (!/^\/jobdetail\/[^/]+\.htm$/.test(parsed.pathname)) return null;
    const clean = new URL(parsed.toString());
    clean.protocol = "https:";
    clean.search = "";
    clean.hash = "";
    return clean.toString();
  }
  // boss：HTTPS-only + zhipin 域名。
  const isBossHost = host === "zhipin.com" || host.endsWith(".zhipin.com");
  if (parsed.protocol !== "https:" || !isBossHost) return null;
  return parsed.toString();
}

// ---------------------------------------------------------------------------
// request ID 生命周期（ui-interaction.md 幂等与失败）
// ---------------------------------------------------------------------------

/** 生成一次用户确认对应的 UUID request_id。 */
export function generateJobFeedbackRequestId(): string {
  return crypto.randomUUID();
}

/**
 * 一次用户确认的请求上下文：
 * - 同一确认内的超时/网络不确定重试调用 retry()，复用同一 ID；
 * - 用户明确再次确认调用 newConfirmation()，生成新 ID。
 */
export class JobFeedbackRequestContext {
  private currentId: string;

  constructor() {
    this.currentId = generateJobFeedbackRequestId();
  }

  get requestId(): string {
    return this.currentId;
  }

  /** 不确定网络重试：复用同一 request_id。 */
  retry(): string {
    return this.currentId;
  }

  /** 用户明确再次确认：生成新的 request_id。 */
  newConfirmation(): string {
    this.currentId = generateJobFeedbackRequestId();
    return this.currentId;
  }
}

// ---------------------------------------------------------------------------
// revision 防倒退 merge helper
// ---------------------------------------------------------------------------

/**
 * 只接受 revision 不低于当前已知值的响应快照；低 revision 的陈旧响应
 * 不得覆盖较新 state（http-api.md 幂等重放节）。
 */
export function mergeJobLifecycleState(
  current: JobLifecycleStateSnapshot | null,
  incoming: JobLifecycleStateSnapshot,
): JobLifecycleStateSnapshot {
  if (!current) return incoming;
  if (incoming.revision < current.revision) return current;
  return incoming;
}

// ---------------------------------------------------------------------------
// action payload 构造（合同字段规则）
// ---------------------------------------------------------------------------

export interface JobFeedbackActionInput {
  requestId: string;
  profileId: string;
  job: JobIdentity;
  action: JobFeedbackAction;
  /** RFC 3339 带时区时刻；服务端仍是权威验证方。 */
  appliedAt?: string | null;
  targetStatus?: JobLifecycleStatus | null;
}

export interface JobFeedbackActionPayload {
  request_id: string;
  profile_id: string;
  job: JobIdentity;
  action: JobFeedbackAction;
  applied_at: string | null;
  target_status: JobLifecycleStatus | null;
}

/**
 * 按合同字段规则构造写请求体：
 * - correct_applied_at 必须带 applied_at；
 * - mark_applied 可带 applied_at（缺省时由服务器使用当前时刻）；
 * - correct_status 必须带 target_status；
 * - 其它 action 不接受不相关字段；allowlist 外 action 直接拒绝。
 * 客户端只阻断明显合同违规，最终语义验证仍以服务端为准。
 */
export function buildJobFeedbackActionPayload(
  input: JobFeedbackActionInput,
): JobFeedbackActionPayload {
  if (!JOB_FEEDBACK_ACTIONS.includes(input.action)) {
    throw new JobFeedbackClientError(`不支持的生命周期操作：${String(input.action)}`);
  }
  const appliedAt = input.appliedAt ?? null;
  const targetStatus = input.targetStatus ?? null;

  let sendAppliedAt: string | null = null;
  let sendTargetStatus: JobLifecycleStatus | null = null;

  switch (input.action) {
    case "mark_applied":
      sendAppliedAt = appliedAt;
      if (targetStatus !== null) {
        throw new JobFeedbackClientError("mark_applied 不接受 target_status");
      }
      break;
    case "correct_applied_at":
      if (appliedAt === null) {
        throw new JobFeedbackClientError("correct_applied_at 必须提供投递时间");
      }
      if (targetStatus !== null) {
        throw new JobFeedbackClientError("correct_applied_at 不接受 target_status");
      }
      sendAppliedAt = appliedAt;
      break;
    case "correct_status":
      if (targetStatus === null) {
        throw new JobFeedbackClientError("correct_status 必须提供目标状态");
      }
      sendTargetStatus = targetStatus;
      sendAppliedAt = appliedAt;
      break;
    default:
      // mark_read / follow_up / mark_stale / restore_applied：
      // 不接受任何额外字段（合同：其它 action 不接受不相关字段）。
      if (appliedAt !== null) {
        throw new JobFeedbackClientError(`${input.action} 不接受 applied_at`);
      }
      if (targetStatus !== null) {
        throw new JobFeedbackClientError(`${input.action} 不接受 target_status`);
      }
      break;
  }

  return {
    request_id: input.requestId,
    profile_id: input.profileId,
    job: input.job,
    action: input.action,
    applied_at: sendAppliedAt,
    target_status: sendTargetStatus,
  };
}

// ---------------------------------------------------------------------------
// API client（复用现有 apiRequest，不引入第二套网络封装）
// ---------------------------------------------------------------------------

/**
 * GET /api/profile-jobs/state：读取生命周期快照，无副作用。
 * 支持内部 job_id，或完整权威三元组（只用于解析已存在岗位，不会创建）。
 */
export function getJobLifecycleState(
  profileId: string,
  job: JobIdentity,
): Promise<JobLifecycleStateQueryResponse> {
  const params = new URLSearchParams();
  params.set("profile_id", profileId);
  if ("job_id" in job) {
    params.set("job_id", job.job_id);
  } else {
    params.set("platform", job.platform);
    params.set("platform_job_id", job.platform_job_id);
    params.set("canonical_url", job.canonical_url);
  }
  return apiRequest<JobLifecycleStateQueryResponse>(
    `/api/profile-jobs/state?${params.toString()}`,
  );
}

/**
 * POST /api/profile-jobs/actions：提交生命周期命令。
 * request_id 由调用方按确认/重试语义提供（见 JobFeedbackRequestContext）。
 * 成功只采用响应 state；调用方不得做乐观更新。
 */
export function submitJobFeedbackAction(
  input: JobFeedbackActionInput,
): Promise<JobFeedbackActionResponse> {
  const payload = buildJobFeedbackActionPayload(input);
  return apiRequest<JobFeedbackActionResponse>("/api/profile-jobs/actions", {
    method: "POST",
    json: payload,
  });
}

/** GET /api/profile-jobs/{profile_id}/{job_id}/events：按需加载客观轨迹。 */
export function getJobLifecycleEvents(
  profileId: string,
  jobId: string,
  options: { afterSequence?: number; limit?: number } = {},
): Promise<JobLifecycleEventsResponse> {
  const afterSequence = options.afterSequence ?? 0;
  const limit = options.limit ?? 100;
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
    throw new RangeError("events limit 必须在 1-200 之间");
  }
  const params = new URLSearchParams();
  params.set("after_sequence", String(afterSequence));
  params.set("limit", String(limit));
  return apiRequest<JobLifecycleEventsResponse>(
    `/api/profile-jobs/${encodeURIComponent(profileId)}/${encodeURIComponent(jobId)}/events?${params.toString()}`,
  );
}

/**
 * GET /api/job-reminders/count：顶部徽标轻量投影。
 * 只按 profile 查询；不接受也不发送 platform 参数。
 */
export function getJobReminderCount(profileId: string): Promise<JobReminderCountResponse> {
  return apiRequest<JobReminderCountResponse>(
    `/api/job-reminders/count?profile_id=${encodeURIComponent(profileId)}`,
  );
}

export const JOB_REMINDER_LIST_LIMIT_MAX = 100;

/**
 * GET /api/job-reminders：提醒列表。
 * total 为完整逾期数量；items 最多 100 条（服务端排序与投影为权威）。
 * 客户端不计算提醒资格，不发送 platform 过滤。
 */
export function getJobReminders(
  profileId: string,
  options: { limit?: number } = {},
): Promise<JobReminderListResponse> {
  const limit = options.limit ?? JOB_REMINDER_LIST_LIMIT_MAX;
  if (!Number.isInteger(limit) || limit < 1 || limit > JOB_REMINDER_LIST_LIMIT_MAX) {
    return Promise.reject(
      new RangeError(`提醒列表 limit 必须在 1-${JOB_REMINDER_LIST_LIMIT_MAX} 之间`),
    );
  }
  const params = new URLSearchParams();
  params.set("profile_id", profileId);
  if (options.limit !== undefined) params.set("limit", String(limit));
  return apiRequest<JobReminderListResponse>(`/api/job-reminders?${params.toString()}`);
}

interface JobAdviceResponse {
  ok: true;
  action: string;
  reason: string;
  source: string;
}

const JOB_ADVICE_ACTIONS: readonly JobAdviceAction[] = ["follow_up", "review"];
const JOB_ADVICE_SOURCES: readonly JobAdviceSource[] = ["ai", "rule"];

/** 建议响应 allowlist 校验：action 只能 follow_up/review，source 只能 ai/rule。 */
export function normalizeJobAdvice(payload: JobAdviceResponse): JobAdvice {
  if (!JOB_ADVICE_ACTIONS.includes(payload.action as JobAdviceAction)) {
    throw new JobFeedbackClientError(`服务端返回了不允许的建议行动：${String(payload.action)}`);
  }
  if (!JOB_ADVICE_SOURCES.includes(payload.source as JobAdviceSource)) {
    throw new JobFeedbackClientError(`服务端返回了未知的建议来源：${String(payload.source)}`);
  }
  return {
    action: payload.action as JobAdviceAction,
    reason: String(payload.reason ?? ""),
    source: payload.source as JobAdviceSource,
  };
}

/**
 * POST /api/profile-jobs/{profile_id}/{job_id}/advice：按需单岗位建议。
 * 请求体为空对象；后端重读当前状态，不接受客户端 JD/时间/platform。
 * 建议不持久化、不改变状态，也不作为提醒资格依据。
 */
export async function requestJobAdvice(profileId: string, jobId: string): Promise<JobAdvice> {
  const response = await apiRequest<JobAdviceResponse>(
    `/api/profile-jobs/${encodeURIComponent(profileId)}/${encodeURIComponent(jobId)}/advice`,
    { method: "POST", json: {} },
  );
  return normalizeJobAdvice(response);
}
