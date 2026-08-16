import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import DiscoveryView from "../DiscoveryView.vue";
import { setThemePlatform } from "../../composables/useTheme";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const settings = {
  inter_combo_delay: 10,
  detail_batch_size: 15,
  detail_interval: 2,
  detail_reset_every: 4,
  detail_batch_cooldown: 5,
  detail_tab_pool_size: 5,
  screen_batch_size: 50,
  screen_concurrency: 5,
  match_batch_size: 4,
  match_concurrency: 10,
};

describe("DiscoveryView history mode", () => {
  beforeEach(() => {
    setBuildIdentity(expectedBackendBuildHash);
    setThemePlatform("boss");
  });

  it("opens a round, locks platform/rewrite actions, and returns to latest", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true,
          selection: "balanced",
          settings,
          last_custom: null,
          mode_version: null,
          manual_ranges: {},
          config_schema_version: 1,
        });
      }
      if (url.includes("/api/filter-labels")) {
        return response({ ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/latest-running-task")) {
        return response({ ok: true, has_task: false });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url.endsWith("/api/result-history")) {
        return response({
          ok: true,
          items: [{
            run_id: "h1",
            platform: "boss",
            status: "interrupted",
            created_at: "2026-08-11 10:00:00",
            total_scraped: 10,
            total_kept: 1,
            total_matched: 1,
            mismatch_count: 0,
            total_dropped: 9,
            pending_count: 0,
            keyword_summary: "Python 后端 / 上海",
            profile_summary_preview: "3年Python后端",
            archived_at: null,
            is_latest: true,
          }],
        });
      }
      if (url.endsWith("/api/result-history/h1")) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "h1",
          platform: "boss",
          status: "interrupted",
          started_at: 1_720_000_000_000,
          finished_at: 1_720_000_036_000,
          result: {
            jobs: [{ job_id: "j1", platform: "boss", verdict: "match", title: "历史岗位" }],
            total_kept: 1,
            total_dropped: 9,
            profile_summary: "完整画像文本",
          },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    expect(wrapper.find('[data-testid="history-drawer"]').exists()).toBe(true);

    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="history-round-marker"]').text()).toContain("失败但有 1 个岗位");
    expect(wrapper.find('[data-testid="result-platform-filter"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="recrawl-uncertain"]').exists()).toBe(false);
    expect(wrapper.findAll("button").some((button) => button.text().includes("补抓 JD"))).toBe(false);
    expect(wrapper.find('[data-testid="history-round-profile"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="history-round-profile-body"]').exists()).toBe(false);

    await wrapper.get('[data-testid="history-round-profile"]').trigger("mouseenter");
    await nextTick();
    expect(wrapper.get('[data-testid="history-round-profile-body"]').text()).toContain("完整画像文本");
    expect(wrapper.find('[data-testid="platform-current-boss"]').exists()).toBe(true);

    const status = wrapper.emitted("round-status")?.flat().at(-1);
    expect(status).toMatchObject({ scope: "history", platform: "boss", phase: "judged", judged: 1 });

    await wrapper.get('[data-testid="back-to-latest"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="latest-result-empty"]').exists()).toBe(true);

    vi.unstubAllGlobals();
  });

  it("binds zhilian history to zhilian mode and brand color", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/advanced-settings")) {
        return response({
          ok: true,
          selection: "balanced",
          settings,
          last_custom: null,
          mode_version: null,
          manual_ranges: {},
          config_schema_version: 1,
        });
      }
      if (url.includes("/api/filter-labels")) {
        return response({ ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
      }
      if (url.includes("/api/options")) {
        return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
      }
      if (url.endsWith("/api/latest-running-task")) {
        return response({ ok: true, has_task: false });
      }
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url.endsWith("/api/result-history")) {
        return response({
          ok: true,
          items: [{
            run_id: "hz",
            platform: "zhilian",
            status: "done",
            created_at: "2026-08-11 11:00:00",
            total_scraped: 8,
            total_kept: 2,
            total_matched: 2,
            total_dropped: 6,
            pending_count: 0,
            keyword_summary: "前端 / 北京",
            profile_summary_preview: "5年前端",
            archived_at: null,
            is_latest: true,
          }],
        });
      }
      if (url.endsWith("/api/result-history/hz")) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "hz",
          platform: "zhilian",
          status: "done",
          started_at: 1_720_000_000_000,
          finished_at: 1_720_000_036_000,
          result: {
            jobs: [{ job_id: "z1", platform: "zhilian", verdict: "match", title: "智联历史岗位" }],
            total_kept: 2,
            total_dropped: 6,
            profile_summary: "智联画像文本",
          },
        });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-1" } });
    await flushPromises();
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();

    expect(document.documentElement.getAttribute("data-platform")).toBe("zhilian");
    expect(wrapper.find('[data-testid="platform-current-zhilian"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="platform-segment-zhilian"]').attributes("aria-selected")).toBe("true");

    vi.unstubAllGlobals();
  });
});
