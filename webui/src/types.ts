// ---------------------------------------------------------------------------
// 平台身份与稳定错误码（contracts/platform-schema.md、http-api.md、job-source.md）
// ---------------------------------------------------------------------------

import type { ErrorCode } from "./errorCodes";

/** 平台稳定键。注册表权威来源是后端 `webui/platforms.py`。 */
export type Platform = "boss" | "zhilian";

/**
 * 任务 API 公共状态映射（http-api.md 公共状态映射表）。
 * 后端 DB 写 canonical 值，前端 / `/api/latest-running-task` /
 * 状态/进度/取消/结束/最近结果都使用本映射。
 */
export type TaskApiStatus =
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "completed_with_pending"
  | "failed"
  | "cancelled"
  | "interrupted";

/** 平台注册表 / API 错误响应中的稳定错误码。 */
export type PlatformErrorCode =
  | "platform_validation_failed"
  | "platform_disabled"
  | "platform_schema_unavailable"
  | "filter_schema_version_mismatch"
  | "filter_snapshot_incompatible"
  | "city_mapping_unavailable"
  | "city_mapping_missing"
  | "search_filters_not_supported"
  | "job_identity_conflict"
  | "platform_url_mismatch"
  | "task_input_mismatch"
  | "run_identity_conflict"
  | "mixed_platform_jobs"
  | "non_pending_platform_job_ids"
  | "result_not_clearable"
  | "login_space_conflict"
  | "legacy_platform_not_supported"
  | "tuning_platform_mismatch"
  | "migration_backup_failed"
  | "migration_failed";

/** JobSource adapter 错误矩阵中的稳定失败码（job-source.md）。 */
export type SourceErrorCode =
  | "source_cdp_unavailable"
  | "source_login_required"
  | "source_unreachable"
  | "source_account_restricted"
  | "source_status_unclear"
  | "source_blocked"
  | "source_not_found"
  | "source_invalid_output"
  | "source_input_drift"
  | "source_timeout"
  | "source_unknown_error"
  | "source_verification_required"
  | "source_rate_limited";

// ---------------------------------------------------------------------------
// 平台注册表投影
// ---------------------------------------------------------------------------

/** `GET /api/platforms` 返回的单个平台注册项摘要。 */
export interface PlatformSummary {
  key: Platform;
  display_name: string;
  filter_schema_version: number;
  city_mapping_version: number;
  enabled_for_new_tasks: boolean;
  availability_reason: string;
}

/** `GET /api/platforms` 响应体。 */
export interface PlatformsResponse {
  ok: true;
  platforms: PlatformSummary[];
  default_platform: Platform;
}

// ---------------------------------------------------------------------------
// AI 筛选 schema（GET /api/filter-labels）
// ---------------------------------------------------------------------------

export interface FilterOption {
  value: string;
  label: string;
}

export interface FilterField {
  key: string;
  label: string;
  multiple: boolean;
  options: FilterOption[];
}

/** `GET /api/filter-labels?platform=...` 响应体。 */
export interface PlatformFilterSchema {
  ok: true;
  platform: Platform;
  schema_version: number;
  enabled_for_new_tasks: boolean;
  fields: FilterField[];
}

// ---------------------------------------------------------------------------
// 城市目录（GET /api/options）
// ---------------------------------------------------------------------------

export interface CityEntry {
  label: string;
  value: string;
}

/** `GET /api/options?platform=...` 响应体。 */
export interface PlatformCityCatalog {
  ok: true;
  platform: Platform;
  city_mapping_version: number;
  cities: CityEntry[];
}

// ---------------------------------------------------------------------------
// B054 地点条件与目录
// ---------------------------------------------------------------------------

/** 单个“城市 + 区”搜索地点条件；BOSS 可带一个商圈/镇。 */
export interface LocationCondition {
  platform: Platform;
  city_name: string;
  city_code: string;
  district_name: string;
  district_code: string;
  business_name?: string;
  business_code?: string;
  label?: string;
}

/** 区/县或商圈/镇目录条目。 */
export interface LocationCatalogEntry {
  code: string;
  name: string;
  children?: LocationCatalogEntry[];
}

