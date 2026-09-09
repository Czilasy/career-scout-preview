import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import DiscoveryView from "../DiscoveryView.vue";
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

function commonResponse(url: string, withCompletedScreenTask = false): Response | null {
  if (url.endsWith("/api/latest-running-task")) {
    if (withCompletedScreenTask) {
      return response({
        ok: true,
        has_task: true,
        task_id: "screen-bootstrap",
        kind: "ai_screen",
        status: "running",
        platform: "boss",
        scrape_task_id: "scrape-bootstrap",
        scrape_completed: true,
        progress: { stage: "ai_fine", message: "AI 筛选中" },
        logs: [],
      });
    }
    return response({ ok: true, has_task: false });
  }
  if (url.includes("/api/task-state/screen-bootstrap")) {
    return response({ ok: true, status: "completed", progress: {}, logs: [], platform: "boss" });
  }
  if (url.includes("/api/filter-labels")) {
    return response({ ok: true, platform: "boss", schema_version: 1, enabled_for_new_tasks: true, fields: [] });
  }
  if (url.includes("/api/options")) {
    return response({ ok: true, platform: "boss", city_mapping_version: 1, cities: [] });
  }
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
  return null;
}

describe("Discovery recovery paths", () => {
  beforeEach(() => {
    setBuildIdentity(expectedBackendBuildHash);
    sessionStorage.clear();
    localStorage.clear();
  });

  it("shows the recrawl action only in the pending header and keeps it after dismissing the reminder", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        if (url.includes("platform=zhilian")) return response({ ok: true, has_result: false });
        return response({
          ok: true,
          has_result: true,
          source_run_id: "latest-boss-run",
          platform: "boss",
          status: "completed_with_pending",
          result: {
            jobs: [
              { job_id: "matched", platform: "boss", verdict: "match", title: "匹配岗位" },
              { job_id: "unmatched", platform: "boss", verdict: "not_match", title: "不匹配岗位" },
              { job_id: "uncertain", platform: "boss", verdict: "uncertain", title: "待确认岗位" },
            ],
            dropped: [{ job_id: "dropped", platform: "boss", title: "已筛除岗位" }],
            total_scraped: 4,
            total_kept: 3,
            total_matched: 1,
            total_dropped: 1,
            profile_summary: "3年后端开发",
          },
        });
      }
      if (url.endsWith("/api/pipeline/recrawl")) {
        return response({ ok: true, task_id: "header-recrawl" }, 202);
      }
      if (url.includes("/api/task-state/header-recrawl")) {
        return response({ status: "paused", progress: { message: "等待处理" }, logs: [] });
      }
      return commonResponse(url, true) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-header-recrawl" } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text().includes("待确认"))!.trigger("click");
    await nextTick();
    const headingAction = wrapper.get('[data-testid="pending-recrawl-heading"]');
    expect(headingAction.text()).toContain("全部重抓（1）");

    await wrapper.get('[data-testid="pending-recrawl-dismiss"]').trigger("click");
    await nextTick();
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="pending-recrawl-heading"]').exists()).toBe(true);

    await wrapper.findAll("button").find((button) => button.text().includes("匹配"))!.trigger("click");
    await nextTick();
    expect(wrapper.find('[data-testid="pending-recrawl-heading"]').exists()).toBe(false);

    await wrapper.findAll("button").find((button) => button.text().includes("已筛除"))!.trigger("click");
    await nextTick();
    expect(wrapper.find('[data-testid="pending-recrawl-heading"]').exists()).toBe(false);

    await wrapper.findAll("button").find((button) => button.text().includes("待确认"))!.trigger("click");
    await wrapper.get('[data-testid="pending-recrawl-heading"]').trigger("click");
    await flushPromises();

    const recrawlCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/pipeline/recrawl"));
    expect(recrawlCall).toBeTruthy();
    expect(JSON.parse(String(recrawlCall![1]?.body))).toMatchObject({
      source_run_id: "latest-boss-run",
      job_ids: ["uncertain"],
    });

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("allows the same pending header action in history and sends the selected history run as source", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        return response({ ok: true, has_result: false });
      }
      if (url.endsWith("/api/result-history")) {
        return response({
          ok: true,
          items: [{
            run_id: "history-pending-run",
            platform: "boss",
            status: "partial",
            created_at: "2026-08-11 10:00:00",
            total_scraped: 1,
            total_kept: 1,
            total_matched: 0,
            mismatch_count: 0,
            total_dropped: 0,
            pending_count: 1,
            keyword_summary: "后端 / 上海",
            profile_summary_preview: "3年后端开发",
            archived_at: "2026-08-12 10:00:00",
            is_latest: false,
          }],
        });
      }
      if (url.endsWith("/api/result-history/history-pending-run")) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "history-pending-run",
          platform: "boss",
          status: "partial",
          result: {
            jobs: [{
              job_id: "history-job",
              platform: "boss",
              verdict: "uncertain",
              title: "历史待确认岗位",
              jd: "岗位详情",
            }],
            dropped: [],
            total_scraped: 1,
            total_kept: 1,
            total_dropped: 0,
            profile_summary: "历史轮画像",
            profile_facts: { experience: "3年" },
          },
        });
      }
      if (url.endsWith("/api/pipeline/recrawl")) {
        return response({ ok: true, task_id: "history-recrawl" }, 202);
      }
      if (url.includes("/api/task-state/history-recrawl")) {
        return response({ status: "paused", progress: { message: "等待处理" }, logs: [] });
      }
      return commonResponse(url) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-history-recrawl" } });
    await flushPromises();
    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="pending-recrawl-heading"]').text()).toContain("全部重抓（1）");
    await wrapper.get('[data-testid="pending-recrawl-heading"]').trigger("click");
    await flushPromises();

    const recrawlCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/pipeline/recrawl"));
    expect(recrawlCall).toBeTruthy();
    expect(JSON.parse(String(recrawlCall![1]?.body))).toMatchObject({
      source_run_id: "history-pending-run",
      job_ids: ["history-job"],
    });

    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("does not render an empty latest results page while the latest result is loading after history", async () => {
    let holdLatest = false;
    let releaseLatest!: () => void;
    const latestGate = new Promise<void>((resolve) => { releaseLatest = resolve; });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/latest-pipeline-result")) {
        if (holdLatest) await latestGate;
        if (url.includes("platform=zhilian")) return response({ ok: true, has_result: false });
        return response({
          ok: true,
          has_result: true,
          source_run_id: "current-latest-run",
          platform: "boss",
          status: "completed_with_pending",
          result: {
            jobs: [{ job_id: "current-job", platform: "boss", verdict: "uncertain", title: "当前最新岗位" }],
            dropped: [],
            total_scraped: 1,
            total_kept: 1,
            total_dropped: 0,
          },
        });
      }
      if (url.endsWith("/api/result-history")) {
        return response({
          ok: true,
          items: [{
            run_id: "old-history-run",
            platform: "boss",
            status: "done",
            created_at: "2026-08-11 10:00:00",
            total_scraped: 1,
            total_kept: 1,
            total_matched: 1,
            mismatch_count: 0,
            total_dropped: 0,
            pending_count: 0,
            keyword_summary: "后端 / 上海",
            profile_summary_preview: "历史画像",
            archived_at: "2026-08-12 10:00:00",
            is_latest: false,
          }],
        });
      }
      if (url.endsWith("/api/result-history/old-history-run")) {
        return response({
          ok: true,
          has_result: true,
          source_run_id: "old-history-run",
          platform: "boss",
          status: "done",
          result: {
            jobs: [{ job_id: "old-job", platform: "boss", verdict: "match", title: "历史岗位" }],
            dropped: [],
            total_scraped: 1,
            total_kept: 1,
            total_matched: 1,
            total_dropped: 0,
          },
        });
      }
      return commonResponse(url, true) || response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DiscoveryView, { props: { profileId: "profile-return-latest" } });
    await flushPromises();
    expect(wrapper.text()).toContain("当前最新岗位");

    (wrapper.vm as unknown as { openHistoryDrawer(): void }).openHistoryDrawer();
    await flushPromises();
    await wrapper.get('[data-testid="history-round-row"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="history-round-marker"]')).toBeTruthy();

    holdLatest = true;
    const returnClick = wrapper.get('[data-testid="back-to-latest"]').trigger("click");
    await nextTick();

    expect(wrapper.find('[data-testid="latest-result-empty"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(true);

    releaseLatest();
    await returnClick;
    await flushPromises();
    expect(wrapper.find('[data-testid="latest-result-empty"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("当前最新岗位");
    expect(wrapper.find('[data-testid="history-round-marker"]').exists()).toBe(false);

    wrapper.unmount();
    vi.unstubAllGlobals();
  });
});
