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
