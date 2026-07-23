let sessionToken = "";

export class ApiError extends Error {
  status: number;
  payload: Record<string, unknown>;

  constructor(status: number, payload: Record<string, unknown>) {
    const message = String(
      payload.user_message || payload.error || payload.error_code || `请求失败（${status}）`,
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

export async function initializeSession(): Promise<void> {
  const response = await fetch("/api/session", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new ApiError(response.status, {});
  const payload = await response.json() as { token?: string };
  sessionToken = payload.token || "";
}

export async function apiRequest<T>(path: string, options: ApiOptions = {}): Promise<T> {
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
