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

function lastObserver(): { trigger: (v: boolean) => void } {
  const list = (globalThis as unknown as { __mockIntersectionObservers: any[] })
    .__mockIntersectionObservers;
  return list[list.length - 1];
}

describe("JobWorkspace", () => {
  it("renders large result sets in batches and expands via sentinel", async () => {
    const wrapper = mount(JobWorkspace, {
      props: { jobs, batchSize: 30, emptyMessage: "暂无岗位" },
    });

    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(30);
    expect(wrapper.findAll('[data-testid="job-detail"]')).toHaveLength(1);
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("岗位 1");
    // 无限滚动哨兵存在
    expect(wrapper.find(".load-sentinel").exists()).toBe(true);

    // 哨兵进入视口 → 自动加载下一批
    lastObserver().trigger(true);
    await wrapper.vm.$nextTick();

    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(60);
    expect(wrapper.findAll('[data-testid="job-detail"]')).toHaveLength(1);
  });

  it("only renders HTTPS links on BOSS-owned hosts", () => {
    for (const unsafeUrl of [
      "javascript:alert(1)",
      "http://www.zhipin.com/job_detail/unsafe.html",
      "https://zhipin.com.evil.example/job_detail/unsafe.html",
      "not-a-url",
    ]) {
      const wrapper = mount(JobWorkspace, {
        props: {
          jobs: [{ ...jobs[0], canonical_url: unsafeUrl }],
          emptyMessage: "暂无岗位",
        },
      });
      expect(wrapper.find('[data-testid="job-detail"] a').exists()).toBe(false);
      wrapper.unmount();
    }

    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [{
          ...jobs[0],
          canonical_url: "https://jobs.zhipin.com/job_detail/safe.html",
        }],
        emptyMessage: "暂无岗位",
      },
    });
    const link = wrapper.get('[data-testid="job-detail"] a');
    expect(link.attributes("href")).toBe("https://jobs.zhipin.com/job_detail/safe.html");
    expect(link.attributes("rel")).toBe("noopener noreferrer");
  });

  it("defers the mobile detail overlay until the user selects a paused job", async () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: jobs.slice(0, 2),
        emptyMessage: "暂无岗位",
        deferMobileDetail: true,
      },
    });
    expect(wrapper.get(".job-workspace").classes()).toContain("defer-mobile-detail");
    expect(wrapper.get(".job-workspace").classes()).not.toContain("detail-selected");

    await wrapper.get('[data-testid="job-row"]').trigger("click");
    expect(wrapper.get(".job-workspace").classes()).toContain("detail-selected");
  });

  it("T511: displays BOSS platform badge and validates BOSS URL host", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [{
          platform: "boss",
          platform_job_id: "boss-123",
          title: "BOSS 岗位",
          company: "BOSS 公司",
          canonical_url: "https://jobs.zhipin.com/job_detail/safe.html",
        }],
        emptyMessage: "暂无岗位",
      },
    });
    const badge = wrapper.get('[data-testid="job-platform-badge"]');
    expect(badge.text()).toBe("BOSS");
    expect(badge.attributes("data-platform")).toBe("boss");
    const link = wrapper.get('[data-testid="job-detail"] a');
    expect(link.attributes("href")).toBe("https://jobs.zhipin.com/job_detail/safe.html");
  });

  it("T511: displays 智联 platform badge and validates zhilian URL host + path", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [{
          platform: "zhilian",
          platform_job_id: "zhilian-456",
          title: "智联岗位",
          company: "智联公司",
          canonical_url: "https://www.zhaopin.com/jobdetail/CC123456.htm",
        }],
        emptyMessage: "暂无岗位",
      },
    });
    const badge = wrapper.get('[data-testid="job-platform-badge"]');
    expect(badge.text()).toBe("智联");
    expect(badge.attributes("data-platform")).toBe("zhilian");
    const link = wrapper.get('[data-testid="job-detail"] a');
    expect(link.attributes("href")).toBe("https://www.zhaopin.com/jobdetail/CC123456.htm");
  });

  it("T511: rejects zhilian URL with non-zhaopin host even if path looks valid", () => {
    // 不按 URL 猜平台 — job.platform 是后端权威来源（platform-schema.md L213）。
    // 声明 zhilian 但 URL host 是 zhipin.com → 拒绝。
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [{
          platform: "zhilian",
          platform_job_id: "zhilian-456",
          title: "智联岗位",
          canonical_url: "https://jobs.zhipin.com/jobdetail/CC123456.htm",
        }],
        emptyMessage: "暂无岗位",
      },
    });
    expect(wrapper.find('[data-testid="job-detail"] a').exists()).toBe(false);
  });

  it("T511: rejects zhilian URL with invalid path (no jobdetail/<id>.htm)", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [{
          platform: "zhilian",
          platform_job_id: "zhilian-456",
          title: "智联岗位",
          canonical_url: "https://www.zhaopin.com/not-a-jobdetail/CC123456.html",
        }],
        emptyMessage: "暂无岗位",
      },
    });
    expect(wrapper.find('[data-testid="job-detail"] a').exists()).toBe(false);
  });

  it("T511: upgrades zhilian http URL to https and strips query + fragment", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [{
          platform: "zhilian",
          platform_job_id: "zhilian-456",
          title: "智联岗位",
          canonical_url: "http://zhaopin.com/jobdetail/CC123456.htm?utm_source=x#frag",
        }],
        emptyMessage: "暂无岗位",
      },
    });
    const link = wrapper.get('[data-testid="job-detail"] a');
    expect(link.attributes("href")).toBe("https://zhaopin.com/jobdetail/CC123456.htm");
  });

  it("T511: displays experience, degree and extra labels (e.g. company_nature_label)", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [{
          platform: "zhilian",
          platform_job_id: "zhilian-789",
          title: "智联岗位",
          experience: "3-5年",
          degree: "本科",
          extra: { company_nature_label: "国企", industry_label: "互联网" },
        }],
        emptyMessage: "暂无岗位",
      },
    });
    expect(wrapper.get('[data-testid="job-experience"]').text()).toBe("3-5年");
    expect(wrapper.get('[data-testid="job-degree"]').text()).toBe("本科");
    const extras = wrapper.get('[data-testid="job-extra-facts"]');
    expect(extras.text()).toContain("公司性质: 国企");
    expect(extras.text()).toContain("行业: 互联网");
    // data-extra-key 用于联调阶段定位单个 extra 字段
    expect(wrapper.find('[data-extra-key="company_nature_label"]').exists()).toBe(true);
  });

  it("extra 原始键（company_size/industry）也映射为中文，不渲染英文", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [{
          platform: "zhilian",
          platform_job_id: "zhilian-extra",
          title: "智联岗位",
          extra: { company_size: "100-499人", industry: "互联网", company_nature_label: "国企" },
        }],
        emptyMessage: "暂无岗位",
      },
    });
    const extras = wrapper.get('[data-testid="job-extra-facts"]');
    expect(extras.text()).toContain("公司规模: 100-499人");
    expect(extras.text()).toContain("行业: 互联网");
    expect(extras.text()).toContain("公司性质: 国企");
    expect(extras.text()).not.toContain("company size");
    expect(extras.text()).not.toContain("industry:");
  });

  it("T511: uses (platform, platform_job_id) for jobKey — two jobs same platform_job_id different platform are distinct", () => {
    // jobKey 不再依赖 job_id（内部 UUID 跨平台可能冲突）；
    // 两个岗位 platform_job_id 相同但 platform 不同 → Vue v-for :key 不同 → 都渲染。
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [
          { platform: "boss", platform_job_id: "shared-1", title: "BOSS 岗位" },
          { platform: "zhilian", platform_job_id: "shared-1", title: "智联岗位" },
        ],
        emptyMessage: "暂无岗位",
      },
    });
    const rows = wrapper.findAll('[data-testid="job-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("BOSS 岗位");
    expect(rows[1].text()).toContain("智联岗位");
  });

  it("renders heading-actions slot in the heading right area", () => {
    const wrapper = mount(JobWorkspace, {
      props: { jobs: [jobs[0]], emptyMessage: "暂无岗位" },
      slots: { "heading-actions": '<button data-testid="recrawl-btn">全部重抓</button>' },
    });
    expect(wrapper.find('[data-testid="job-list-mode"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="recrawl-btn"]').exists()).toBe(true);
  });
});
