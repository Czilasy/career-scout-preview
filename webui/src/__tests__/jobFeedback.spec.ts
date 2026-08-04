// Task 004：岗位反馈前端 client 合同测试。
// 不变式锚点：specs/002-job-feedback-reminders/contracts/http-api.md（job-feedback-v1）、
// contracts/ui-interaction.md 幂等与失败节、data-model.md 岗位身份解析节。
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, expectedBackendBuildHash, setBuildIdentity } from "../api";
import {
  JOB_FEEDBACK_ACTIONS,
  JOB_LIFECYCLE_STATUS_LABELS,
  JobFeedbackClientError,
  JobFeedbackRequestContext,
  JobIdentityIncompleteError,
  buildJobFeedbackActionPayload,
  buildJobIdentity,
  generateJobFeedbackRequestId,
  getJobLifecycleEvents,
  getJobLifecycleState,
  getJobReminderCount,
  getJobReminders,
  mapJobFeedbackError,
  mergeJobLifecycleState,
  normalizeJobAdvice,
  requestJobAdvice,
  safeCanonicalUrl,
  submitJobFeedbackAction,
} from "../jobFeedback";
import type {
  JobFeedbackAction,
  JobLifecycleStateSnapshot,
  JobLifecycleStatus,
} from "../jobFeedback";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface RecordedCall {
  url: string;
  method: string;
  body: unknown;
}

interface RouteHandler {
  pattern: RegExp;
  respond: (url: string, init?: RequestInit) => Response;
}

// 所有测试统一 mock fetch；/api/session 恒成功（apiRequest 的会话初始化依赖它）。
function installFetch(handlers: RouteHandler[] = []): { calls: RecordedCall[] } {
  const calls: RecordedCall[] = [];
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    let body: unknown;
    if (typeof init?.body === "string") {
      try { body = JSON.parse(init.body); } catch { body = init.body; }
    }
    calls.push({ url, method: (init?.method || "GET").toUpperCase(), body });
    if (url === "/api/session") return jsonResponse({ token: "test-token" });
    for (const handler of handlers) {
      if (handler.pattern.test(url)) return handler.respond(url, init);
    }
    return jsonResponse({ ok: true });
  });
  vi.stubGlobal("fetch", mock);
  return { calls };
}

function snapshot(revision: number, status: JobLifecycleStatus = "applied"): JobLifecycleStateSnapshot {
  return {
    profile_id: "p1",
    job_id: "j1",
    status,
    applied_at: "2026-06-01T02:00:00+00:00",
    last_follow_up_at: null,
    revision,
    reminder: {
      eligible: false,
      baseline_at: "2026-06-01T02:00:00+00:00",
      elapsed_seconds: 86400,
      elapsed_days: 1,
    },
  };
}

function actionReceipt(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    replayed: false,
    changed: true,
    event_id: "event-uuid",
    event_sequence: 4,
    state: snapshot(4, "read"),
    ...overrides,
  };
}

function errorBody(errorCode: string, userMessage: string) {
  return { ok: false, error_code: errorCode, user_message: userMessage, details: {} };
}

