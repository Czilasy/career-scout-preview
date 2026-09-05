import type {
  AdvancedSettingsState,
  ExecutionSelection,
  ExecutionSettings,
  ModeSelectionResponse,
  ScopePreviewRequest,
  ScopePreviewResponse,
  IntegritySnapshot,
} from "./types";
import { ERROR_MESSAGES } from "./errorCodes";

let sessionToken = "";
let buildIdentityVerified = false;
let sessionRuntimeMode = "source";
let sessionInitialization: Promise<void> | null = null;

// 桌面壳（pywebview）注入的 JS API；浏览器模式下不存在
declare global {
  interface Window {
    pywebview?: {
      api?: {
        open_external?: (url: string) => Promise<{ ok: boolean; error?: string }>;
        quit_app?: () => Promise<{ ok: boolean; error?: string }>;
        // 036 自绘标题栏窗口控制（desktop.py DesktopJsApi 提供）
        window_minimize?: () => Promise<{ ok: boolean; error?: string }>;
        window_toggle_maximize?: () => Promise<{ ok: boolean; maximized?: boolean; error?: string }>;
        window_is_maximized?: () => Promise<{ ok: boolean; maximized?: boolean; error?: string }>;
        window_close?: () => Promise<{ ok: boolean; error?: string }>;
      };
    };
  }
}

export const GITHUB_REPO_URL = "https://github.com/Czilasy/career-scout-preview";

/** 打开外链：桌面壳走 js_api 抛到系统浏览器（后端有域名白名单），
 * 浏览器模式直接新窗口打开。 */
