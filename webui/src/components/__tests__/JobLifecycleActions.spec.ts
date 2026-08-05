// Task 007：岗位详情生命周期控件（JobLifecycleActions）组件测试。
// 不变式锚点：specs/002-job-feedback-reminders/contracts/http-api.md（job-feedback-v1）、
// contracts/ui-interaction.md `JobLifecycleActions` 节、data-model.md 岗位身份解析节。
import { flushPromises, mount } from "@vue/test-utils";
import JobLifecycleActions from "../JobLifecycleActions.vue";
import type { JobItem } from "../../types";
import type { JobLifecycleStateSnapshot, JobLifecycleStatus } from "../../jobFeedback";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

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
  respond: (url: string, init?: RequestInit) => Response | Promise<Response>;
}

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

function snapshot(revision: number, status: JobLifecycleStatus = "read", appliedAt: string | null = "2026-06-01T02:00:00+00:00"): JobLifecycleStateSnapshot {
  return {
    profile_id: "p1",
    job_id: "j1",
    status,
    applied_at: appliedAt,
    last_follow_up_at: null,
    revision,
    reminder: {
      eligible: false,
      baseline_at: appliedAt ?? "2026-06-01T02:00:00+00:00",
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
    state: snapshot(5, "applied"),
    ...overrides,
  };
}

function errorBody(errorCode: string, userMessage: string) {
  return { ok: false, error_code: errorCode, user_message: userMessage, details: {} };
}

function jobFixture(overrides: Partial<JobItem> = {}): JobItem {
  return {
    job_id: "j1",
    platform: "boss",
    platform_job_id: "p-1",
    canonical_url: "https://www.zhipin.com/job_detail/p-1.html",
    title: "Python 后端工程师",
    company: "示例公司",
    jd: "岗位职责：负责 Python 后端开发。",
    ...overrides,
  };
}

function mountComponent(job: Partial<JobItem> = {}, profileId = "p1") {
  return mount(JobLifecycleActions, {
    props: { profileId, job: jobFixture(job) },
  });
}

/** 需要验证真实焦点行为时，把组件挂载进 document.body（jsdom 对未入档元素 focus 无效）。 */
function mountAttached(job: Partial<JobItem> = {}, profileId = "p1") {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const wrapper = mount(JobLifecycleActions, {
    props: { profileId, job: jobFixture(job) },
    attachTo: host,
  });
  const cleanup = () => {
    wrapper.unmount();
    host.remove();
  };
  return { wrapper, cleanup };
}

function stateHandler(state: JobLifecycleStateSnapshot | null, extra: Record<string, unknown> = {}): RouteHandler {
  return {
    pattern: /^\/api\/profile-jobs\/state/,
    respond: () => jsonResponse({
      ok: true,
      exists: Boolean(state),
      state,
      job_id: state?.job_id ?? null,
      ...extra,
    }),
  };
}

function actionsHandler(receipt: unknown): RouteHandler {
  return {
    pattern: /^\/api\/profile-jobs\/actions$/,
    respond: () => jsonResponse(receipt),
  };
}

function actionCalls(calls: RecordedCall[]): RecordedCall[] {
  return calls.filter((call) => call.url.endsWith("/actions"));
}

function stateCalls(calls: RecordedCall[]): RecordedCall[] {
  return calls.filter((call) => call.url.startsWith("/api/profile-jobs/state"));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// 本文件引用的 api 模块实例可能未被 setup.ts 验证过（vitest 模块实例隔离），逐用例重新验证
beforeEach(() => {
  setBuildIdentity(expectedBackendBuildHash);
});

describe("T055 只读初始加载", () => {
  it("详情变化时 GET state，打开详情绝不发送 action（无自动 mark_read）", async () => {
    const { calls } = installFetch([stateHandler(snapshot(4))]);
    const wrapper = mountComponent();
    await flushPromises();

    const reads = stateCalls(calls);
    expect(reads).toHaveLength(1);
    expect(reads[0].method).toBe("GET");
    expect(reads[0].url).toBe("/api/profile-jobs/state?profile_id=p1&job_id=j1");
    // 只查看不得标记已读：任何写请求都不允许。
    expect(actionCalls(calls)).toHaveLength(0);
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已读");
  });

  it("岗位切换时重新读取 state，仍然不发送 action", async () => {
    const { calls } = installFetch([stateHandler(snapshot(4))]);
    const wrapper = mountComponent();
    await flushPromises();

    await wrapper.setProps({ job: jobFixture({ job_id: "j2", platform_job_id: "p-2" }) });
    await flushPromises();

    const reads = stateCalls(calls);
    expect(reads).toHaveLength(2);
    expect(reads[1].url).toBe("/api/profile-jobs/state?profile_id=p1&job_id=j2");
    expect(actionCalls(calls)).toHaveLength(0);
  });

  it("加载中显示读取状态，加载失败显示真实文案并可重试", async () => {
    let attempt = 0;
    const { calls } = installFetch([
      {
        pattern: /^\/api\/profile-jobs\/state/,
        respond: () => {
          attempt += 1;
          if (attempt === 1) return jsonResponse(errorBody("not_found", "岗位不存在"), 404);
          return jsonResponse({ ok: true, exists: true, state: snapshot(4) });
        },
      },
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    expect(wrapper.get('[data-testid="lca-load-error"]').text()).toContain("岗位不存在");

    await wrapper.get('[data-testid="lca-load-retry"]').trigger("click");
    await flushPromises();

    expect(stateCalls(calls)).toHaveLength(2);
    expect(wrapper.find('[data-testid="lca-load-error"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已读");
  });

  it("profile 为空时不发起任何请求", async () => {
    const { calls } = installFetch([]);
    mountComponent({}, "");
    await flushPromises();
    expect(calls.filter((call) => call.url !== "/api/session")).toHaveLength(0);
  });
});

describe("T056/T057 身份 payload 与主命令矩阵", () => {
  it("已有内部 ID：写请求 body.job 只含 job_id，成功采用服务端 state 并发出变更事件", async () => {
    const { calls } = installFetch([
      stateHandler(snapshot(4)),
      actionsHandler(actionReceipt()),
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    await wrapper.get('[data-testid="lca-action-mark_applied"]').trigger("click");
    await flushPromises();

    const writes = actionCalls(calls);
    expect(writes).toHaveLength(1);
    const body = writes[0].body as Record<string, unknown>;
    expect(body.action).toBe("mark_applied");
    expect(body.job).toEqual({ job_id: "j1" });
    expect(body.profile_id).toBe("p1");
    // 成功只采用响应 state。
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已投递");
    expect(wrapper.emitted("job-feedback-changed")).toEqual([
      [{ profileId: "p1", jobId: "j1" }],
    ]);
  });

  it("未入库 pipeline 岗位：写命令携带完整权威三元组，成功后缓存并使用服务端内部 ID", async () => {
    const { calls } = installFetch([
      stateHandler(null, { job_id: "internal-1" }),
      {
        pattern: /^\/api\/profile-jobs\/actions$/,
        respond: (_url, init) => {
          const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
          const job = body.job as Record<string, unknown>;
          // 第一次用三元组写入；成功后服务端给出内部 ID，第二次必须只用内部 ID。
          if ("job_id" in job) return jsonResponse(actionReceipt({ state: snapshot(6, "applied") }));
          return jsonResponse(actionReceipt({
            state: { ...snapshot(5, "applied"), job_id: "internal-1" },
          }));
        },
      },
    ]);
    const wrapper = mountComponent({ job_id: undefined });
    await flushPromises();

    await wrapper.get('[data-testid="lca-action-mark_applied"]').trigger("click");
    await flushPromises();
    const firstBody = actionCalls(calls)[0].body as Record<string, unknown>;
    expect(firstBody.job).toMatchObject({
      platform: "boss",
      platform_job_id: "p-1",
      canonical_url: "https://www.zhipin.com/job_detail/p-1.html",
      jd: "岗位职责：负责 Python 后端开发。",
    });
    expect(firstBody.job).not.toHaveProperty("job_id");
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已投递");

    // 已投递 → 记录跟进；此时身份必须是缓存的内部 ID。
    await wrapper.get('[data-testid="lca-action-follow_up"]').trigger("click");
    await flushPromises();
    const secondBody = actionCalls(calls)[1].body as Record<string, unknown>;
    expect(secondBody.job).toEqual({ job_id: "internal-1" });
  });

  it("主命令按当前状态选择：read→已投递/已读；applied→跟进/荒废；stale→恢复", async () => {
    installFetch([stateHandler(snapshot(4, "read"))]);
    const readWrapper = mountComponent();
    await flushPromises();
    expect(readWrapper.find('[data-testid="lca-action-mark_read"]').exists()).toBe(false);
    expect(readWrapper.find('[data-testid="lca-action-mark_applied"]').exists()).toBe(true);
    await readWrapper.unmount();

    installFetch([stateHandler(snapshot(4, "applied"))]);
    const appliedWrapper = mountComponent();
    await flushPromises();
    expect(appliedWrapper.find('[data-testid="lca-action-follow_up"]').exists()).toBe(true);
    expect(appliedWrapper.find('[data-testid="lca-action-mark_stale"]').exists()).toBe(true);
    expect(appliedWrapper.find('[data-testid="lca-action-mark_applied"]').exists()).toBe(false);
    await appliedWrapper.unmount();

    installFetch([stateHandler(snapshot(4, "stale"))]);
    const staleWrapper = mountComponent();
    await flushPromises();
    expect(staleWrapper.find('[data-testid="lca-action-restore_applied"]').exists()).toBe(true);
    expect(staleWrapper.find('[data-testid="lca-action-follow_up"]').exists()).toBe(false);
  });

  it("follow_up / mark_stale / restore_applied 各自按合同 action 提交", async () => {
    const { calls } = installFetch([
      stateHandler(snapshot(4, "applied")),
      actionsHandler(actionReceipt()),
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    await wrapper.get('[data-testid="lca-action-follow_up"]').trigger("click");
    await flushPromises();
    expect((actionCalls(calls)[0].body as Record<string, unknown>).action).toBe("follow_up");
    // follow_up 不接受无关字段。
    expect((actionCalls(calls)[0].body as Record<string, unknown>).applied_at).toBeNull();
    expect((actionCalls(calls)[0].body as Record<string, unknown>).target_status).toBeNull();
  });
});

describe("T058 纠正表单与缺失投递时间补录", () => {
  it("已投递缺失投递时间：显示补录入口与不可计算提醒，补录走 correct_applied_at", async () => {
    const { calls } = installFetch([
      stateHandler(snapshot(4, "applied", null)),
      actionsHandler(actionReceipt({ state: snapshot(5, "applied", "2026-06-01T02:00:00+00:00") })),
    ]);
    const { wrapper, cleanup } = mountAttached();
    await flushPromises();

    const missing = wrapper.get('[data-testid="lca-missing-applied-at"]');
    expect(missing.text()).toContain("提醒资格无法计算");

    await wrapper.get('[data-testid="lca-fill-applied-at"]').trigger("click");
    await flushPromises();
    // 补录入口直接聚焦时间控件，目标状态锁定当前 applied。
    expect(document.activeElement).toBe(wrapper.get('[data-testid="lca-correction-time"]').element);
    expect((wrapper.get('[data-testid="lca-correction-status"]').element as HTMLSelectElement).value).toBe("applied");

    await wrapper.get('[data-testid="lca-correction-time"]').setValue("2026-06-01T10:00");
    await wrapper.get('[data-testid="lca-correction-submit"]').trigger("submit");
    await flushPromises();

    const body = actionCalls(calls)[0].body as Record<string, unknown>;
    expect(body.action).toBe("correct_applied_at");
    // datetime-local 无时区：必须补上本地 offset 形成 RFC 3339 带时区时刻。
    expect(String(body.applied_at)).toMatch(/^2026-06-01T10:00:00[+-]\d{2}:\d{2}$/);
    cleanup();
  });

  it("纠正状态走 correct_status：目标状态必填，可附带投递时间", async () => {
    const { calls } = installFetch([
      stateHandler(snapshot(4, "read")),
      actionsHandler(actionReceipt({ state: snapshot(5, "stale") })),
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    await wrapper.get('[data-testid="lca-open-correction"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="lca-correction-status"]').setValue("stale");
    await wrapper.get('[data-testid="lca-correction-submit"]').trigger("submit");
    await flushPromises();

    const body = actionCalls(calls)[0].body as Record<string, unknown>;
    expect(body.action).toBe("correct_status");
    expect(body.target_status).toBe("stale");
    // 非 applied 目标不携带 applied_at，避免服务端 invalid_action_payload。
    expect(body.applied_at).toBeNull();
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已荒废");
  });

  it("目标 applied 且无历史投递时间时必须填写投递时间", async () => {
    // read 状态且无历史投递时间：目标 applied 缺时间必须被客户端阻断。
    const { calls } = installFetch([stateHandler(snapshot(4, "read", null))]);
    const wrapper = mountComponent();
    await flushPromises();

    await wrapper.get('[data-testid="lca-open-correction"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="lca-correction-status"]').setValue("applied");
    await wrapper.get('[data-testid="lca-correction-submit"]').trigger("submit");
    await flushPromises();

    expect(wrapper.get('[data-testid="lca-correction-error"]').text()).toContain("必须填写投递时间");
    expect(actionCalls(calls)).toHaveLength(0);
  });

  it("未来时间在客户端被预警且不发送请求；无时区控件的非法值被 DOM 归一为空后仍按缺时间规则阻断", async () => {
    // applied 但无历史投递时间：未来时间预警 + 缺时间阻断两条路径都能覆盖。
    const { calls } = installFetch([stateHandler(snapshot(4, "applied", null))]);
    const wrapper = mountComponent();
    await flushPromises();

    await wrapper.get('[data-testid="lca-open-correction"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="lca-correction-time"]').setValue("2999-01-01T00:00");
    await wrapper.get('[data-testid="lca-correction-submit"]').trigger("submit");
    await flushPromises();
    expect(wrapper.get('[data-testid="lca-correction-error"]').text()).toContain("不能晚于当前时刻");

    // datetime-local 不接受任意文本：DOM 会把非法值归一为空字符串。
    // 组件按“缺时间 + 目标 applied 且无历史投递时间”规则继续阻断，不伪造时刻。
    await wrapper.get('[data-testid="lca-correction-status"]').setValue("applied");
    const timeInput = wrapper.get('[data-testid="lca-correction-time"]').element as HTMLInputElement;
    timeInput.value = "not-a-date";
    await wrapper.get('[data-testid="lca-correction-time"]').trigger("input");
    expect(timeInput.value).toBe("");
    await wrapper.get('[data-testid="lca-correction-submit"]').trigger("submit");
    await flushPromises();
    expect(wrapper.get('[data-testid="lca-correction-error"]').text()).toContain("必须填写投递时间");
    expect(actionCalls(calls)).toHaveLength(0);
  });
});

describe("T059 幂等、写锁、revision 防倒退与失败保留", () => {
  it("不确定网络失败自动重试复用同一 request_id，成功后采用 state", async () => {
    const seenRequestIds: string[] = [];
    let attempts = 0;
    installFetch([
      stateHandler(snapshot(4)),
      {
        pattern: /^\/api\/profile-jobs\/actions$/,
        respond: (_url, init) => {
          attempts += 1;
          const requestId = String((JSON.parse(String(init?.body)) as Record<string, unknown>).request_id);
          seenRequestIds.push(requestId);
          if (attempts === 1) throw new TypeError("network timeout");
          return jsonResponse(actionReceipt());
        },
      },
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    await wrapper.get('[data-testid="lca-action-mark_applied"]').trigger("click");
    await flushPromises();

    // 重试复用同一 ID，且最终成功无错误文案。
    expect(seenRequestIds).toHaveLength(2);
    expect(seenRequestIds[0]).toBe(seenRequestIds[1]);
    expect(wrapper.find('[data-testid="lca-action-error"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已投递");
  });

  it("确定性失败保留原状态并显示 API 文案；用户再次点击生成新 request_id", async () => {
    const seenRequestIds: string[] = [];
    let attempts = 0;
    installFetch([
      stateHandler(snapshot(4, "applied")),
      {
        pattern: /^\/api\/profile-jobs\/actions$/,
        respond: (_url, init) => {
          attempts += 1;
          seenRequestIds.push(String((JSON.parse(String(init?.body)) as Record<string, unknown>).request_id));
          if (attempts === 1) {
            return jsonResponse(errorBody("state_precondition_failed", "前置状态不满足，请刷新后重试"), 409);
          }
          return jsonResponse(actionReceipt());
        },
      },
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    await wrapper.get('[data-testid="lca-action-follow_up"]').trigger("click");
    await flushPromises();

    // 失败：保留原状态/时间，显示 API user_message，不发变更事件。
    expect(wrapper.get('[data-testid="lca-action-error"]').text()).toContain("前置状态不满足");
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已投递");
    expect(wrapper.emitted("job-feedback-changed")).toBeUndefined();

    // 用户明确再次确认 → 新 request_id，成功后正常采用 state。
    await wrapper.get('[data-testid="lca-action-follow_up"]').trigger("click");
    await flushPromises();
    expect(seenRequestIds).toHaveLength(2);
    expect(seenRequestIds[1]).not.toBe(seenRequestIds[0]);
    expect(wrapper.emitted("job-feedback-changed")).toHaveLength(1);
  });

  it("低 revision 的陈旧响应不得覆盖较新 state（防倒退）", async () => {
    installFetch([
      stateHandler(snapshot(5, "read")),
      actionsHandler(actionReceipt({ state: snapshot(3, "applied") })),
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    await wrapper.get('[data-testid="lca-action-mark_applied"]').trigger("click");
    await flushPromises();

    // 响应 revision=3 低于已知 revision=5：保留原状态。
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已读");
  });

  it("写请求期间切换岗位：旧响应晚到被丢弃，不采用 state、不发事件、不显示旧错误", async () => {
    let release!: (response: Response) => void;
    const gate = new Promise<Response>((resolve) => { release = resolve; });
    installFetch([
      stateHandler(snapshot(4)),
      { pattern: /^\/api\/profile-jobs\/actions$/, respond: () => gate },
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    void wrapper.get('[data-testid="lca-action-mark_applied"]').trigger("click").then(() => flushPromises());
    await flushPromises();

    // 在途写期间切换到另一个岗位：state 重置并重新只读加载。
    await wrapper.setProps({ job: jobFixture({ job_id: "j2", platform_job_id: "p-2" }) });
    await flushPromises();

    // 旧岗位的写响应晚到：rev9 applied 不得覆盖新岗位的 rev4 read。
    release(jsonResponse(actionReceipt({ state: snapshot(9, "applied") })));
    await flushPromises();

    expect(wrapper.emitted("job-feedback-changed")).toBeUndefined();
    expect(wrapper.get('[data-testid="lca-current-status"]').text()).toBe("已读");
    expect(wrapper.find('[data-testid="lca-action-error"]').exists()).toBe(false);
  });

  it("写请求期间锁定同一岗位的全部生命周期按钮", async () => {
    let release!: (response: Response) => void;
    const gate = new Promise<Response>((resolve) => { release = resolve; });
    installFetch([
      stateHandler(snapshot(4)),
      { pattern: /^\/api\/profile-jobs\/actions$/, respond: () => gate },
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    void wrapper.get('[data-testid="lca-action-mark_applied"]').trigger("click").then(() => flushPromises());
    await flushPromises();

    for (const testId of ["lca-action-mark_applied", "lca-open-correction"]) {
      expect((wrapper.get(`[data-testid="${testId}"]`).element as HTMLButtonElement).disabled).toBe(true);
    }

    release(jsonResponse(actionReceipt()));
    await flushPromises();
    expect((wrapper.get('[data-testid="lca-open-correction"]').element as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("T060 events 按需展开与 sequence 分页", () => {
  function makeEvent(sequence: number) {
    return {
      sequence,
      id: `event-${sequence}`,
      action: "follow_up",
      from_status: "applied",
      to_status: "applied",
      from_applied_at: "2026-06-01T02:00:00+00:00",
      to_applied_at: "2026-06-01T02:00:00+00:00",
      from_last_follow_up_at: null,
      to_last_follow_up_at: "2026-07-01T02:00:00+00:00",
      occurred_at: "2026-07-01T02:00:00+00:00",
    };
  }

  it("展开才加载 events；按 next_after_sequence 分页，末页后隐藏加载更多", async () => {
    const { calls } = installFetch([
      stateHandler(snapshot(4, "applied")),
      {
        pattern: /\/events/,
        respond: (url) => {
          const afterSequence = Number(new URL(`https://local${url}`).searchParams.get("after_sequence"));
          if (afterSequence === 0) {
            const events = Array.from({ length: 20 }, (_, index) => makeEvent(index + 1));
            return jsonResponse({ ok: true, events, next_after_sequence: 20 });
          }
          const events = Array.from({ length: 3 }, (_, index) => makeEvent(21 + index));
          return jsonResponse({ ok: true, events, next_after_sequence: 23 });
        },
      },
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    // 未展开时不加载任何事件。
    expect(calls.filter((call) => call.url.includes("/events"))).toHaveLength(0);

    await wrapper.get('[data-testid="lca-toggle-events"]').trigger("click");
    await flushPromises();
    const eventCalls = calls.filter((call) => call.url.includes("/events"));
    expect(eventCalls).toHaveLength(1);
    expect(eventCalls[0].url).toBe("/api/profile-jobs/p1/j1/events?after_sequence=0&limit=20");
    expect(wrapper.findAll('[data-testid="lca-event-list"] li')).toHaveLength(20);

    await wrapper.get('[data-testid="lca-load-more-events"]').trigger("click");
    await flushPromises();
    expect(calls.filter((call) => call.url.includes("/events"))[1].url)
      .toBe("/api/profile-jobs/p1/j1/events?after_sequence=20&limit=20");
    expect(wrapper.findAll('[data-testid="lca-event-list"] li')).toHaveLength(23);
    expect(wrapper.find('[data-testid="lca-load-more-events"]').exists()).toBe(false);
  });
});

describe("T061 身份不完整阻断与安全 URL", () => {
  it("身份不完整：阻断写操作并提示，不发起任何请求，不从 UI 平台补值", async () => {
    const { calls } = installFetch([]);
    const wrapper = mount(JobLifecycleActions, {
      props: { profileId: "p1", job: { title: "只有标题的岗位" } },
    });
    await flushPromises();

    expect(wrapper.get('[data-testid="lca-identity-blocked"]').text()).toContain("岗位身份未可靠入库");
    expect(wrapper.find('[data-testid="lca-commands"]').exists()).toBe(false);
    // 既不发读请求，也不发写请求。
    expect(calls.filter((call) => call.url !== "/api/session")).toHaveLength(0);
  });

  it("安全 URL：非法/跨平台链接禁用跳转并提示；合法链接正常渲染", async () => {
    installFetch([stateHandler(snapshot(4))]);
    const unsafeWrapper = mountComponent({ canonical_url: "javascript:alert(1)" });
    await flushPromises();
    expect(unsafeWrapper.find('[data-testid="lca-open-original"]').exists()).toBe(false);
    expect(unsafeWrapper.find('[data-testid="lca-url-unavailable"]').exists()).toBe(true);
    await unsafeWrapper.unmount();

    installFetch([stateHandler(snapshot(4))]);
    const httpWrapper = mountComponent({ canonical_url: "http://www.zhipin.com/job_detail/p-1.html" });
    await flushPromises();
    expect(httpWrapper.find('[data-testid="lca-open-original"]').exists()).toBe(false);
    await httpWrapper.unmount();

    installFetch([stateHandler(snapshot(4))]);
    const safeWrapper = mountComponent({ canonical_url: "https://jobs.zhipin.com/job_detail/p-1.html" });
    await flushPromises();
    const link = safeWrapper.get('[data-testid="lca-open-original"]');
    expect(link.attributes("href")).toBe("https://jobs.zhipin.com/job_detail/p-1.html");
    expect(link.attributes("rel")).toBe("noopener noreferrer");
  });
});

describe("T062 可达性、焦点与布局结构", () => {
  it("打开纠正表单后焦点进入目标状态选择器；取消后表单移除", async () => {
    installFetch([stateHandler(snapshot(4))]);
    const { wrapper, cleanup } = mountAttached();
    await flushPromises();

    await wrapper.get('[data-testid="lca-open-correction"]').trigger("click");
    await flushPromises();
    expect(document.activeElement).toBe(wrapper.get('[data-testid="lca-correction-status"]').element);

    await wrapper.get('[data-testid="lca-correction-cancel"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="lca-correction-form"]').exists()).toBe(false);
    cleanup();
  });

  it("所有控件为真实 button、错误使用 role=alert、命令区可换行不产生横向溢出结构", async () => {
    installFetch([
      stateHandler(snapshot(4)),
      {
        pattern: /^\/api\/profile-jobs\/actions$/,
        respond: () => jsonResponse(errorBody("applied_at_in_future", "投递时间不能晚于当前时刻"), 422),
      },
    ]);
    const wrapper = mountComponent();
    await flushPromises();

    // 命令区使用可换行容器（scoped CSS flex-wrap），窄屏不横向溢出。
    const commands = wrapper.get('[data-testid="lca-commands"]');
    expect(commands.classes()).toContain("lca-commands");
    for (const button of commands.findAll("button")) {
      expect(button.attributes("type")).toBe("button");
      expect((button.element as HTMLButtonElement).disabled).toBe(false);
    }

    // 失败文案以 role=alert 呈现并可读（word-break 由 scoped CSS 保证）。
    await wrapper.get('[data-testid="lca-action-mark_applied"]').trigger("click");
    await flushPromises();
    const error = wrapper.get('[data-testid="lca-action-error"]');
    expect(error.attributes("role")).toBe("alert");
    expect(error.text()).toContain("投递时间不能晚于当前时刻");
  });
});
