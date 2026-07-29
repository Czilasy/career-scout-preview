export interface CandidateProfile {
  id: string;
  name: string;
  confirmed_fields?: Record<string, unknown>;
}

export interface JobItem {
  id?: string;
  job_id?: string;
  title?: string;
  company?: string;
  boss_name?: string;
  salary?: string;
  location?: string;
  jd?: string;
  jd_excerpt?: string;
  job_link?: string;
  source_url?: string;
  canonical_url?: string;
  verdict?: "match" | "not_match" | "uncertain" | string;
  verdict_reason?: string;
  caveats?: string[];
  reason?: string;
  interest_state?: string;
  reject_state?: string;
  origin_zone?: string;
  failure_stage?: string;
  retryable?: boolean;
  attempts?: number;
  _marked?: "interested" | null;
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
  inter_combo_delay: number;
  detail_batch_size: number;
  detail_interval: number;
  detail_reset_every: number;
  detail_batch_cooldown: number;
  screen_batch_size: number;
  screen_concurrency: number;
  match_batch_size: number;
  match_concurrency: number;
}

export interface FrozenSearchScope {
  keywords: string[];
  scope_kind: "cities" | "nationwide";
  cities: string[];
  pages_per_combination: number;
  combination_count: number;
  planned_pages: number;
  task_size: TaskSize;
  scope_digest: string;
}

export interface ScopePreviewRequest {
  keywords: string[];
  scope_kind: "cities" | "nationwide";
  cities: string[];
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

export type TuningExperimentStatus =
  | "draft" | "preflight" | "awaiting_instruction" | "queued"
  | "running" | "evaluating" | "blocked" | "completed"
  | "failed" | "cancelled";

export interface TuningProgress {
  confirmed_rounds: number;
  invalid_rounds?: number;
  remaining_required_rounds: number;
  estimated_remaining_seconds: number;
}

export interface TuningExperiment {
  id: string;
  status: TuningExperimentStatus;
  spec_version: string;
  current_stage?: string | null;
  current_candidate_id?: string | null;
  current_round_id?: string | null;
  input_version_id?: string | null;
  quality_reference_id?: string | null;
  blocked_code?: string | null;
  blocked_reason?: string | null;
  source_scope?: Partial<FrozenSearchScope>;
  progress: TuningProgress;
  candidate_summary?: Array<Record<string, unknown>>;
  evidence?: Array<Record<string, unknown>>;
  can_cancel: boolean;
  can_resume: boolean;
  can_apply: boolean;
}

export interface TuningExperimentResponse {
  ok: true;
  experiment: TuningExperiment;
}

export interface TuningExperimentCreateRequest {
  spec_version: "011-deep-configuration-probing";
  source_scope: ScopePreviewRequest;
  workloads: Array<{
    task_size: TaskSize;
    structure_index: number;
    scope: Record<string, unknown>;
  }>;
}

export interface TuningExperimentResult {
  ok: true;
  experiment_id: string;
  status: TuningExperimentStatus;
  can_apply: boolean;
  candidate_mode_version_digest?: string | null;
  summary?: Record<string, unknown>;
  candidate_summary?: Array<Record<string, unknown>>;
  evidence?: Array<Record<string, unknown>>;
}
