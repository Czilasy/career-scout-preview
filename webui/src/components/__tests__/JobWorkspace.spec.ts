import { mount } from "@vue/test-utils";
import JobWorkspace from "../JobWorkspace.vue";
import type { JobItem } from "../../types";

function job(overrides: Partial<JobItem> = {}): JobItem {
  return {
    job_id: "j1",
    title: "Python 后端",
    company: "测试公司",
    salary: "20-40K",
    location: "上海 · 浦东新区",
    ...overrides,
  };
}

describe("JobWorkspace job count", () => {

  it("shows job count without location summary", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ location: "上海" })],
        emptyMessage: "暂无岗位",
      },
    });
    expect(wrapper.find('[data-testid="location-summary"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("1 个岗位");
  });

  it("removes empty location dots from job cards and detail", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ location: "东莞··" })],
        emptyMessage: "暂无岗位",
      },
    });
    const rows = wrapper.findAll('[data-testid="job-row"]');
    expect(rows[0].text()).toContain("东莞");
    expect(rows[0].text()).not.toContain("··");
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("东莞");
    expect(wrapper.get('[data-testid="job-detail"]').text()).not.toContain("··");
  });
});

describe("JobWorkspace company insight buttons (B058/B065)", () => {
  it("shows insight buttons at the far right of the job facts row and keeps content in hover popovers", async () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [
          job({
            verdict: "match",
            verdict_reason: "技能匹配",
            caveats: ["优先英语六级，候选人未提供"],
          }),
        ],
        emptyMessage: "暂无岗位",
      },
    });
    const line = wrapper.get(".job-detail-facts");
    expect(line.findAll(".company-insight-button").map((button) => button.text())).toEqual([
      "AI 判断说明", "软性要求提醒",
    ]);
    expect(line.get(".ai-insight").attributes("data-platform")).toBe("boss");
    expect(wrapper.get(".jd-content").attributes("data-platform")).toBe("boss");
    expect(line.findAll(".company-insight-popover")).toHaveLength(2);
    expect(wrapper.find(".verdict-pair").exists()).toBe(false);
    await line.findAll(".company-insight")[0].trigger("mouseenter");
    expect(line.findAll(".company-insight-popover")[0].text()).toContain("技能匹配");
    expect(line.findAll(".ai-insight-list li")).toHaveLength(1);
    const softItems = line.findAll(".company-insight-popover")[1].findAll(".soft-insight-list li");
    expect(softItems.map((item) => item.text())).toEqual(["优先英语六级，候选人未提供"]);
  });

  it("binds the description divider to the selected job platform", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ platform: "zhilian", verdict: "match", verdict_reason: "技能匹配" })],
        emptyMessage: "暂无岗位",
      },
    });

    expect(wrapper.get(".jd-content").attributes("data-platform")).toBe("zhilian");
  });

  it("hides soft requirements for a not-matched job", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ verdict: "not_match", verdict_reason: "薪资不符", caveats: ["仅供参考"] })],
        emptyMessage: "暂无岗位",
      },
    });
    const line = wrapper.get(".job-detail-facts");
    expect(line.findAll(".company-insight-button").map((button) => button.text())).toEqual(["AI 判断说明"]);
    expect(line.text()).toContain("AI 判断说明");
    expect(line.text()).not.toContain("软性要求提醒");
  });

  it("does not show soft requirements when all reminder content is blank", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({
          verdict: "match",
          verdict_reason: "技能匹配",
          caveats: ["  "],
          flags: [{ code: "M1", level: "medium", reason: "" }],
        })],
        emptyMessage: "暂无岗位",
      },
    });

    expect(wrapper.get(".job-detail-facts").findAll(".company-insight-button").map((button) => button.text())).toEqual(["AI 判断说明"]);
  });
});