/** `GET /api/location-catalog` 响应体。 */
export interface LocationCatalogResponse {
  ok: true;
  platform: Platform;
  city: string;
  city_code: string;
  districts: LocationCatalogEntry[];
}

// ---------------------------------------------------------------------------
// AI 筛选冻结快照
// ---------------------------------------------------------------------------

/** 单字段冻结值与当时标签（值与标签数组按同序一一对应）。 */
export interface FilterSnapshotField {
  values: string[];
  labels: string[];
}

/** AI run 保存的完整筛选快照（platform-schema.md 完整冻结快照）。 */
export interface FilterSnapshot {
  schema_version: number;
  platform: Platform;
  fields: Record<string, FilterSnapshotField>;
}

// ---------------------------------------------------------------------------
// 浏览器账号投影（GET /api/browser-accounts）
// ---------------------------------------------------------------------------

/** 单个平台在浏览器账号上的登录空间投影。 */
export interface BrowserAccountPlatformSpace {
  cdp_port: number;
}

/** 浏览器账号记录，平台登录空间以非敏感投影形式返回。 */
export interface BrowserAccount {
  id: string;
  name: string;
  builtin?: boolean;
  // GET /api/browser-accounts 不返回 profile 路径或路径摘要（http-api.md L319）。
  platforms?: Partial<Record<Platform, BrowserAccountPlatformSpace>>;
}

// ---------------------------------------------------------------------------
// 统一任务快照（GET /api/task-state、/api/search-progress、/api/latest-running-task）
// ---------------------------------------------------------------------------

/** 任务暂停 / 阻断时的稳定错误信息。 */
export interface TaskPauseInfo {
  error_code?: ErrorCode | string;
  error_reason?: string;
}

/**
 * 任务统一快照。覆盖状态、进度、平台身份、输入摘要、来源审计与暂停原因。
 * DiscoveryView.vue / TaskProgress.vue 等组件应直接复用本类型。
 */
export interface TaskSnapshot {
  status?: TaskApiStatus | string;
  progress?: Record<string, unknown>;
  logs?: string[];
  error?: string;
  started_at?: number;
  finished_at?: number;
  /** 后端从 task_logs pause/resume 事件推导的累计实际运行时长（排除暂停，毫秒）。 */
  active_elapsed_ms?: number;
  stage?: string;
  success_count?: number;
  fail_count?: number;
  unstarted_count?: number;
  total?: number;
  kept_count?: number;
  dropped_count?: number;
  pending_count?: number;
  source_total?: number;
  pause_info?: TaskPauseInfo | null;
  execution_config?: Record<string, unknown> | null;
  platform?: Platform;
  task_input_digest?: string;
  scope_digest?: string;
  source_summary?: Record<string, unknown> | null;
  source_outcomes?: Array<Record<string, unknown>> | null;
  /** 016：软失败组合留痕（最近 20 条倒序），文案来自统一注册表。 */
  combo_issues?: ComboIssue[] | null;
  /** 一键链路标记：抓取任务完成后前端自动接续 AI 筛选。 */
  auto_screen?: boolean;
}

/** 016：单个组合的软失败记录（combo_issue 事件投影）。 */
export interface ComboIssue {
  combo_key: string;
  code: string;
  code_text: string;
  reason: string;
  ts: string;
}

/** 本轮上下文：后端从父抓取 run 与 AI 筛选 run 合并返回。 */
export interface RoundContext {
  platform: Platform;
  keywords: string[];
  cities: string[];
  locations?: LocationCondition[];
  screening_fields: Record<string, string[]>;
  profile_summary: string;
  profile_facts: Record<string, unknown>;
  scrape_task_id: string;
  screen_run_id: string;
  status: string;
  resumable: boolean;
  /** 2993 回归：已存在 AI 筛选轮时禁止空条件静默继续。 */
  has_frozen_filters?: boolean;
}

export interface CandidateProfile {
  id: string;
  name: string;
  confirmed_fields?: Record<string, unknown>;
}