export async function openExternalLink(url: string): Promise<void> {
  const api = window.pywebview?.api;
  if (api?.open_external) {
    try {
      await api.open_external(url);
      return;
    } catch {
      // 落到浏览器打开
    }
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

/** 应用内更新重启后优雅退出应用（仅桌面壳可用）。 */
export async function quitDesktopApp(): Promise<boolean> {
  const api = window.pywebview?.api;
  if (!api?.quit_app) return false;
  try {
    const result = await api.quit_app();
    return Boolean(result?.ok);
  } catch {
    return false;
  }
}

export const expectedBackendBuildHash = __EXPECTED_BACKEND_BUILD_HASH__;

export function setBuildIdentity(hash: string): boolean {
  buildIdentityVerified = Boolean(
    expectedBackendBuildHash
    && hash.trim()
    && hash.trim() === expectedBackendBuildHash,
  );
  return buildIdentityVerified;
}

export class ApiError extends Error {
  status: number;
  payload: Record<string, unknown>;

  constructor(status: number, payload: Record<string, unknown>) {
    // error_reason 优先于 error/error_code：后两者是机器码（如 block_not_resolved），
    // 直接展示给用户会产生"找不到任务"之类的误读；error_reason 恒为中文原因。
    // 机器码直出前先查中文映射表（020 US2），查不到才回退原始值。
    const mapped = ERROR_MESSAGES[String(payload.error_code || "")] || "";
    const message = String(
      payload.user_message || payload.message || payload.error_reason || mapped || payload.error || payload.error_code || `请求失败（${status}）`,
    );
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

type ApiOptions = Omit<RequestInit, "body"> & {
  body?: BodyInit | null;
  json?: unknown;
};

export function initializeSession(): Promise<void> {
  if (sessionInitialization) return sessionInitialization;
  sessionInitialization = (async () => {
    const response = await fetch("/api/session", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new ApiError(response.status, {});
    const payload = await response.json() as { token?: string; build_hash?: string; runtime_mode?: string };
    sessionToken = payload.token || "";
    if (payload.build_hash) setBuildIdentity(payload.build_hash);
    sessionRuntimeMode = payload.runtime_mode === "exe" ? "exe" : "source";
  })().catch((error) => {
    sessionInitialization = null;
    throw error;
  });
  return sessionInitialization;
}

/** 当前会话运行时模式：桌面 EXE 为 exe，源码模式/未知为 source。 */
export function currentRuntimeMode(): string {
  return sessionRuntimeMode;
}

/** 仅测试使用：清除会话初始化缓存，让下一次 initializeSession 重新请求。 */
export function resetSessionStateForTests(): void {
  sessionToken = "";
  buildIdentityVerified = false;
  sessionRuntimeMode = "source";
  sessionInitialization = null;
}

export async function apiRequest<T>(path: string, options: ApiOptions = {}): Promise<T> {
  await initializeSession();
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (sessionToken) headers.set("X-Boss-Token", sessionToken);

  let body = options.body;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }

  const response = await fetch(path, {
    ...options,
    headers,
    body,
    credentials: "same-origin",
  });
  const payload = await response.json().catch((err: unknown) => {
    console.warn("response.json parse failed", err);
    return {};
  }) as Record<string, unknown>;
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload as T;
}

export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function userFacingMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError && typeof error.payload.user_message === "string" && error.payload.user_message
    ? error.payload.user_message
    : fallback;
}

export interface WhiteboxReport {
  ok: boolean;
  owner_kind: string;
  owner_id: string;
  lifecycle_status?: string;
  integrity: IntegritySnapshot;
  plan?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  units?: Array<Record<string, unknown>>;
  events?: Array<Record<string, unknown>>;
  events_truncated?: boolean;
  next_sequence?: number;
}

/** 开发者白箱查询；普通任务轮询不拉取逐事件技术明细。 */
export function fetchWhiteboxReport(
  ownerKind: string,
  ownerId: string,
  options: { includeEvents?: boolean; eventLimit?: number } = {},
): Promise<WhiteboxReport> {
  const query = new URLSearchParams();
  if (options.includeEvents) query.set("include_events", "1");
  if (options.eventLimit !== undefined) query.set("event_limit", String(options.eventLimit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiRequest<WhiteboxReport>(
    `/api/task-state/${encodeURIComponent(ownerKind)}/${encodeURIComponent(ownerId)}/whitebox${suffix}`,
  );
}

export const settingsApi = {
  previewScope(payload: ScopePreviewRequest) {
    return apiRequest<ScopePreviewResponse>("/api/search-scope/preview", {
      method: "POST", json: payload,
    });
  },
  getAdvancedSettings() {
    return apiRequest<AdvancedSettingsState>("/api/advanced-settings");
  },
  saveCustom(settings: ExecutionSettings) {
    return apiRequest<AdvancedSettingsState>("/api/advanced-settings/custom", {
      method: "PUT", json: { config_schema_version: 1, settings },
    });
  },
  selectMode(mode: ExecutionSelection, scopeDigest: string) {
    return apiRequest<ModeSelectionResponse>("/api/advanced-settings/select-mode", {
      method: "POST", json: { mode, scope_digest: scopeDigest },
    });
  },
};

// ---------------------------------------------------------------------------
// 应用内更新（后端 webui/updater.py；仅桌面 EXE 模式启用入口）
// ---------------------------------------------------------------------------
export interface UpdateCheckResult {
  ok: boolean;
  current: string;
  latest: string;
  has_update: boolean;
  release_url: string;
  release_notes: string;
  asset_name: string;
  asset_url: string;
  asset_size: number;
  sha256_url: string;
  reason: string;
  checked_at?: number;
  runtime_mode: string;
  installable: boolean;
}

export interface UpdateProgress {
  ok?: boolean;
  already?: boolean;
  status: "idle" | "downloading" | "verifying" | "ready" | "failed";
  received: number;
  total: number;
  progress: number;
  path: string;
  error: string;
}

export const updateApi = {
  check() {
    return apiRequest<UpdateCheckResult>("/api/update-check");
  },
  download() {
    return apiRequest<UpdateProgress>("/api/update-download", { method: "POST" });
  },
  status() {
    return apiRequest<UpdateProgress>("/api/update-status");
  },
  restart() {
    return apiRequest<{ ok: boolean; user_message?: string }>(
      "/api/update-restart", { method: "POST" },
    );
  },
};

// ---------------------------------------------------------------------------
// 日志查看（022-jd-stall-guard US4）：读 career-scout.log 尾部/分页/轮询
// ---------------------------------------------------------------------------
export interface LogsResponse {
  ok: boolean;
  lines: string[];
  start: number;
  end: number;
  total: number;
  identity: string;
  rotated: boolean;
  empty: boolean;
}

export interface LogsQuery {
  tail?: number;
  offset?: number;
  since?: number;
  identity?: string;
  /** 035：按任务查看运行日志，优先读取持久化任务日志并兼容旧版文件日志。 */
  task_id?: string;
}

export function fetchLogs(params: LogsQuery = {}): Promise<LogsResponse> {
  const query = new URLSearchParams();
  if (params.tail) query.set("tail", String(params.tail));
  if (params.offset) query.set("offset", String(params.offset));
  if (params.since) query.set("since", String(params.since));
  if (params.identity) query.set("identity", params.identity);
  if (params.task_id) query.set("task_id", params.task_id);
  const qs = query.toString();
  return apiRequest<LogsResponse>(`/api/logs${qs ? `?${qs}` : ""}`);
}

// ---------------------------------------------------------------------------
// 浏览器注册表（029 B082③）：抓取用 Chromium 浏览器探测/选择/手动路径校验
// ---------------------------------------------------------------------------
export interface BrowserRegistryEntryState {
  key: string;
  name: string;
  installed: boolean;
  path: string | null;
}

export interface BrowserSelection {
  mode: "auto" | "registry" | "manual";
  key?: string;
  manual_path?: string;
}

export interface BrowserRegistryResponse {
  registry: BrowserRegistryEntryState[];
  selection: BrowserSelection;
  effective_path: string | null;
}

export type BrowserSelectionSave =
  | { mode: "auto" }
  | { mode: "registry"; key: string }
  | { mode: "manual"; path: string };

export type BrowserPathValidation =
  | { ok: true; version: string }
  | { ok: false; error: string; message: string };

export const browserRegistryApi = {
  get() {
    return apiRequest<BrowserRegistryResponse>("/api/browser-registry");
  },
  save(payload: BrowserSelectionSave) {
    return apiRequest<BrowserRegistryResponse & { ok: boolean }>(
      "/api/browser-registry", { method: "PUT", json: payload },
    );
  },
  /** 手动路径即时校验：失败时后端返回 400，抛 ApiError（message 用户可读）。 */
  validatePath(path: string) {
    return apiRequest<BrowserPathValidation>(
      "/api/browser-registry/validate-path", { method: "POST", json: { path } },
    );
  },
};