describe("T029 生命周期 state/action/event 类型与错误映射", () => {
  afterEach(() => {
    setBuildIdentity(expectedBackendBuildHash);
    vi.unstubAllGlobals();
  });

  it("按内部 ID 读取 state：GET /api/profile-jobs/state 且只带 profile_id/job_id", async () => {
    const state = snapshot(4);
    const { calls } = installFetch([
      { pattern: /^\/api\/profile-jobs\/state/, respond: () => jsonResponse({ ok: true, exists: true, state }) },
    ]);

    const result = await getJobLifecycleState("p1", { job_id: "j1" });

    const read = calls.find((call) => call.url.startsWith("/api/profile-jobs/state"));
    expect(read?.method).toBe("GET");
    expect(read?.url).toBe("/api/profile-jobs/state?profile_id=p1&job_id=j1");
    expect(result).toEqual({ ok: true, exists: true, state });
  });

  it("稳定错误体映射为结构化错误：status/error_code/user_message", async () => {
    installFetch([
      {
        pattern: /^\/api\/profile-jobs\/actions$/,
        respond: () => jsonResponse(errorBody("idempotency_conflict", "同一请求 ID 被用于不同请求"), 409),
      },
    ]);

    let caught: unknown;
    try {
      await submitJobFeedbackAction({
        requestId: generateJobFeedbackRequestId(),
        profileId: "p1",
        job: { job_id: "j1" },
        action: "mark_read",
      });
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(ApiError);
    const mapped = mapJobFeedbackError(caught);
    expect(mapped.status).toBe(409);
    expect(mapped.errorCode).toBe("idempotency_conflict");
    expect(mapped.userMessage).toBe("同一请求 ID 被用于不同请求");
  });

  it("非 API 错误（网络层）映射为 unknown_error 而不是伪造服务端码", () => {
    const mapped = mapJobFeedbackError(new TypeError("fetch failed"));
    expect(mapped.status).toBe(0);
    expect(mapped.errorCode).toBe("unknown_error");
    expect(mapped.userMessage).toBeTruthy();
  });

  it("状态标签覆盖全部六种生命周期状态", () => {
    const statuses: JobLifecycleStatus[] = ["new", "interested", "read", "applied", "stale", "deleted"];
    for (const status of statuses) {
      expect(JOB_LIFECYCLE_STATUS_LABELS[status]).toBeTruthy();
    }
    expect(Object.keys(JOB_LIFECYCLE_STATUS_LABELS)).toHaveLength(6);
  });

  it("action allowlist 与冻结合同一致", () => {
    expect([...JOB_FEEDBACK_ACTIONS].sort()).toEqual([
      "correct_applied_at",
      "correct_status",
      "follow_up",
      "mark_applied",
      "mark_read",
      "mark_stale",
      "restore_applied",
    ]);
  });

  it("events 读取按 after_sequence/limit 组装 URL，limit 上限 200", async () => {
    const { calls } = installFetch([
      {
        pattern: /^\/api\/profile-jobs\/p1\/j1\/events/,
        respond: () => jsonResponse({ ok: true, events: [], next_after_sequence: 7 }),
      },
    ]);

    await getJobLifecycleEvents("p1", "j1", { afterSequence: 7, limit: 50 });
    const read = calls.find((call) => call.url.includes("/events"));
    expect(read?.url).toBe("/api/profile-jobs/p1/j1/events?after_sequence=7&limit=50");
    expect(() => getJobLifecycleEvents("p1", "j1", { limit: 201 })).toThrow(RangeError);
  });
});

describe("T030 reminder count/list：真实 total、最多 100 条、无 platform 过滤", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("count 请求不携带 platform，返回完整 total", async () => {
    const { calls } = installFetch([
      {
        pattern: /^\/api\/job-reminders\/count/,
        respond: () => jsonResponse({ ok: true, profile_id: "p1", threshold_hours: 720, total: 137 }),
      },
    ]);

    const result = await getJobReminderCount("p1");

    expect(calls.some((call) => call.url.includes("platform"))).toBe(false);
    expect(calls.find((call) => call.url.includes("count"))?.url)
      .toBe("/api/job-reminders/count?profile_id=p1");
    expect(result.total).toBe(137);
  });

  it("list 请求不发送 platform；total 为完整数量，items 最多 100 条", async () => {
    const items = Array.from({ length: 100 }, (_, index) => ({
      job_id: `job-${index}`,
      platform: index % 2 === 0 ? "boss" : "zhilian",
      platform_job_id: `platform-${index}`,
      title: `岗位 ${index}`,
      company: "示例公司",
      salary: "20-30K",
      location: "上海",
      canonical_url: "",
      status: "applied",
      applied_at: "2026-05-01T02:00:00+00:00",
      last_follow_up_at: null,
      baseline_at: "2026-05-01T02:00:00+00:00",
      elapsed_seconds: 8294400,
      elapsed_days: 96,
      can_open: false,
    }));
    const { calls } = installFetch([
      {
        pattern: /^\/api\/job-reminders\?/,
        respond: () => jsonResponse({ ok: true, profile_id: "p1", threshold_hours: 720, total: 137, items }),
      },
    ]);

    const result = await getJobReminders("p1");

    const listCall = calls.find((call) => call.url.startsWith("/api/job-reminders?"));
    expect(listCall?.url).toBe("/api/job-reminders?profile_id=p1");
    expect(listCall?.url.includes("platform")).toBe(false);
    expect(result.total).toBe(137);
    expect(result.items.length).toBeLessThanOrEqual(100);
  });

  it("limit 超过 100 在客户端即被阻断，不发请求", async () => {
    const { calls } = installFetch();
    await expect(getJobReminders("p1", { limit: 101 })).rejects.toThrow(RangeError);
    expect(calls.filter((call) => call.url.includes("job-reminders"))).toHaveLength(0);
  });
});

