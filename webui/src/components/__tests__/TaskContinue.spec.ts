import { flushPromises, mount } from "@vue/test-utils";
import DiscoveryView from "../../views/DiscoveryView.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";
import TaskProgress from "../TaskProgress.vue";

// 本文件引用的 api 模块实例可能未被 setup.ts 验证过（vitest 模块实例隔离），逐用例重新验证
beforeEach(() => {
  setBuildIdentity(expectedBackendBuildHash);
});

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("DiscoveryView paused AI recovery", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("restores the AI stage and continues through the server resume route", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/latest-running-task") {
        return response({
          ok: true,
          has_task: true,
          task_id: "paused-ai-run",
          kind: "ai_screen",
          status: "paused",
          stage: "ai_rough",
          scrape_task_id: "scrape-task-1",
          scrape_completed: true,
          source_run_id: "source-run-1",
          pause_info: { error_code: "ai_rate_limited", error_reason: "AI 接口限流" },
          version_match: true,
        });
      }
      if (url === "/api/task-state/paused-ai-run") {
        return response({
          status: "paused",
          stage: "ai_rough",
          progress: 40,
          success_count: 20,
          fail_count: 0,
          unstarted_count: 30,
          total: 50,
          pause_info: { error_code: "ai_rate_limited", error_reason: "AI 接口限流" },
        });
      }
      if (url === "/api/task/continue/paused-ai-run") {
        return response({ ok: true, task_id: "resumed-ai-run", status: "running" });
      }
      if (url === "/api/task-state/resumed-ai-run") {
        return response({ status: "paused", progress: {}, logs: [], error: "AI 接口限流" });
      }
      if (url.startsWith("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url === "/api/version") {
        return response({
          backend_version: "010",
          build_hash: expectedBackendBuildHash,
          build_time: "now",
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();

    expect(wrapper.get('[aria-expanded]').attributes("aria-expanded")).toBe("true");
    const resumeButton = wrapper.get('[data-testid="continue-ai-screen"]');
    expect(resumeButton.attributes("disabled")).toBeUndefined();
    expect(wrapper.get('[data-testid="pause-reason"]').text()).toContain("AI 接口限流");
    expect(wrapper.get(".task-percentage").text()).toBe("40%");
    expect(wrapper.get('[data-testid="task-counts"]').text()).toContain("已完成 20");

    await resumeButton.trigger("click");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/task/continue/paused-ai-run",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({}),
      }),
    );
    const continueCall = fetchMock.mock.calls.find(
      ([url]) => String(url) === "/api/task/continue/paused-ai-run",
    );
    // 构建身份拦截已下线：写请求不再携带 X-Boss-Build 头
    expect((continueCall?.[1]?.headers as Headers).get("X-Boss-Build"))
      .toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/ai-screen")).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/search-progress")))
      .toBe(false);
  });

  it("T510: displays zhilian platform badge and continues without submitting platform body", async () => {
    // T510：任务自身平台从 /api/latest-running-task 透传到 TaskProgress（snapshot.platform）；
    // cancel/continue/finish 是无 body POST，不提交草稿平台选择（http-api.md L323 平台不属于 activate 状态）。
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/latest-running-task") {
        return response({
          ok: true,
          has_task: true,
          task_id: "paused-zhilian-run",
          kind: "ai_screen",
          status: "paused",
          platform: "zhilian",
          stage: "ai_rough",
          scrape_task_id: "scrape-task-1",
          scrape_completed: true,
          pause_info: { error_code: "ai_rate_limited", error_reason: "AI 接口限流" },
          version_match: true,
        });
      }
      if (url === "/api/task-state/paused-zhilian-run") {
        return response({
          status: "paused",
          stage: "ai_rough",
          progress: 40,
          success_count: 20,
          fail_count: 0,
          unstarted_count: 30,
          total: 50,
        });
      }
      if (url === "/api/task/continue/paused-zhilian-run") {
        return response({ ok: true, task_id: "resumed-zhilian-run", status: "running" });
      }
      if (url === "/api/task-state/resumed-zhilian-run") {
        return response({ status: "paused", progress: {}, logs: [], error: "AI 接口限流" });
      }
      if (url.startsWith("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url === "/api/version") {
        return response({
          backend_version: "010",
          build_hash: expectedBackendBuildHash,
          build_time: "now",
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();

    // T510：TaskProgress 头部展示任务自身平台（zhilian）徽章，与 .platform-segment 草稿徽章独立
    const badge = wrapper.get('[data-testid="task-platform-badge"]');
    expect(badge.text()).toContain("智联");
    expect(badge.attributes("data-platform")).toBe("zhilian");

    // 点继续：continue 不发 body（不发 platform — 任务平台已冻结，后端从父 run 读）
    await wrapper.get('[data-testid="continue-ai-screen"]').trigger("click");
    await flushPromises();

    const continueCall = fetchMock.mock.calls.find(
      ([url]) => String(url) === "/api/task/continue/paused-zhilian-run",
    );
    expect(continueCall).toBeTruthy();
    // T510：cancel/continue/finish 都是无 body POST，不提交草稿平台选择
    expect(continueCall?.[1]?.body).toBeUndefined();
  });
});

describe("TaskProgress canonical terminal states", () => {
  it("renders completed as a stopped successful task", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "completed",
          progress: { stage: "done", overall_percent: 100 },
          started_at: 1_000,
          finished_at: 2_000,
        },
      },
    });

    expect(wrapper.get(".task-status").text()).toContain("已完成");
    expect(wrapper.find(".spin").exists()).toBe(false);
    expect(wrapper.get(".task-elapsed").text()).toContain("用时");
  });

  it("renders completed_with_pending as terminal with a distinct label", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "completed_with_pending",
          progress: { stage: "done", overall_percent: 100 },
          fail_count: 1,
          total: 3,
          started_at: 1_000,
          finished_at: 2_000,
        },
      },
    });

    expect(wrapper.get(".task-status").text()).toContain("完成，但有待确认");
    expect(wrapper.find(".spin").exists()).toBe(false);
    expect(wrapper.get(".task-elapsed").text()).toContain("用时");
  });

  it("hides elapsed time for terminal snapshots without real timestamps", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "completed",
          progress: { stage: "done", overall_percent: 100 },
        },
      },
    });

    expect(wrapper.get(".task-status").text()).toContain("已完成");
    expect(wrapper.find(".task-elapsed").exists()).toBe(false);
  });

  it("resets elapsed time when a new running snapshot replaces a terminal snapshot", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-01T00:00:00.000Z"));
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "completed",
          progress: { stage: "done", overall_percent: 100 },
          started_at: 1_000,
          finished_at: 2_000,
        },
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 1秒");

    await wrapper.setProps({
      snapshot: {
        status: "running",
        progress: { stage: "searching" },
        started_at: Date.now(),
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 0秒");
    vi.useRealTimers();
  });

  it("keeps elapsed time when a paused task resumes with the same started_at", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-01T00:00:00.000Z").getTime();
    vi.setSystemTime(baseTime + 60_000);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "paused",
          progress: { stage: "searching", overall_percent: 20 },
          started_at: baseTime,
        },
      },
    });

    await wrapper.setProps({
      snapshot: {
        status: "running",
        progress: { stage: "searching", overall_percent: 20 },
        started_at: baseTime,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分00秒");
    vi.useRealTimers();
  });

  it("does not render execution batch summary", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          total: 50,
          success_count: 0,
          kept_count: 4,
          dropped_count: 0,
          execution_config: {
            screen_batch_size: 50,
            screen_concurrency: 10,
            match_batch_size: 10,
            match_concurrency: 10,
          },
        },
      },
    });
    expect(wrapper.find('[data-testid="task-config"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain("粗筛每批");
    expect(wrapper.text()).not.toContain("精筛每批");
  });
});

