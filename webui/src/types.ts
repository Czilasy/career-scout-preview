export type AppView = "discovery" | "screening";

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
  verdict?: "priority" | "consider" | "match" | "not_match" | "uncertain" | string;
  verdict_reason?: string;
  match_score?: number | null;
  confidence?: number | null;
  dimensions?: Record<string, { score?: number }>;
  reason?: string;
  interest_state?: string;
  reject_state?: string;
  origin_zone?: string;
  failure_stage?: string;
  retryable?: boolean;
  attempts?: number;
  _marked?: "interested" | null;
  [key: string]: unknown;
}

export interface Notice {
  message: string;
  tone: "info" | "success" | "warning" | "error";
}

export interface SelectOption {
  label: string;
  value: string;
}