describe("T031 advice action/source allowlist 与服务端错误投影", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("请求体为空对象，POST 到指定岗位 advice 路由", async () => {
    const { calls } = installFetch([
      {
        pattern: /\/advice$/,
        respond: () => jsonResponse({ ok: true, action: "follow_up", reason: "建议先确认进展。", source: "ai" }),
      },
    ]);

    const advice = await requestJobAdvice("p1", "j1");

    const adviceCall = calls.find((call) => call.url.endsWith("/advice"));
    expect(adviceCall?.method).toBe("POST");
    expect(adviceCall?.url).toBe("/api/profile-jobs/p1/j1/advice");
    expect(adviceCall?.body).toEqual({});
    expect(advice).toEqual({ action: "follow_up", reason: "建议先确认进展。", source: "ai" });
  });

  it("只接受 follow_up/review 与 ai/rule，越界值一律拒绝", () => {
    expect(normalizeJobAdvice({ ok: true, action: "follow_up", reason: "r", source: "ai" }).action).toBe("follow_up");
    expect(normalizeJobAdvice({ ok: true, action: "review", reason: "r", source: "rule" }).action).toBe("review");
    expect(() => normalizeJobAdvice({ ok: true, action: "mark_stale", reason: "r", source: "ai" }))
      .toThrow(JobFeedbackClientError);
    expect(() => normalizeJobAdvice({ ok: true, action: "follow_up", reason: "r", source: "model" }))
      .toThrow(JobFeedbackClientError);
  });

  it("非逾期岗位的 409 reminder_not_eligible 原样投影为 API 错误", async () => {
    installFetch([
      {
        pattern: /\/advice$/,
        respond: () => jsonResponse(errorBody("reminder_not_eligible", "该岗位当前不在提醒范围"), 409),
      },
    ]);

    let caught: unknown;
    try {
      await requestJobAdvice("p1", "j1");
    } catch (error) {
      caught = error;
    }
    const mapped = mapJobFeedbackError(caught);
    expect(mapped.status).toBe(409);
    expect(mapped.errorCode).toBe("reminder_not_eligible");
  });
});

