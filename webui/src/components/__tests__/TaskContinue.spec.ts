import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import DiscoveryView from "../../views/DiscoveryView.vue";
import { expectedBackendBuildHash } from "../../api";
import TaskProgress from "../TaskProgress.vue";

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

    expect(wrapper.get('[aria-expanded]').attributes("aria-expanded")).toBe("false");
    const resumeButton = wrapper.get('[data-testid="resume-ai-screen"]');
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
    expect((continueCall?.[1]?.headers as Headers).get("X-Boss-Build"))
      .toBe(expectedBackendBuildHash);
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/ai-screen")).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/search-progress")))
      .toBe(false);
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

describe("TaskProgress smooth progression and grouped counts", () => {
  afterEach(() => vi.useRealTimers());

  it("creeps forward during fetch_jd plateau even when real counts stay still", async () => {
    // 第二版逐帧驱动：mock Date + requestAnimationFrame + setInterval，让 RAF 在假时间线里跑
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-03T00:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);

    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 0, total: 57 },
          total: 57,
          success_count: 0,
          fail_count: 0,
          unstarted_count: 57,
          started_at: baseTime,
        },
      },
    });
    await flushPromises();

    const before = Number(wrapper.get(".task-percentage").text().replace("%", ""));

    // 推进 10 秒：RAF 逐帧触发，环境爬升分量驱动百分比持续前进（真实计数 current 始终 0）
    for (let i = 0; i < 10; i++) {
      vi.advanceTimersByTime(1000);
      await flushPromises();
    }

    const after = Number(wrapper.get(".task-percentage").text().replace("%", ""));
    expect(after).toBeGreaterThan(before);
  });

  it("stays below stage end after exceeding estimated duration", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-03T00:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);

    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 0, total: 57 },
          total: 57,
          success_count: 0,
          fail_count: 0,
          unstarted_count: 57,
          started_at: baseTime,
        },
      },
    });
    await flushPromises();

    // 推进 310 秒（超过 fetch_jd 预估时长 300 秒）：真实计数没变，百分比不应到达阶段 end 65，
    // 最多停在软上限附近（fetch_jd 软上限 = 24 + (65-24) × 0.88 ≈ 60）
    vi.advanceTimersByTime(310_000);
    await flushPromises();

    const pct = Number(wrapper.get(".task-percentage").text().replace("%", ""));
    expect(pct).toBeLessThan(65);
  });

  it("chases to stage end when real counts complete", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-03T00:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);

    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 0, total: 57 },
          total: 57,
          success_count: 0,
          fail_count: 0,
          unstarted_count: 57,
          started_at: baseTime,
        },
      },
    });
    await flushPromises();

    // 真实计数完成：current=57/57，真实锚点跳到 65（fetch_jd 区间 end），进度条快速追上去
    await wrapper.setProps({
      snapshot: {
        status: "running",
        progress: { stage: "fetch_jd", current: 57, total: 57 },
        total: 57,
        success_count: 57,
        fail_count: 0,
        unstarted_count: 0,
        started_at: baseTime,
      },
    });
    await flushPromises();
    // 推进 5 秒：追赶期 600ms + 平台期继续追剩余 + 余量覆盖随机停顿
    vi.advanceTimersByTime(5_000);
    await flushPromises();

    const pct = Number(wrapper.get(".task-percentage").text().replace("%", ""));
    expect(pct).toBeGreaterThanOrEqual(64); // 接近阶段 end 65
  });

  it("uses combo_done as a real progress anchor after each completed combo", () => {
    vi.useFakeTimers();
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: {
          status: "running",
          progress: { stage: "combo_done", current: 1, total: 5 },
        },
      },
    });
    // 区间 10→90，1/5 已完成 → 26%，不能因为 combo_done 被当非进度阶段而停在 10%。
    expect(wrapper.get(".task-percentage").text()).toBe("26%");
  });

  it("falls back to snapshot stage when progress payload omits stage", () => {
    vi.useFakeTimers();
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          stage: "fetch_jd",
          progress: { current: 0, total: 57 },
        },
      },
    });
    expect(wrapper.get(".task-percentage").text()).toBe("24%");
  });

  it("keeps creeping after estimated stage duration is exceeded", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-03T01:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);

    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 0, total: 57 },
          started_at: baseTime,
        },
      },
    });
    await flushPromises();

    vi.advanceTimersByTime(300_000);
    await flushPromises();
    const atCap = Number(wrapper.get(".task-percentage").text().replace("%", ""));

    // 超过预估时长后继续极慢微动：再走 10 秒应高于软上限位置，而不是冻结。
    vi.advanceTimersByTime(10_000);
    await flushPromises();
    const after = Number(wrapper.get(".task-percentage").text().replace("%", ""));
    expect(after).toBeGreaterThan(atCap);
  });

  it("resets display for a new run that stays in the same stage", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-03T02:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);

    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 57, total: 57 },
          started_at: baseTime,
        },
      },
    });
    await flushPromises();
    expect(wrapper.get(".task-percentage").text()).toBe("65%");

    // 同一 stage 的新 run（started_at 变化）：显示值回到 24，而不是沿用上一轮的 65。
    await wrapper.setProps({
      snapshot: {
        status: "running",
        progress: { stage: "fetch_jd", current: 0, total: 57 },
        started_at: baseTime + 1000,
      },
    });
    await flushPromises();
    expect(wrapper.get(".task-percentage").text()).toBe("24%");
  });

  it("groups counts into 已完成 X / Y and 保留 N · 淘汰 M, drops 共/失败 0", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: {
          status: "running",
          progress: { stage: "fetch_jd", current: 30, total: 57 },
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
