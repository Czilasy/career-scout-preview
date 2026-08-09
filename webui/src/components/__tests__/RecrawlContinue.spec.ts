import { flushPromises, mount } from "@vue/test-utils";
import DiscoveryView from "../../views/DiscoveryView.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

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

describe("DiscoveryView paused recrawl recovery", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("continues the original recrawl task without creating another one", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/latest-running-task") {
        return response({
          ok: true,
          has_task: true,
          task_id: "recrawl-original",
          kind: "recrawl",
          status: "paused",
          stage: "recrawl_fetch_jd",
          source_run_id: "source-run-1",
          pause_info: { error_code: "captcha_required", error_reason: "触发验证码" },
          version_match: true,
        });
      }
      if (url === "/api/task-state/recrawl-original") {
        return response({
          status: "paused",
          stage: "recrawl_fetch_jd",
          success_count: 2,
          fail_count: 0,
          unstarted_count: 3,
          total: 5,
          pause_info: { error_code: "captcha_required", error_reason: "触发验证码" },
        });
      }
      if (url === "/api/task/continue/recrawl-original") {
        return response({
          ok: true,
          task_id: "recrawl-original",
          source_run_id: "source-run-1",
          completed_job_ids: ["j1", "j2"],
        });
      }
      if (url === "/api/task-state/recrawl-original") {
        return response({ status: "paused", progress: {}, logs: [], error: "触发验证码" });
      }
      if (url.startsWith("/api/latest-pipeline-result")) {
        return response({
          ok: true,
          has_result: true,
          result: {
            jobs: [{ job_id: "j1", title: "前端", verdict: "uncertain", verdict_reason: "触发验证码" }],
          },
        });
      }
      if (url === "/api/version") {
        return response({
          backend_version: "010", build_hash: expectedBackendBuildHash, build_time: "now",
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();

    expect(wrapper.get(".results-stage").classes()).toContain("has-recrawl-banner");
    expect(wrapper.get(".task-stage").text()).toContain("重抓 JD 详情");
    expect(wrapper.get(".task-stage").text()).not.toContain("recrawl_fetch_jd");
    const resumeButton = wrapper.get('[data-testid="resume-recrawl"]');
    expect(wrapper.get('[data-testid="pause-reason"]').text()).toContain("触发验证码");
    await resumeButton.trigger("click");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/task/continue/recrawl-original",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/pipeline/recrawl")).toBe(false);
  });

  it("starts bulk recrawl with the persisted source run id", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/latest-running-task") {
        return response({ ok: true, has_task: false });
      }
      if (url.startsWith("/api/latest-pipeline-result")) {
        // 合并载入按平台分别查询；智联无结果，只有 BOSS 这一份。
        if (url.includes("platform=zhilian")) {
          return response({ ok: true, has_result: false });
        }
        return response({
          ok: true,
          has_result: true,
          source_run_id: "result-run-42",
          result: {
            jobs: [{
              job_id: "pending-1", title: "前端", verdict: "uncertain",
              verdict_reason: "详情超时",
            }],
          },
        });
      }
      if (url === "/api/pipeline/recrawl") {
        return response({ ok: true, task_id: "recrawl-42" }, 202);
      }
      if (url === "/api/task-state/recrawl-42") {
        return response({ status: "paused", progress: {}, logs: [], error: "验证码" });
      }
      if (url === "/api/version") {
        return response({
          backend_version: "010", build_hash: expectedBackendBuildHash, build_time: "now",
        });
      }
      return response({ init });
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    const resultsStep = wrapper.findAll("button").find((button) => button.text().includes("查看结果"));
    expect(resultsStep).toBeDefined();
    await resultsStep?.trigger("click");
    await flushPromises();
    // “全部重抓”只在单平台视图可见：先切到 BOSS 视图再触发。
    await wrapper.get('[data-testid="result-platform-filter-boss"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="recrawl-uncertain"]').trigger("click");
    await flushPromises();

    const call = fetchMock.mock.calls.find(([url]) => String(url) === "/api/pipeline/recrawl");
    expect(call).toBeDefined();
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      source_run_id: "result-run-42",
      job_ids: ["pending-1"],
    });
  });

  it("starts a single JD retry as a resumable task with source identity", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/latest-running-task") return response({ ok: true, has_task: false });
      if (url.startsWith("/api/latest-pipeline-result")) {
        return response({
          ok: true, has_result: true, source_run_id: "result-run-43",
          result: { jobs: [{
            job_id: "pending-1", title: "前端", verdict: "uncertain",
            verdict_reason: "详情超时", source_url: "https://www.zhipin.com/job_detail/pending-1.html",
          }] },
        });
      }
      if (url === "/api/pipeline/jobs/pending-1/jd") {
        return response({ ok: true, task_id: "single-retry-1", single_retry: true }, 202);
      }
      if (url === "/api/task-state/single-retry-1") {
        return response({ status: "paused", progress: {}, logs: [], error: "触发验证码" });
      }
      if (url === "/api/version") {
        return response({
          backend_version: "010", build_hash: expectedBackendBuildHash, build_time: "now",
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    const resultsStep = wrapper.findAll("button").find((button) => button.text().includes("查看结果"));
    await resultsStep?.trigger("click");
    await flushPromises();
    const retryButton = wrapper.findAll("button").find((button) => button.text().includes("补抓 JD"));
    expect(retryButton).toBeDefined();
    await retryButton?.trigger("click");
    await flushPromises();

    const call = fetchMock.mock.calls.find(([url]) => String(url) === "/api/pipeline/jobs/pending-1/jd");
    expect(call).toBeDefined();
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      source_run_id: "result-run-43",
    });
    expect(wrapper.get('[data-testid="pause-reason"]').text()).toContain("触发验证码");
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/search-progress")))
      .toBe(false);
  });
});