describe("T032 request ID 生命周期：重试复用、再次确认换新", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("每次确认生成 UUID；不确定重试复用；明确再次确认生成新 ID", () => {
    const context = new JobFeedbackRequestContext();
    const first = context.requestId;
    expect(first).toMatch(UUID_PATTERN);

    // 网络不确定时的重试复用同一 ID。
    expect(context.retry()).toBe(first);
    expect(context.retry()).toBe(first);

    // 用户明确再次确认 → 新 ID。
    const second = context.newConfirmation();
    expect(second).toMatch(UUID_PATTERN);
    expect(second).not.toBe(first);
    expect(context.retry()).toBe(second);
  });

  it("重试复用同一 request_id 发送请求；再次确认后换新 ID", async () => {
    const seenRequestIds = new Set<string>();
    const { calls } = installFetch([
      {
        pattern: /^\/api\/profile-jobs\/actions$/,
        respond: (_url, init) => {
          const requestId = String(
            (JSON.parse(String(init?.body)) as Record<string, unknown>).request_id,
          );
          const receipt = actionReceipt({ replayed: seenRequestIds.has(requestId) });
          seenRequestIds.add(requestId);
          return jsonResponse(receipt);
        },
      },
    ]);

    const context = new JobFeedbackRequestContext();
    const send = (requestId: string) => submitJobFeedbackAction({
      requestId,
      profileId: "p1",
      job: { job_id: "j1" },
      action: "mark_read",
    });

    const first = await send(context.retry());
    const retried = await send(context.retry());
    const fresh = await send(context.newConfirmation());

    const actionCalls = calls.filter((call) => call.url.endsWith("/actions"));
    const ids = actionCalls.map((call) => (call.body as Record<string, unknown>).request_id);
    expect(ids[0]).toBe(ids[1]); // 不确定重试复用同一 ID
    expect(ids[2]).not.toBe(ids[0]); // 明确再次确认生成新 ID
    expect(first.replayed).toBe(false);
    expect(retried.replayed).toBe(true);
    expect(fresh.replayed).toBe(false);
  });

  it("payload 字段组合规则：不允许无关字段、必填字段缺失即阻断", () => {
    const base = { requestId: "rid", profileId: "p1", job: { job_id: "j1" } as const };

    const applied = buildJobFeedbackActionPayload({
      ...base, action: "mark_applied", appliedAt: "2026-06-01T10:00:00+08:00",
    });
    expect(applied).toEqual({
      request_id: "rid",
      profile_id: "p1",
      job: { job_id: "j1" },
      action: "mark_applied",
      applied_at: "2026-06-01T10:00:00+08:00",
      target_status: null,
    });

    // mark_read 不接受 applied_at。
    expect(() => buildJobFeedbackActionPayload({ ...base, action: "mark_read", appliedAt: "2026-06-01T10:00:00+08:00" }))
      .toThrow(JobFeedbackClientError);
    // follow_up 不接受 target_status。
    expect(() => buildJobFeedbackActionPayload({ ...base, action: "follow_up", targetStatus: "stale" }))
      .toThrow(JobFeedbackClientError);
    // correct_applied_at 必须带 applied_at。
    expect(() => buildJobFeedbackActionPayload({ ...base, action: "correct_applied_at" }))
      .toThrow(JobFeedbackClientError);
    // correct_status 必须带 target_status。
    expect(() => buildJobFeedbackActionPayload({ ...base, action: "correct_status" }))
      .toThrow(JobFeedbackClientError);
    // allowlist 外的 action 直接拒绝。
    expect(() => buildJobFeedbackActionPayload({ ...base, action: "erase" as JobFeedbackAction }))
      .toThrow(JobFeedbackClientError);
  });
});

describe("T033 revision 防倒退：低 revision 响应不能覆盖较新 state", () => {
  it("接受更高或相同 revision，拒绝更低 revision", () => {
    const current = snapshot(5);

    expect(mergeJobLifecycleState(null, snapshot(1))).toEqual(snapshot(1));
    // 低 revision 晚到：保留当前状态（引用不变）。
    expect(mergeJobLifecycleState(current, snapshot(3))).toBe(current);
    // 相同 revision：允许以响应快照为准。
    expect(mergeJobLifecycleState(current, snapshot(5, "read")).status).toBe("read");
    // 更高 revision：采纳。
    expect(mergeJobLifecycleState(current, snapshot(6)).revision).toBe(6);
  });
});