export interface JobItem {
  id?: string;
  job_id?: string;
  /**
   * 平台原始身份。任何接口不得把 `platform_job_id` 填进 `job_id`：
   * `job_id` 是内部 UUID，`platform_job_id` 是平台稳定 ID。
   * 见 http-api.md 岗位对象合同。
   */
  platform_job_id?: string;
  /** 该岗位所属平台。后端权威来源，前端不按 URL 猜。 */
  platform?: Platform;
  title?: string;
  company?: string;
  boss_name?: string;
  salary?: string;
  location?: string;
  experience?: string;
  degree?: string;
  jd?: string;
  jd_excerpt?: string;
  job_link?: string;
  source_url?: string;
  canonical_url?: string;
  verdict?: "match" | "not_match" | "uncertain" | string;
  verdict_reason?: string;
  caveats?: string[];
  /** 岗位靠谱判定（B033）：code/level/reason 结构化 flags，高危红 / 中危黄。 */
  flags?: { code: string; level: "high" | "medium"; reason: string }[];
  reason?: string;
  interest_state?: string;
  reject_state?: string;
  origin_zone?: string;
  failure_stage?: string;
  retryable?: boolean;
  attempts?: number;
  _marked?: "interested" | "rejected" | null;
  /** 后端按 profile_jobs.applied_at 回填：该岗位是否投递过（仅带 profile_id 查询时返回）。 */
  _applied?: boolean;
  /** 前端合并多平台结果时标记的来源 run（仅前端使用，供单岗位动作定位来源）。 */
  _result_run_id?: string;
  /**
   * 跨平台重复簇成员（019）：合并视图由剔除行 extra.cross_platform_dup_of
   * 反查保留条目后挂载的运行时数据（复用 _result_run_id 惯例，仅前端使用，
   * 不持久化、不回写）。成员字段取自本平台剔除行自身。
   */
  _also_on_copies?: Array<{
    platform: Platform;
    salary?: string;
    source_url?: string;
    platform_job_id?: string;
  }>;
  extra?: Record<string, unknown>;
}

export interface Notice {
  message: string;
  tone: "info" | "success" | "warning" | "error";
}

export type TaskSize = "small" | "medium" | "large";
export type ExecutionSelection = "stable" | "balanced" | "extreme" | "custom";
export type SystemExecutionMode = Exclude<ExecutionSelection, "custom">;

export interface ExecutionSettings {
  /** 每组合翻页数：不属于速度配置快照（FR-009），但需随自定义配置持久化。 */
  pages: number;
  inter_combo_delay: number;
  detail_batch_size: number;
  detail_interval: number;
  detail_reset_every: number;
  detail_batch_cooldown: number;
  detail_tab_pool_size: number;
  screen_batch_size: number;
  screen_concurrency: number;
  match_batch_size: number;
  match_concurrency: number;
}

export interface FrozenSearchScope {
  keywords: string[];
  scope_kind: "cities" | "nationwide";
  cities: string[];
  locations?: LocationCondition[];
  pages_per_combination: number;
  combination_count: number;
  planned_pages: number;
  task_size: TaskSize;
  scope_digest: string;
}

export interface ScopePreviewRequest {
  platform: Platform;
  keywords: string[];
  scope_kind: "cities" | "nationwide";
  cities: string[];
  locations?: LocationCondition[];
  pages_per_combination: number;
}

export interface ScopePreviewResponse {
  ok: true;
  scope: FrozenSearchScope;
  deduplicated: { keywords: string[]; cities: string[] };
}

export interface AdvancedSettingsState {
  ok: true;
  selection: ExecutionSelection;
  settings: ExecutionSettings;
  last_custom: { config_digest: string; settings: ExecutionSettings } | null;
  mode_version: {
    id: string;
    version_digest: string;
    previous_version_id?: string | null;
    available_modes: SystemExecutionMode[];
  };
  manual_ranges: Record<string, [number, number] | { min: number; max: number }>;
  config_schema_version: number;
}

export interface ModeSelectionResponse {
  ok: true;
  selection: ExecutionSelection;
  settings: ExecutionSettings;
  task_size: TaskSize;
  mode_version_id: string | null;
  config_digest: string;
}
