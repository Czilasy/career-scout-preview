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

describe("JobWorkspace verdict pair (B058)", () => {
  it("wraps verdict reason and soft-requirement blocks in .verdict-pair", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [
          job({
            verdict_reason: "技能匹配",
            caveats: ["优先英语六级，候选人未提供"],
          }),
        ],
        emptyMessage: "暂无岗位",
      },
    });
    const pair = wrapper.find(".verdict-pair");
    expect(pair.exists()).toBe(true);
    expect(pair.find(".verdict-reason").exists()).toBe(true);
    expect(pair.find(".caveats-list").exists()).toBe(true);
    // 桌面双栏：两个区块是 .verdict-pair 的直接子元素，以便 grid 50/50 生效。
    expect(pair.element.children).toHaveLength(2);
    expect(pair.element.children[0].className).toContain("verdict-reason");
    expect(pair.element.children[1].className).toContain("caveats-list");
    expect(pair.text()).toContain("AI 判断说明");
    expect(pair.text()).toContain("软性要求提醒");
  });

  it("renders verdict reason block without soft requirements", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ verdict_reason: "技能匹配" })],
        emptyMessage: "暂无岗位",
      },
    });
    const pair = wrapper.find(".verdict-pair");
    expect(pair.exists()).toBe(true);
    expect(pair.find(".verdict-reason").exists()).toBe(true);
    expect(pair.find(".caveats-list").exists()).toBe(false);
  });
});