describe("T034 岗位身份与平台安全 URL", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("内部 ID 身份：写请求 body.job 只含 job_id", async () => {
    const { calls } = installFetch([
      { pattern: /^\/api\/profile-jobs\/actions$/, respond: () => jsonResponse(actionReceipt()) },
    ]);

    await submitJobFeedbackAction({
      requestId: generateJobFeedbackRequestId(),
      profileId: "p1",
      job: buildJobIdentity({ job_id: "internal-uuid" }),
      action: "follow_up",
    });

    const actionCall = calls.find((call) => call.url.endsWith("/actions"));
    expect((actionCall?.body as Record<string, unknown>).job).toEqual({ job_id: "internal-uuid" });
  });

  it("权威三元组身份：完整携带 platform/platform_job_id/canonical_url 与展示字段", async () => {
    const { calls } = installFetch([
      { pattern: /^\/api\/profile-jobs\/actions$/, respond: () => jsonResponse(actionReceipt()) },
    ]);

    const identity = buildJobIdentity({
      platform: "boss",
      platform_job_id: "platform-stable-id",
      canonical_url: "https://www.zhipin.com/job_detail/platform-stable-id.html",
      title: "Python 后端工程师",
      company: "示例公司",
    });

    await submitJobFeedbackAction({
      requestId: generateJobFeedbackRequestId(),
      profileId: "p1",
      job: identity,
      action: "mark_applied",
    });

    const body = calls.find((call) => call.url.endsWith("/actions"))?.body as Record<string, unknown>;
    expect(body.job).toMatchObject({
      platform: "boss",
      platform_job_id: "platform-stable-id",
      canonical_url: "https://www.zhipin.com/job_detail/platform-stable-id.html",
      title: "Python 后端工程师",
      company: "示例公司",
    });
    expect(body.job).not.toHaveProperty("job_id");
  });

  it("三元组缺一即阻断；已有内部 ID 时不要求三元组", () => {
    expect(() => buildJobIdentity({ platform: "boss", platform_job_id: "x" }))
      .toThrow(JobIdentityIncompleteError);
    expect(() => buildJobIdentity({ platform: "boss", canonical_url: "https://www.zhipin.com/job_detail/x.html" }))
      .toThrow(JobIdentityIncompleteError);
    expect(() => buildJobIdentity({ platform_job_id: "x", canonical_url: "https://www.zhipin.com/job_detail/x.html" }))
      .toThrow(JobIdentityIncompleteError);
    expect(() => buildJobIdentity({}))
      .toThrow(JobIdentityIncompleteError);
    // 有内部 ID 即可；服务端按内部 ID 解析并校验附带三元组一致性。
    expect(buildJobIdentity({ job_id: "j1", platform: "boss" })).toEqual({ job_id: "j1" });
  });

  it("state 查询支持权威三元组解析已存在岗位", async () => {
    const { calls } = installFetch([
      {
        pattern: /^\/api\/profile-jobs\/state/,
        respond: () => jsonResponse({ ok: true, exists: false, job_id: null }),
      },
    ]);

    await getJobLifecycleState("p1", buildJobIdentity({
      platform: "zhilian",
      platform_job_id: "z-1",
      canonical_url: "https://www.zhaopin.com/jobdetail/z-1.htm",
    }));

    const read = calls.find((call) => call.url.startsWith("/api/profile-jobs/state"));
    const params = new URL(`https://local${read?.url}`).searchParams;
    expect(params.get("profile_id")).toBe("p1");
    expect(params.get("platform")).toBe("zhilian");
    expect(params.get("platform_job_id")).toBe("z-1");
    expect(params.get("canonical_url")).toBe("https://www.zhaopin.com/jobdetail/z-1.htm");
    expect(params.has("job_id")).toBe(false);
  });

  it("canonical URL 按所属平台复验：非法/跨平台/无效链接返回空", () => {
    // BOSS：https + zhipin 域名。
    expect(safeCanonicalUrl("boss", "https://www.zhipin.com/job_detail/abc.html"))
      .toBe("https://www.zhipin.com/job_detail/abc.html");
    expect(safeCanonicalUrl("boss", "http://www.zhipin.com/job_detail/abc.html")).toBeNull();
    expect(safeCanonicalUrl("boss", "https://www.zhaopin.com/jobdetail/abc.htm")).toBeNull();

    // 智联：zhaopin 域名 + /jobdetail/<id>.htm；query/fragment 被剥离。
    expect(safeCanonicalUrl("zhilian", "https://www.zhaopin.com/jobdetail/abc.htm?utm=x#y"))
      .toBe("https://www.zhaopin.com/jobdetail/abc.htm");
    expect(safeCanonicalUrl("zhilian", "https://www.zhipin.com/job_detail/abc.html")).toBeNull();
    expect(safeCanonicalUrl("zhilian", "https://www.zhaopin.com/sou/abc.htm")).toBeNull();

    // 空值与非法 URL 安全返回 null，不抛异常。
    expect(safeCanonicalUrl("boss", "")).toBeNull();
    expect(safeCanonicalUrl("boss", "not a url")).toBeNull();
  });
});
