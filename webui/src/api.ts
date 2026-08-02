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
let sessionInitialization: Promise<void> | null = null;

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
    const payload = await response.json() as { token?: string; build_hash?: string };
    sessionToken = payload.token || "";
    if (payload.build_hash) setBuildIdentity(payload.build_hash);
  })().catch((error) => {
    sessionInitialization = null;
    throw error;
  });
  return sessionInitialization;
}

export async function apiRequest<T>(path: string, options: ApiOptions = {}): Promise<T> {
  await initializeSession();
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (sessionToken) headers.set("X-Boss-Token", sessionToken);
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD"].includes(method)) {
    if (!buildIdentityVerified) {
      throw new ApiError(409, {
        error_code: "build_identity_mismatch",
        user_message: "页面版本与当前后端不一致，请刷新页面后重试",
      });
    }
    headers.set("X-Boss-Build", expectedBackendBuildHash);
  }

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
