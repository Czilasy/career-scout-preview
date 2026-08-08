import type {
  AdvancedSettingsState,
  ExecutionSelection,
  ExecutionSettings,
  ModeSelectionResponse,
  ScopePreviewRequest,
  ScopePreviewResponse,
} from "./types";

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
    const message = String(
      payload.user_message || payload.message || payload.error || payload.error_code || `请求失败（${status}）`,
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
