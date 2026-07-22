import { flushPromises, mount } from "@vue/test-utils";
import ScreeningView from "../ScreeningView.vue";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ScreeningView", () => {
  beforeEach(() => localStorage.clear());

  it("executes the existing screening run and loads every result zone", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/screening/filter-options")) {
        return response({
          options: {
            city: [{ label: "不限", value: "" }, { label: "上海", value: "101020100" }],
            salary: [{ label: "不限", value: "" }],
            experience: [{ label: "不限", value: "" }],
            degree: [{ label: "不限", value: "" }],
            scale: [{ label: "不限", value: "" }],
            stage: [{ label: "不限", value: "" }],
            industry: [{ label: "不限", value: "" }],
          },
        });
      }
      if (url.includes("/api/screening/interested?")) return response({ items: [], count: 0 });
      if (url.includes("/api/screening/trash?")) return response({ items: [], count: 0 });
      if (url.endsWith("/api/screening/runs") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          keyword: "Python 后端",
          filters: { city: "101020100" },
          pages: 1,
          max_details: 3,
          profile_id: "profile-1",
        });
        return response({ run_id: "run-1", status: "queued" }, 202);
      }
      if (url.endsWith("/api/screening/runs/run-1")) {
        return response({
          run_id: "run-1",
          status: "succeeded",
          source_count: 2,
          processed_count: 2,
          match_count: 1,
          mismatch_count: 1,
          pending_count: 0,
          parse_failure_count: 0,
        });
      }
      if (url.includes("/api/screening/runs/run-1/matches")) {
        return response({ items: [{ job_id: "m-1", title: "Python 后端", company: "甲公司" }], count: 1 });
      }
      if (url.includes("/api/screening/runs/run-1/mismatches")) {
        return response({ items: [{ job_id: "n-1", title: "Java 后端", company: "乙公司" }], count: 1 });
      }
      if (url.endsWith("/api/screening/runs/run-1/pending")) {
        return response({ items: [], count: 0 });
      }
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ScreeningView, { props: { profileId: "profile-1" } });
    await flushPromises();

    await wrapper.get('[data-testid="screening-keyword"]').setValue("Python 后端");
    await wrapper.get('[data-testid="filter-city"]').setValue("101020100");
    await wrapper.get('[data-testid="start-screening"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-zone-tab="match"]').text()).toContain("1");
    expect(wrapper.get('[data-testid="job-row"]').text()).toContain("Python 后端");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/screening/runs",
      expect.objectContaining({ method: "POST" }),
    );

    vi.unstubAllGlobals();
  });
});
