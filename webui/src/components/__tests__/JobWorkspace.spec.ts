import { mount } from "@vue/test-utils";
import JobWorkspace from "../JobWorkspace.vue";

const jobs = Array.from({ length: 75 }, (_, index) => ({
  job_id: `job-${index + 1}`,
  title: `岗位 ${index + 1}`,
  company: `公司 ${index + 1}`,
  salary: "20-30K",
  location: "上海",
  jd: `岗位描述 ${index + 1}`,
  verdict: index % 2 === 0 ? "match" : "not_match",
}));

describe("JobWorkspace", () => {
  it("renders large result sets in batches with a single detail panel", async () => {
    const wrapper = mount(JobWorkspace, {
      props: { jobs, batchSize: 30, emptyMessage: "暂无岗位" },
    });

    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(30);
    expect(wrapper.findAll('[data-testid="job-detail"]')).toHaveLength(1);
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("岗位 1");

    await wrapper.get('[data-testid="load-more"]').trigger("click");

    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(60);
    expect(wrapper.findAll('[data-testid="job-detail"]')).toHaveLength(1);
  });
});
