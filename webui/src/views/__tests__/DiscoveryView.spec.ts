import { flushPromises, mount } from "@vue/test-utils";
import DiscoveryView from "../DiscoveryView.vue";
import { expectedBackendBuildHash } from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("DiscoveryView", () => {
  it("creates tuning only from six explicitly entered representative workloads", async () => {
    localStorage.removeItem("boss-tuning-experiment-id");
    const settings = {
      inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, screen_batch_size: 50,
      screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/version")) return response({ backend_version: "011", build_hash: expectedBackendBuildHash, build_time: "now" });
      if (url.endsWith("/api/advanced-settings")) return response({
        ok: true, selection: "custom", settings,
        last_custom: { config_digest: "custom", settings }, mode_version: null,
        manual_ranges: {}, config_schema_version: 1,
      });
      if (url.endsWith("/api/search-scope/preview")) return response({
        ok: true,
        scope: {
          keywords: ["AI应用开发"], scope_kind: "cities", cities: ["东莞"],
          pages_per_combination: 3, combination_count: 1, planned_pages: 3,
          task_size: "small", scope_digest: "scope-source",
        }, deduplicated: { keywords: [], cities: [] },
      });
      if (url.endsWith("/api/tuning/experiments") && init?.method === "POST") {
        const payload = JSON.parse(String(init.body));
        expect(payload.workloads).toHaveLength(6);
        expect(payload.workloads.map((item: { task_size: string }) => item.task_size)).toEqual([
          "small", "small", "medium", "medium", "large", "large",
        ]);
        expect(payload.workloads[4].scope.pages_per_combination).toBe(10);
        return response({ ok: true, experiment_id: "exp-created", status: "draft" }, 201);
      }
      if (url.endsWith("/api/tuning/experiments/exp-created/result")) return response({
        ok: true, experiment_id: "exp-created", status: "draft", can_apply: false,
        candidate_summary: [], evidence: [],
      });
      if (url.endsWith("/api/tuning/experiments/exp-created")) return response({
        ok: true, experiment: {
          id: "exp-created", status: "draft", spec_version: "011-deep-configuration-probing",
          progress: { confirmed_rounds: 0, remaining_required_rounds: 0, estimated_remaining_seconds: 0 },
          can_cancel: true, can_resume: false, can_apply: false,
        },
      });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("AI应用开发");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("东莞");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="create-tuning"]').trigger("click");

    const rows = [
      ["AI应用开发", "东莞", "3"],
      ["AI应用开发,智能体开发", "东莞", "4"],
      ["AI应用开发", "东莞,深圳", "5"],
      ["AI应用开发,智能体开发", "东莞", "5"],
      ["AI应用开发", "东莞,深圳", "10"],
      ["AI应用开发,智能体开发", "东莞", "10"],
    ];
    for (const [index, values] of rows.entries()) {
      await wrapper.get(`[data-testid="workload-keywords-${index}"]`).setValue(values[0]);
      await wrapper.get(`[data-testid="workload-cities-${index}"]`).setValue(values[1]);
      await wrapper.get(`[data-testid="workload-pages-${index}"]`).setValue(values[2]);
    }
    await wrapper.get('[data-testid="submit-tuning-create"]').trigger("click");
    await flushPromises();
    expect(localStorage.getItem("boss-tuning-experiment-id")).toBe("exp-created");
    expect(wrapper.get('[data-testid="tuning-workspace"]').text()).toContain("待确认输入");

    localStorage.removeItem("boss-tuning-experiment-id");
    vi.unstubAllGlobals();
  });

  it("recovers a persisted completed experiment and applies or rolls back exact versions", async () => {
    localStorage.setItem("boss-tuning-experiment-id", "exp-persisted");
    const settings = {
      inter_combo_delay: 10, detail_batch_size: 15, detail_interval: 2,
      detail_reset_every: 4, detail_batch_cooldown: 5, screen_batch_size: 50,
      screen_concurrency: 5, match_batch_size: 4, match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/version")) return response({ backend_version: "011", build_hash: expectedBackendBuildHash, build_time: "now" });
      if (url.endsWith("/api/advanced-settings")) return response({
        ok: true, selection: "balanced", settings,
        last_custom: { config_digest: "custom-digest", settings },
        mode_version: {
          id: "mode-current", version_digest: "mode-current-digest",
          previous_version_id: "mode-previous",
          available_modes: ["stable", "balanced", "extreme"],
        },
        manual_ranges: {}, config_schema_version: 1,
      });
      if (url.endsWith("/api/tuning/experiments/exp-persisted/result")) return response({
        ok: true, experiment_id: "exp-persisted", status: "completed", can_apply: true,
        candidate_mode_version_digest: "sha256:candidate",
        candidate_summary: [{ id: "candidate-final", status: "accepted" }],
        evidence: [{ id: "round-final", status: "confirmed", total_duration_ms: 9000 }],
      });
      if (url.endsWith("/api/tuning/experiments/exp-persisted")) return response({
        ok: true,
        experiment: {
          id: "exp-persisted", status: "completed", spec_version: "011-deep-configuration-probing",
          current_stage: "end_to_end", current_candidate_id: "candidate-final",
          current_round_id: "round-final",
          progress: { confirmed_rounds: 18, remaining_required_rounds: 0, estimated_remaining_seconds: 0 },
          can_cancel: false, can_resume: false, can_apply: true,
        },
      });
      if (url.endsWith("/api/tuning/experiments/exp-persisted/apply")) {
        expect(JSON.parse(String(init?.body))).toEqual({ candidate_mode_version_digest: "sha256:candidate" });
        return response({ ok: true, mode_version_id: "mode-new" });
      }
      if (url.endsWith("/api/advanced-settings/mode-versions/rollback")) {
        expect(JSON.parse(String(init?.body))).toEqual({ target_version_id: "mode-previous" });
        return response({ ok: true, active_mode_version_id: "mode-previous" });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("跳过简历"))!.trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="tuning-workspace"]').text()).toContain("candidate-final");
    expect(wrapper.get('[data-testid="tuning-workspace"]').text()).toContain("round-final");
    await wrapper.get('[data-testid="apply-tuning"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="rollback-tuning"]').trigger("click");
    await flushPromises();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/apply"))).toBe(true);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/rollback"))).toBe(true);

    localStorage.removeItem("boss-tuning-experiment-id");
    vi.unstubAllGlobals();
  });

  it("uses canonical scope, preserves pages across modes and locks a started task", async () => {
    const settings = {
      inter_combo_delay: 10,
      detail_batch_size: 15,
      detail_interval: 2,
      detail_reset_every: 4,
      detail_batch_cooldown: 5,
      screen_batch_size: 50,
      screen_concurrency: 5,
      match_batch_size: 4,
      match_concurrency: 10,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) return response({ ok: true, has_result: false });
      if (url.endsWith("/api/version")) return response({ backend_version: "011", build_hash: expectedBackendBuildHash, build_time: "now" });
      if (url.endsWith("/api/filter-labels")) return response({ labels: {} });
      if (url.endsWith("/api/latest-running-task")) return response({ task: null });
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true,
          selection: "balanced",
          settings,
          last_custom: { config_digest: "sha256:custom", settings: { ...settings, detail_batch_size: 8 } },
          mode_version: { id: "mode-v1", version_digest: "sha256:mode", available_modes: ["stable", "balanced", "extreme"] },
          manual_ranges: {},
          config_schema_version: 1,
        });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: {
            keywords: ["AI应用开发"], scope_kind: "cities", cities: ["东莞"],
            pages_per_combination: 3, combination_count: 1, planned_pages: 3,
            task_size: "small", scope_digest: "sha256:scope",
          },
          deduplicated: { keywords: ["ai应用开发"], cities: ["东莞市"] },
        });
      }
      if (url.endsWith("/api/advanced-settings/select-mode")) {
        expect(JSON.parse(String(init?.body))).toEqual({ mode: "stable", scope_digest: "sha256:scope" });
        return response({
          ok: true, selection: "stable", settings: { ...settings, detail_batch_size: 6 },
          task_size: "small", mode_version_id: "mode-v1", config_digest: "sha256:stable",
        });
      }
      if (url.endsWith("/api/execute-search")) {
        expect(JSON.parse(String(init?.body)).scope_digest).toBe("sha256:scope");
        return response({ ok: true, task_id: "scrape-locked" });
      }
      if (url.endsWith("/api/task-state/scrape-locked")) return response({ status: "running", progress: {}, logs: [] });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("跳过简历"))!.trigger("click");
    await wrapper.get('[data-testid="custom-keyword"]').setValue("AI应用开发");
    await wrapper.get('[data-testid="add-keyword"]').trigger("click");
    await wrapper.get('[data-testid="custom-city"]').setValue("东莞市");
    await wrapper.get('[data-testid="add-city"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="scope-preview"]').text()).toContain("东莞");
    expect(wrapper.get('[data-testid="scope-preview"]').text()).toContain("小任务");
    const pages = wrapper.get('[data-testid="pages-per-combination"]');
    expect((pages.element as HTMLInputElement).value).toBe("3");

    await wrapper.get('[data-mode="stable"]').trigger("click");
    await flushPromises();
    expect((pages.element as HTMLInputElement).value).toBe("3");
    expect((wrapper.get('[data-testid="detail-batch-size"]').element as HTMLInputElement).value).toBe("6");

    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="custom-keyword"]').attributes()).toHaveProperty("disabled");
    expect(wrapper.get('[data-testid="custom-city"]').attributes()).toHaveProperty("disabled");
    expect(wrapper.get('[data-testid="pages-per-combination"]').attributes()).toHaveProperty("disabled");

    vi.unstubAllGlobals();
  });

  it("keeps gated actions separate and stops on the canonical completed state", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url.endsWith("/api/version")) {
        return response({
          backend_version: "010",
          build_hash: expectedBackendBuildHash,
          build_time: "now",
        });
      }
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true,
          settings: {
            pages: 3,
            inter_combo_delay: 30,
            detail_batch_size: 5,
            screen_batch_size: 50,
            screen_concurrency: 1,
            match_batch_size: 4,
            match_concurrency: 1,
          },
          defaults: {},
        });
      }
      if (url.endsWith("/api/search-scope/preview")) {
        return response({
          ok: true,
          scope: {
            keywords: ["Python 后端"], scope_kind: "cities", cities: ["上海"],
            pages_per_combination: 3, combination_count: 1, planned_pages: 3,
            task_size: "small", scope_digest: "sha256:scope-existing",
          },
          deduplicated: { keywords: [], cities: [] },
        });
      }
      if (url.endsWith("/api/analyze-resume")) {
        return response({
          ok: true,
          fields: {
            keyword: [{ word: "Python 后端", recommended: true }],
            city: ["上海"],
            salary: ["406"],
            experience: [],
            degree: [],
            industry: [],
            scale: [],
            stage: [],
            profile_summary: "Python 后端候选人",
          },
          labels: {
            keyword: ["搜索关键词", [{ word: "Python 后端", recommended: true }], "keyword_chips"],
            city: ["城市", ["上海"], "city"],
            salary: ["薪资范围", ["406"], { "不限": "0", "20-50K": "406" }],
            experience: ["经验要求", [], { "不限": "0", "3-5年": "105" }],
            degree: ["学历", [], { "不限": "0", "本科": "203" }],
            industry: ["行业", [], { "不限": "0", "互联网": "100020" }],
            scale: ["公司规模", [], { "不限": "0", "100-499人": "304" }],
            stage: ["融资阶段", [], { "不限": "0", "B轮": "804" }],
          },
        });
      }
      if (url.endsWith("/api/execute-search")) {
        expect(JSON.parse(String(init?.body))).toEqual({
          script_params: { keyword: "Python 后端", city: ["上海"], filters: {} },
          scope_digest: "sha256:scope-existing",
        });
        return response({ ok: true, task_id: "scrape-1" });
      }
      if (url.endsWith("/api/task-state/scrape-1")) {
        return response({ status: "completed", progress: {}, logs: [], result: { jobs: [] } });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();

    const file = new File(["resume"], "resume.txt", { type: "text/plain" });
    Object.defineProperty(wrapper.get('[data-testid="resume-input"]').element, "files", {
      value: [file],
      configurable: true,
    });
    await wrapper.get('[data-testid="resume-input"]').trigger("change");
    await wrapper.get('[data-testid="resume-consent"]').setValue(true);
    await wrapper.get('[data-testid="analyze-resume"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="keyword-chip"]').attributes("aria-pressed")).toBe("true");
    expect(wrapper.get('[data-testid="start-scrape"]').text()).toContain("开始抓取");
    expect(wrapper.find('[data-testid="start-ai-screen"]').exists()).toBe(false);

    await wrapper.get('[data-testid="start-scrape"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="continue-to-screen"]').exists()).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/execute-search",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/search-progress")))
      .toBe(false);

    vi.unstubAllGlobals();
  });
});
