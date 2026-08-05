// Task 006：提醒抽屉组件合同测试。
// 不变式锚点：specs/002-job-feedback-reminders/contracts/ui-interaction.md
// （ReminderDrawer 节、跳转安全、视口验收）、http-api.md（job-reminders/actions/advice）。
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { flushPromises, mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import ReminderDrawer from "../ReminderDrawer.vue";
import type { JobReminderItem } from "../../jobFeedback";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

// 本文件引用的 api 模块实例可能未被 setup.ts 验证过（vitest 模块实例隔离），逐用例重新验证
beforeEach(() => {
  setBuildIdentity(expectedBackendBuildHash);
});

// ---------------------------------------------------------------------------
// 测试基建：fetch mock 与提醒项工厂
// ---------------------------------------------------------------------------

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function errorBody(errorCode: string, userMessage: string) {
  return { ok: false, error_code: errorCode, user_message: userMessage, details: {} };
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

function reminderItem(overrides: Partial<JobReminderItem> = {}): JobReminderItem {
  return {
    job_id: "j1",
    platform: "boss",
    platform_job_id: "platform-1",
    title: "Python 后端工程师",
    company: "示例公司",
    salary: "20-30K",
    location: "上海",
    canonical_url: "https://www.zhipin.com/job_detail/platform-1.html",
    status: "applied",
    applied_at: "2026-05-01T02:00:00+00:00",
    last_follow_up_at: null,
    baseline_at: "2026-05-01T02:00:00+00:00",
    elapsed_seconds: 8294400,
    elapsed_days: 96,
    can_open: true,
    ...overrides,
  };
}

function listBody(items: JobReminderItem[], total = items.length) {
  return { ok: true, profile_id: "p1", threshold_hours: 720, total, items };
}

type DrawerWrapper = VueWrapper<InstanceType<typeof ReminderDrawer>>;

function mountDrawer(
  props: { open?: boolean; profileId?: string | null } = {},
): DrawerWrapper {
  return mount(ReminderDrawer, {
    props: { open: props.open ?? true, profileId: props.profileId ?? "p1" },
    attachTo: document.body,
  });
}

function listItem(wrapper: DrawerWrapper, jobId: string) {
  return wrapper.findAll('[data-testid="reminder-item"]')
    .find((node) => node.attributes("data-job-id") === jobId);
}

const wrappers: DrawerWrapper[] = [];
function tracked(wrapper: DrawerWrapper): DrawerWrapper {
  wrappers.push(wrapper);
  return wrapper;
}

afterEach(() => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// T046 loading / error / empty / populated / 旧数据保留与重试
// ---------------------------------------------------------------------------

describe("T046 抽屉状态：loading/error/empty/populated/旧数据保留/重试", () => {
  it("loading：抽屉尺寸稳定、列表区显示加载状态、关闭按钮可用", async () => {
    installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => new Promise<Response>(() => {}) },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    expect(wrapper.find('[data-testid="reminder-drawer"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="reminder-loading"]').text()).toContain("正在加载");
    const close = wrapper.get('[data-testid="reminder-close"]');
    expect(close.attributes("disabled")).toBeUndefined();
    expect(close.attributes("aria-label")).toBe("关闭提醒抽屉");
    // loading 期间不出现列表或空状态
    expect(wrapper.find('[data-testid="reminder-list"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="reminder-empty"]').exists()).toBe(false);
  });

  it("error：显示真实失败文案与重试按钮，重试成功后恢复 populated", async () => {
    let attempt = 0;
    const { calls } = installFetch([
      {
        pattern: /^\/api\/job-reminders\?/,
        respond: () => {
          attempt += 1;
          if (attempt === 1) {
            return jsonResponse(errorBody("persistence_failed", "数据库暂时不可用，请稍后重试"), 500);
          }
          return jsonResponse(listBody([reminderItem()]));
        },
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    const error = wrapper.get('[data-testid="reminder-error"]');
    expect(error.text()).toContain("数据库暂时不可用，请稍后重试");
    expect(wrapper.find('[data-testid="reminder-list"]').exists()).toBe(false);

    await wrapper.get('[data-testid="reminder-retry"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="reminder-error"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="reminder-list"]').exists()).toBe(true);
    expect(calls.filter((call) => call.url.startsWith("/api/job-reminders?"))).toHaveLength(2);
  });

  it("empty：显示“当前没有需要跟进的岗位”，不暗示查看已清除", async () => {
    installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => jsonResponse(listBody([], 0)) },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    const empty = wrapper.get('[data-testid="reminder-empty"]');
    expect(empty.text()).toBe("当前没有需要跟进的岗位");
    expect(empty.text()).not.toContain("已清除");
  });

  it("populated：真实 total、最长未活动优先按服务端顺序渲染、不发 platform 参数", async () => {
    const items = [
      reminderItem({ job_id: "j-old", elapsed_days: 120, title: "最久未活动" }),
      reminderItem({ job_id: "j-mid", elapsed_days: 60, title: "中间" }),
      reminderItem({ job_id: "j-new", elapsed_days: 31, title: "刚逾期" }),
    ];
    const { calls } = installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => jsonResponse(listBody(items, 3)) },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    expect(wrapper.get('[data-testid="reminder-total"]').text()).toContain("共 3 个逾期岗位");
    const rendered = wrapper.findAll('[data-testid="reminder-item"]');
    expect(rendered.map((node) => node.attributes("data-job-id")))
      .toEqual(["j-old", "j-mid", "j-new"]);
    const listCall = calls.find((call) => call.url.startsWith("/api/job-reminders?"));
    expect(listCall?.url).toBe("/api/job-reminders?profile_id=p1");
    expect(listCall?.url.includes("platform")).toBe(false);
  });

  it("total 超过 100 时：显示真实 total、渲染服务端返回的最多 100 条并给出上限说明", async () => {
    const items = Array.from({ length: 100 }, (_, index) => reminderItem({
      job_id: `job-${index}`,
      elapsed_days: 200 - index,
      title: `岗位 ${index}`,
    }));
    installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => jsonResponse(listBody(items, 137)) },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    expect(wrapper.get('[data-testid="reminder-total"]').text()).toContain("共 137 个逾期岗位");
    expect(wrapper.findAll('[data-testid="reminder-item"]')).toHaveLength(100);
    const note = wrapper.get('[data-testid="reminder-limit-note"]');
    expect(note.text()).toContain("已显示前 100 条");
    expect(note.text()).toContain("共 137 条逾期");
  });

  it("每项显示岗位名/公司/薪资/地点/来源标签/未活动天数；可空字段缺失时不渲染", async () => {
    installFetch([
      {
        pattern: /^\/api\/job-reminders\?/,
        respond: () => jsonResponse(listBody([
          reminderItem({ job_id: "j-full", platform: "boss" }),
          reminderItem({
            job_id: "j-sparse",
            platform: "zhilian",
            company: null,
            salary: null,
            location: null,
            canonical_url: null,
            can_open: false,
            elapsed_days: 45,
          }),
        ])),
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    const full = listItem(wrapper, "j-full")!;
    expect(full.text()).toContain("Python 后端工程师");
    expect(full.text()).toContain("示例公司");
    expect(full.text()).toContain("20-30K");
    expect(full.text()).toContain("上海");
    expect(full.get('[data-platform="boss"]').text()).toBe("BOSS");
    expect(full.get('[data-testid="reminder-days"]').text()).toContain("96 天");

    const sparse = listItem(wrapper, "j-sparse")!;
    expect(sparse.get('[data-platform="zhilian"]').text()).toBe("智联");
    expect(sparse.text()).toContain("45 天");
    expect(sparse.text()).not.toContain("示例公司");
    expect(sparse.text()).not.toContain("20-30K");
    expect(sparse.text()).not.toContain("上海");
  });

  it("有旧数据时刷新失败：保留旧数据并标记，重试成功后清除旧数据标记", async () => {
    let fail = false;
    let revision = 0;
    const { calls } = installFetch([
      {
        pattern: /^\/api\/job-reminders\?/,
        respond: () => {
          if (fail) return jsonResponse(errorBody("unknown_error", "网络请求失败"), 500);
          revision += 1;
          return jsonResponse(listBody([reminderItem({ job_id: `j-r${revision}` })]));
        },
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();
    expect(listItem(wrapper, "j-r1")?.exists()).toBe(true);

    // 关闭再打开触发非静默刷新；此时服务端失败。
    fail = true;
    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    await flushPromises();

    // 旧数据保留且被明确标记，错误文案真实。
    expect(wrapper.get('[data-testid="reminder-error"]').text()).toContain("网络请求失败");
    expect(wrapper.find('[data-testid="reminder-stale-note"]').exists()).toBe(true);
    expect(listItem(wrapper, "j-r1")?.exists()).toBe(true);

    // 重试成功：列表换成服务端新结果，旧数据标记消失。
    fail = false;
    await wrapper.get('[data-testid="reminder-retry"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="reminder-stale-note"]').exists()).toBe(false);
    expect(listItem(wrapper, "j-r1")).toBeUndefined();
    expect(listItem(wrapper, "j-r2")?.exists()).toBe(true);
    expect(calls.filter((call) => call.url.startsWith("/api/job-reminders?"))).toHaveLength(3);
  });
});

// ---------------------------------------------------------------------------
// T047 尺寸稳定、标题、关闭、Escape、打开焦点与关闭焦点恢复
// ---------------------------------------------------------------------------

describe("T047 焦点与键盘", () => {
  it("打开后焦点进入抽屉（关闭按钮），Escape 关闭，关闭后焦点回到触发元素", async () => {
    installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => jsonResponse(listBody([])) },
    ]);
    const trigger = document.createElement("button");
    trigger.textContent = "提醒入口";
    document.body.appendChild(trigger);
    trigger.focus();

    const wrapper = tracked(mountDrawer());
    await flushPromises();
    expect(document.activeElement).toBe(wrapper.get('[data-testid="reminder-close"]').element);

    await wrapper.get("aside").trigger("keydown", { key: "Escape" });
    expect(wrapper.emitted("close")).toBeTruthy();

    await wrapper.setProps({ open: false });
    await flushPromises();
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });
});

// ---------------------------------------------------------------------------
// T049/T050 逐项 busy、refresh 驱动退出、失败保留原项
// ---------------------------------------------------------------------------

describe("T050 快捷动作：逐项 busy、服务端刷新决定去留、失败保留", () => {
  function makeActionItem(jobId: string): JobReminderItem {
    return reminderItem({ job_id: jobId, platform_job_id: `platform-${jobId}` });
  }

  it("进行中只锁目标项的动作按钮；其它项与建议按钮可用；不提前本地删除", async () => {
    let currentItems = [makeActionItem("j1"), makeActionItem("j2")];
    let resolveAction: ((response: Response) => void) | null = null;
    const { calls } = installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => jsonResponse(listBody(currentItems)) },
      {
        pattern: /^\/api\/profile-jobs\/actions$/,
        respond: () => new Promise<Response>((resolve) => { resolveAction = resolve; }),
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    await listItem(wrapper, "j1")!.get('[data-testid="btn-follow-up"]').trigger("click");
    await flushPromises();

    const item1 = listItem(wrapper, "j1")!;
    const item2 = listItem(wrapper, "j2")!;
    // 只锁 j1 的生命周期按钮；advice 不被锁。
    expect(item1.get('[data-testid="btn-follow-up"]').attributes("disabled")).toBeDefined();
    expect(item1.get('[data-testid="btn-mark-stale"]').attributes("disabled")).toBeDefined();
    expect(item1.get('[data-testid="btn-advice"]').attributes("disabled")).toBeUndefined();
    // 其它项完全不受影响。
    expect(item2.get('[data-testid="btn-follow-up"]').attributes("disabled")).toBeUndefined();
    expect(item2.get('[data-testid="btn-mark-stale"]').attributes("disabled")).toBeUndefined();
    // 成功响应到达前不本地删除任何项。
    expect(wrapper.findAll('[data-testid="reminder-item"]')).toHaveLength(2);

    // 服务端确认成功：刷新列表，j1 是否退出由刷新结果决定。
    currentItems = [makeActionItem("j2")];
    resolveAction!(jsonResponse({
      ok: true, replayed: false, changed: true,
      event_id: "event-uuid", event_sequence: 4,
      state: {
        profile_id: "p1", job_id: "j1", status: "applied",
        applied_at: "2026-05-01T02:00:00+00:00",
        last_follow_up_at: "2026-08-05T02:00:00+00:00",
        revision: 5,
        reminder: { eligible: false, baseline_at: "2026-08-05T02:00:00+00:00", elapsed_seconds: 0, elapsed_days: 0 },
      },
    }));
    await flushPromises();

    expect(wrapper.emitted("jobFeedbackChanged")).toBeTruthy();
    expect(wrapper.findAll('[data-testid="reminder-item"]')).toHaveLength(1);
    expect(listItem(wrapper, "j1")).toBeUndefined();
    expect(listItem(wrapper, "j2")?.exists()).toBe(true);

    // 动作请求体：内部 ID 身份 + follow_up + 每次确认新 request_id。
    const actionCall = calls.find((call) => call.url.endsWith("/actions"));
    expect(actionCall?.method).toBe("POST");
    expect(actionCall?.body).toMatchObject({
      profile_id: "p1",
      job: { job_id: "j1" },
      action: "follow_up",
      applied_at: null,
      target_status: null,
    });
    expect(String((actionCall?.body as Record<string, unknown>).request_id))
      .toMatch(/^[0-9a-f-]{36}$/i);
    // 成功后触发了服务端刷新（第二次列表请求）。
    expect(calls.filter((call) => call.url.startsWith("/api/job-reminders?"))).toHaveLength(2);
  });

  it("操作失败：保留原项、显示真实 API 文案、按钮恢复可用", async () => {
    const currentItems = [makeActionItem("j1")];
    installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => jsonResponse(listBody(currentItems)) },
      {
        pattern: /^\/api\/profile-jobs\/actions$/,
        respond: () => jsonResponse(errorBody("state_precondition_failed", "岗位当前状态不允许该操作"), 409),
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    await listItem(wrapper, "j1")!.get('[data-testid="btn-mark-stale"]').trigger("click");
    await flushPromises();

    // 失败不删除、不乐观更新：原项仍在，错误为服务端真实文案。
    const item1 = listItem(wrapper, "j1")!;
    expect(item1.exists()).toBe(true);
    expect(item1.get('[data-testid="item-action-error"]').text())
      .toContain("岗位当前状态不允许该操作");
    expect(item1.get('[data-testid="btn-follow-up"]').attributes("disabled")).toBeUndefined();
    expect(item1.get('[data-testid="btn-mark-stale"]').attributes("disabled")).toBeUndefined();
  });

  it("关闭抽屉期间动作完成：仍发出 jobFeedbackChanged，重新打开后按钮不残留禁用锁", async () => {
    let currentItems = [makeActionItem("j1")];
    let resolveAction: ((response: Response) => void) | null = null;
    const { calls } = installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => jsonResponse(listBody(currentItems)) },
      {
        pattern: /^\/api\/profile-jobs\/actions$/,
        respond: () => new Promise<Response>((resolve) => { resolveAction = resolve; }),
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    await listItem(wrapper, "j1")!.get('[data-testid="btn-follow-up"]').trigger("click");
    await flushPromises();

    // 请求在途时关闭抽屉，随后服务端成功响应到达。
    await wrapper.setProps({ open: false });
    resolveAction!(jsonResponse({
      ok: true, replayed: false, changed: true,
      event_id: "event-uuid", event_sequence: 4,
      state: {
        profile_id: "p1", job_id: "j1", status: "applied",
        applied_at: "2026-05-01T02:00:00+00:00",
        last_follow_up_at: "2026-08-05T02:00:00+00:00",
        revision: 5,
        reminder: { eligible: false, baseline_at: "2026-08-05T02:00:00+00:00", elapsed_seconds: 0, elapsed_days: 0 },
      },
    }));
    await flushPromises();

    // 动作已成功：无论抽屉开闭都必须通知父层刷新徽标；关闭期间不触发静默刷新。
    expect(wrapper.emitted("jobFeedbackChanged")).toHaveLength(1);
    expect(calls.filter((call) => call.url.startsWith("/api/job-reminders?"))).toHaveLength(1);

    // 重新打开：不得残留永久禁用锁。
    await wrapper.setProps({ open: true });
    await flushPromises();
    const reopened = listItem(wrapper, "j1")!;
    expect(reopened.get('[data-testid="btn-follow-up"]').attributes("disabled")).toBeUndefined();
    expect(reopened.get('[data-testid="btn-mark-stale"]').attributes("disabled")).toBeUndefined();
  });

  it("关闭抽屉期间建议完成：重新打开后不残留 loading，建议可再次请求", async () => {
    let resolveAdvice: ((response: Response) => void) | null = null;
    installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => jsonResponse(listBody([makeActionItem("j1")])) },
      {
        pattern: /\/advice$/,
        respond: () => new Promise<Response>((resolve) => { resolveAdvice = resolve; }),
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    await listItem(wrapper, "j1")!.get('[data-testid="btn-advice"]').trigger("click");
    await flushPromises();

    await wrapper.setProps({ open: false });
    resolveAdvice!(jsonResponse({ ok: true, action: "review", reason: "需要人工复核。", source: "rule" }));
    await flushPromises();

    await wrapper.setProps({ open: true });
    await flushPromises();

    const reopened = listItem(wrapper, "j1")!;
    expect(reopened.find('[data-testid="advice-loading"]').exists()).toBe(false);
    expect(reopened.get('[data-testid="btn-advice"]').attributes("disabled")).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// T051 逐项 advice：loading/result/error、AI/规则 source、不自动执行
// ---------------------------------------------------------------------------

describe("T051 按需建议：逐项 loading、来源标签、不改变岗位状态", () => {
  it("advice loading 只出现在该项建议区，且不锁住跟进/荒废按钮", async () => {
    let resolveAdvice: ((response: Response) => void) | null = null;
    const { calls } = installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => jsonResponse(listBody([reminderItem({ job_id: "j1" })])) },
      {
        pattern: /\/advice$/,
        respond: () => new Promise<Response>((resolve) => { resolveAdvice = resolve; }),
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    const item1 = listItem(wrapper, "j1")!;
    await item1.get('[data-testid="btn-advice"]').trigger("click");
    await flushPromises();

    const loading = listItem(wrapper, "j1")!;
    expect(loading.find('[data-testid="advice-loading"]').exists()).toBe(true);
    // AI 请求不锁生命周期动作。
    expect(loading.get('[data-testid="btn-follow-up"]').attributes("disabled")).toBeUndefined();
    expect(loading.get('[data-testid="btn-mark-stale"]').attributes("disabled")).toBeUndefined();
    expect(loading.get('[data-testid="btn-advice"]').attributes("disabled")).toBeDefined();

    resolveAdvice!(jsonResponse({
      ok: true,
      action: "follow_up",
      reason: "已超过 30 天没有新的跟进记录，建议先主动确认进展。",
      source: "ai",
    }));
    await flushPromises();

    const done = listItem(wrapper, "j1")!;
    expect(done.find('[data-testid="advice-loading"]').exists()).toBe(false);
    expect(done.get('[data-testid="advice-result"]').text()).toContain("建议跟进");
    expect(done.get('[data-testid="advice-source"]').text()).toBe("AI");
    expect(done.text()).toContain("建议先主动确认进展");
    // 建议不自动执行：没有产生任何 action 请求，岗位仍在列表。
    expect(calls.filter((call) => call.url.endsWith("/actions"))).toHaveLength(0);
    expect(done.exists()).toBe(true);
  });

  it("规则兜底建议显示“规则”来源；advice 失败显示真实错误且不影响动作按钮", async () => {
    installFetch([
      {
        pattern: /^\/api\/job-reminders\?/,
        respond: () => jsonResponse(listBody([
          reminderItem({ job_id: "j-rule" }),
          reminderItem({ job_id: "j-fail" }),
        ])),
      },
      {
        pattern: /\/advice$/,
        respond: (url) => {
          if (url.includes("/j-rule/")) {
            return jsonResponse({ ok: true, action: "review", reason: "缺少职位描述，需要人工复核。", source: "rule" });
          }
          return jsonResponse(errorBody("reminder_not_eligible", "该岗位当前不在提醒范围"), 409);
        },
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    await listItem(wrapper, "j-rule")!.get('[data-testid="btn-advice"]').trigger("click");
    await flushPromises();
    const ruleItem = listItem(wrapper, "j-rule")!;
    expect(ruleItem.get('[data-testid="advice-result"]').text()).toContain("建议复核");
    expect(ruleItem.get('[data-testid="advice-source"]').text()).toBe("规则");

    await listItem(wrapper, "j-fail")!.get('[data-testid="btn-advice"]').trigger("click");
    await flushPromises();
    const failItem = listItem(wrapper, "j-fail")!;
    expect(failItem.get('[data-testid="advice-error"]').text()).toContain("该岗位当前不在提醒范围");
    // 建议失败不影响生命周期动作可用性，也不改变岗位状态。
    expect(failItem.get('[data-testid="btn-follow-up"]').attributes("disabled")).toBeUndefined();
    expect(failItem.get('[data-testid="btn-mark-stale"]').attributes("disabled")).toBeUndefined();
    expect(listItem(wrapper, "j-fail")?.exists()).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// T052 can_open + platform URL 复验：无效链接禁用但其它操作可用
// ---------------------------------------------------------------------------

describe("T052 跳转安全：can_open 与所属平台 URL 复验", () => {
  it("can_open=true 且链接通过平台复验：渲染校验后的 href（智联剥离 query/fragment）", async () => {
    installFetch([
      {
        pattern: /^\/api\/job-reminders\?/,
        respond: () => jsonResponse(listBody([
          reminderItem({ job_id: "j-boss" }),
          reminderItem({
            job_id: "j-zl",
            platform: "zhilian",
            canonical_url: "http://www.zhaopin.com/jobdetail/z-1.htm?utm=x#y",
          }),
        ])),
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    const bossLink = listItem(wrapper, "j-boss")!.get('[data-testid="btn-open-job"]');
    expect(bossLink.attributes("href")).toBe("https://www.zhipin.com/job_detail/platform-1.html");

    // http 升级 https、剥离 query/fragment。
    const zhilianLink = listItem(wrapper, "j-zl")!.get('[data-testid="btn-open-job"]');
    expect(zhilianLink.attributes("href")).toBe("https://www.zhaopin.com/jobdetail/z-1.htm");
  });

  it("can_open=false：跳转禁用并给出可理解状态；跟进/荒废/建议仍可用", async () => {
    installFetch([
      {
        pattern: /^\/api\/job-reminders\?/,
        respond: () => jsonResponse(listBody([
          reminderItem({ job_id: "j-noopen", canonical_url: null, can_open: false }),
        ])),
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    const item = listItem(wrapper, "j-noopen")!;
    const disabled = item.get('[data-testid="btn-open-job-disabled"]');
    expect(disabled.attributes("disabled")).toBeDefined();
    expect(item.find('a[data-testid="btn-open-job"]').exists()).toBe(false);
    expect(item.text()).toContain("来源链接不可用");
    expect(item.get('[data-testid="btn-follow-up"]').attributes("disabled")).toBeUndefined();
    expect(item.get('[data-testid="btn-mark-stale"]').attributes("disabled")).toBeUndefined();
    expect(item.get('[data-testid="btn-advice"]').attributes("disabled")).toBeUndefined();
  });

  it("can_open=true 但链接不属于所属平台：前端复验禁用跳转，不拼裸平台 ID", async () => {
    installFetch([
      {
        pattern: /^\/api\/job-reminders\?/,
        respond: () => jsonResponse(listBody([
          reminderItem({
            job_id: "j-mismatch",
            platform: "boss",
            canonical_url: "https://www.zhaopin.com/jobdetail/other.htm",
            can_open: true,
          }),
        ])),
      },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    const item = listItem(wrapper, "j-mismatch")!;
    expect(item.find('a[data-testid="btn-open-job"]').exists()).toBe(false);
    expect(item.get('[data-testid="btn-open-job-disabled"]').attributes("disabled")).toBeDefined();
    expect(item.text()).toContain("安全校验");
    // 该项内不存在任何跳转链接（不拼裸平台 ID、不打开任意地址）。
    expect(item.find("a[href]").exists()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// T053 响应式布局合同：scoped CSS、双视口、44px 点击区、无横向溢出
// ---------------------------------------------------------------------------

describe("T053 布局合同（scoped CSS 静态校验）", () => {
  const source = readFileSync(
    resolve(process.cwd(), "src/components/ReminderDrawer.vue"),
    "utf8",
  );

  it("使用 scoped 样式且包含窄屏断点", () => {
    expect(source).toContain("<style scoped>");
    expect(source).toMatch(/@media\s*\(max-width:\s*720px\)/);
    // 圆润浮窗合同：宽度受视口约束（min(380px, calc(100vw - 32px))），窄屏仍留 16px 边距。
    expect(source).toMatch(/width:\s*min\(380px,\s*calc\(100vw - 32px\)\)/);
    expect(source).toMatch(/width:\s*calc\(100vw - 32px\)/);
  });

  it("动作按钮保持 ≥44px 点击区且可换行不逐字竖排", () => {
    expect(source).toContain("min-height: 44px");
    expect(source).toMatch(/\.reminder-item-actions\s*\{[^}]*flex-wrap:\s*wrap/s);
  });

  it("列表是唯一内部滚动区，文本可换行且无横向溢出", () => {
    expect(source).toMatch(/\.reminder-list\s*\{[^}]*overflow-y:\s*auto/s);
    expect(source).toMatch(/\.reminder-list\s*\{[^}]*overflow-x:\s*hidden/s);
    expect(source).toContain("overflow-wrap: anywhere");
    expect(source).toMatch(/\.reminder-drawer\s*\{[^}]*overflow-x:\s*hidden/s);
    // 浮窗宽度用 calc(100vw - 32px) 封顶，不会产生横向溢出。
    expect(source).toMatch(/calc\(100vw - 32px\)/);
  });

  it("渲染的操作行使用可换行容器且不出现嵌套按钮", async () => {
    installFetch([
      { pattern: /^\/api\/job-reminders\?/, respond: () => jsonResponse(listBody([reminderItem()])) },
    ]);
    const wrapper = tracked(mountDrawer());
    await flushPromises();

    const actions = wrapper.get(".reminder-item-actions");
    expect(actions.classes()).toContain("reminder-item-actions");
    // 四个快捷入口：查看/跟进/荒废/建议，均为独立可点击元素。
    expect(actions.findAll("button, a")).toHaveLength(4);
    expect(actions.find("button button").exists()).toBe(false);
  });
});
