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
    expect(wrapper.get('[data-testid="task-counts"]').text()).toContain("成功 20");

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