describe("TaskProgress real progress engine and grouped counts", () => {
  afterEach(() => vi.useRealTimers());

  it("shows a spinning circle while running and stops at a terminal state", async () => {
    vi.useFakeTimers();
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "running",
          progress: { stage: "searching", current: 1, total: 5, overall_percent: 20 },
        },
      },
    });
    expect(wrapper.find(".task-status .spin").exists()).toBe(true);

    await wrapper.setProps({
      snapshot: { status: "completed", progress: { stage: "done", overall_percent: 100 } },
    });
    await flushPromises();
    expect(wrapper.find(".task-status .spin").exists()).toBe(false);
  });

  it("does not creep while the real anchor stays still", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-03T00:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);

    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 0, total: 57, overall_percent: 20 },
          total: 57,
          started_at: baseTime,
        },
      },
    });
    await flushPromises();
    // 先让显示值追上真实锚点
    vi.advanceTimersByTime(2_000);
    await flushPromises();
    expect(wrapper.get(".task-percentage").text()).toBe("20%");

    // 真实锚点不变时，再走 10 秒也必须保持 20%，不得时间假爬
    vi.advanceTimersByTime(10_000);
    await flushPromises();
    expect(wrapper.get(".task-percentage").text()).toBe("20%");
  });

  it("never exceeds the backend anchor while chasing", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-03T01:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);

    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "running",
          progress: { stage: "searching", current: 1, total: 10, overall_percent: 10 },
          total: 10,
          started_at: baseTime,
        },
      },
    });
    await flushPromises();
    vi.advanceTimersByTime(10_000);
    await flushPromises();
    const pct = Number(wrapper.get(".task-percentage").text().replace("%", ""));
    expect(pct).toBeLessThanOrEqual(10);
  });

  it("advances only when the backend anchor moves for 10 groups", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-03T02:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);

    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "running",
          progress: { stage: "searching", current: 0, total: 10, overall_percent: 1 },
          total: 10,
          started_at: baseTime,
        },
      },
    });
    await flushPromises();
    vi.advanceTimersByTime(3_000);
    await flushPromises();
    const before = Number(wrapper.get(".task-percentage").text().replace("%", ""));
    expect(before).toBeLessThanOrEqual(10);

    // 第 1 组真实完成：锚点从 1% 到 10%，允许追到 10%，但第 2 组完成前不得超过 10%。
    await wrapper.setProps({
      snapshot: {
        status: "running",
        progress: { stage: "searching", current: 1, total: 10, overall_percent: 10 },
        total: 10,
        started_at: baseTime,
      },
    });
    await flushPromises();
    vi.advanceTimersByTime(3_000);
    await flushPromises();
    const firstDone = Number(wrapper.get(".task-percentage").text().replace("%", ""));
    expect(firstDone).toBe(10);

    vi.advanceTimersByTime(5_000);
    await flushPromises();
    expect(Number(wrapper.get(".task-percentage").text().replace("%", ""))).toBe(10);
  });

  it("freezes at the real value while paused", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-03T03:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);

    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 30, total: 57, overall_percent: 51 },
          started_at: baseTime,
        },
      },
    });
    await flushPromises();
    vi.advanceTimersByTime(2_000);
    await flushPromises();

    await wrapper.setProps({
      snapshot: {
        status: "paused",
        progress: { stage: "fetch_jd", current: 30, total: 57, overall_percent: 51 },
        started_at: baseTime,
      },
    });
    await flushPromises();
    expect(wrapper.get(".task-percentage").text()).toBe("51%");
    vi.advanceTimersByTime(10_000);
    await flushPromises();
    expect(wrapper.get(".task-percentage").text()).toBe("51%");
  });

  it("shows the current real value on failure and 100 on completion", () => {
    const failed = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: { status: "failed", progress: { overall_percent: 20 } },
      },
    });
    expect(failed.get(".task-percentage").text()).toBe("20%");

    const done = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: { status: "completed", progress: { stage: "done", overall_percent: 20 } },
      },
    });
    expect(done.get(".task-percentage").text()).toBe("100%");
  });

  it("uses backend overall_percent instead of stage weights for screen phases", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 0, total: 57, overall_percent: 37 },
        },
      },
    });
    expect(wrapper.get(".task-percentage").text()).toBe("37%");
  });

  it("falls back to raw current/total when overall_percent is missing", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 0, total: 57 },
        },
      },
    });
    expect(wrapper.get(".task-percentage").text()).toBe("0%");
  });

  it("maps known internal stages to Chinese and hides raw stage text", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "running",
          stage: "waiting",
          progress: { stage: "waiting", overall_percent: 10 },
        },
      },
    });
    expect(wrapper.get(".task-stage").text()).toContain("防限流等待");
    expect(wrapper.text()).not.toContain("waiting");
  });

  it("shows Chinese fallback for unknown stages", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          stage: "mystery_stage",
          progress: { stage: "mystery_stage" },
        },
      },
    });
    expect(wrapper.get(".task-stage").text()).toContain("处理中");
    expect(wrapper.text()).not.toContain("mystery_stage");
  });

  it("derives scrape counts from completed combinations instead of screen counters", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "running",
          total: 2,
          success_count: 0,
          unstarted_count: 2,
          progress: { stage: "searching", current: 1, total: 2, overall_percent: 50 },
        },
      },
    });

    const text = wrapper.get('[data-testid="task-counts"]').text();
    expect(text).toContain("已完成 1 / 2");
    expect(text).toContain("进行中 1");
    expect(text).toContain("未开始 0");
    expect(text).not.toContain("已完成 0 / 2");
  });

  it("resets display for a new run that stays in the same stage", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-03T04:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);

    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 57, total: 57, overall_percent: 75 },
          started_at: baseTime,
        },
      },
    });
    await flushPromises();
    vi.advanceTimersByTime(2_000);
    await flushPromises();
    expect(wrapper.get(".task-percentage").text()).toBe("75%");

    await wrapper.setProps({
      snapshot: {
        status: "running",
        progress: { stage: "fetch_jd", current: 0, total: 57, overall_percent: 25 },
        started_at: baseTime + 1_000,
      },
    });
    await flushPromises();
    expect(wrapper.get(".task-percentage").text()).toBe("25%");
  });

  it("groups counts into 已完成 X / Y and 保留 N · 淘汰 M, drops 共/失败 0", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 30, total: 57, overall_percent: 51 },
          total: 57,
          source_total: 90,
          success_count: 30,
          fail_count: 0,
          unstarted_count: 27,
          kept_count: 57,
          dropped_count: 33,
          pending_count: 0,
        },
      },
    });

    const text = wrapper.get('[data-testid="task-counts"]').text();
    expect(text).toContain("已完成 30 / 57");
    expect(text).toContain("保留 57");
    expect(text).toContain("淘汰 33");
    expect(text).toMatch(/保留 57\s*·\s*淘汰 33/);
    expect(text).not.toContain("共 57");
    expect(text).not.toContain("失败 0");
  });
});

describe("TaskProgress platform badge (T510)", () => {
  // T510：snapshot.platform 由父组件从 /api/latest-running-task 或 /api/task-state 透传；
  // 草稿平台切换不影响此处 — 这里展示的是任务自身平台，与 .platform-segment 草稿徽章独立。
  it("displays 智联 badge when snapshot.platform is zhilian", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          platform: "zhilian",
          progress: { stage: "fetch_jd", current: 0, total: 10 },
        },
      },
    });
    const badge = wrapper.get('[data-testid="task-platform-badge"]');
    expect(badge.text()).toContain("智联");
    expect(badge.attributes("data-platform")).toBe("zhilian");
  });

  it("displays BOSS badge when snapshot.platform is boss", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "running",
          platform: "boss",
          progress: { stage: "searching" },
        },
      },
    });
    const badge = wrapper.get('[data-testid="task-platform-badge"]');
    expect(badge.text()).toContain("BOSS");
    expect(badge.attributes("data-platform")).toBe("boss");
  });

  it("hides platform badge when snapshot.platform is undefined", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: { status: "running", progress: { stage: "searching" } },
      },
    });
    expect(wrapper.find('[data-testid="task-platform-badge"]').exists()).toBe(false);
  });
});
