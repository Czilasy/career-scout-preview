import { mount } from "@vue/test-utils";
import JobWorkspace from "../JobWorkspace.vue";
import type { JobItem } from "../../types";

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

  it("R3: keeps the platform filter visible and shows the platform empty state when a platform has no jobs", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [],
        platformFilter: "zhilian",
        emptyMessage: "没有明确匹配的岗位",
      },
    });
    expect(wrapper.find('[data-testid="result-platform-filter"]').exists()).toBe(true);
    const empty = wrapper.get('[data-testid="job-workspace-empty"]');
    expect(empty.text()).toContain("该平台暂无数据");
    expect(empty.text()).toContain("切回「全部」");
  });

  it("R3: keeps the platform filter visible and falls back to the category message on the all filter", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [],
        platformFilter: "all",
        emptyMessage: "没有明确匹配的岗位",
      },
    });
    expect(wrapper.find('[data-testid="result-platform-filter"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="job-workspace-empty"]').text()).toContain("没有明确匹配的岗位");
  });

  // -------------------------------------------------------------------------
  // 筛选 / 排序（列表头部工具）
  // -------------------------------------------------------------------------

  const filterableJobs: JobItem[] = [
    {
      job_id: "f1", title: "资深后端", company: "A", salary: "25-35K",
      experience: "3-5年", degree: "本科", jd: "五险一金，周末双休",
      platform: "boss",
    },
    {
      job_id: "f2", title: "初级前端", company: "B", salary: "6-8K",
      experience: "应届生", degree: "大专", jd: "欢迎应届生",
      platform: "boss",
    },
    {
      job_id: "f3", title: "测试实习生", company: "C", salary: "200-300元/天",
      experience: "", degree: "本科", jd: "",
      platform: "boss",
    },
  ];

  it("renders filter and sort buttons only when there are jobs", () => {
    const empty = mount(JobWorkspace, { props: { jobs: [], emptyMessage: "暂无" } });
    expect(empty.find('[data-testid="result-filter-toggle"]').exists()).toBe(false);
    expect(empty.find('[data-testid="result-sort-toggle"]').exists()).toBe(false);

    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无" } });
    expect(wrapper.find('[data-testid="result-filter-toggle"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="result-sort-toggle"]').exists()).toBe(true);
  });

  it("opens the filter panel, applies a salary band and filters the list", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无" } });
    expect(wrapper.find('[data-testid="result-filter-panel"]').exists()).toBe(false);

    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    const panel = wrapper.get('[data-testid="result-filter-panel"]');
    expect(wrapper.find('[data-testid="result-filter-panel"]').exists()).toBe(true);

    // 选择「15K以上」（25-35K 下限 25 → 15up）
    const over15 = panel.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "15K以上")!;
    await over15.trigger("click");
    await wrapper.get('[data-testid="filter-apply"]').trigger("click");
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll('[data-testid="job-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain("资深后端");
    // 按钮显示「筛选 1」
    expect(wrapper.get('[data-testid="result-filter-label"]').text()).toBe("筛选 1");
    // 面板已关闭
    expect(wrapper.find('[data-testid="result-filter-panel"]').exists()).toBe(false);
  });

  it("sentinel 不限 is mutually exclusive with concrete options and selected by default", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无" } });
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    const panel = wrapper.get('[data-testid="result-filter-panel"]');

    // 经验组：默认 sentinel「不限」选中
    const sentinel = panel.findAll('[data-testid="filter-sentinel"]').find((chip) => chip.text() === "不限")!;
    expect(sentinel.classes()).toContain("selected");

    // 点「应届生」→ sentinel 取消（互斥）
    await panel.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "应届生")!.trigger("click");
    expect(sentinel.classes()).not.toContain("selected");

    // 再点 sentinel → 具体选项全部取消（回到组无约束）
    await sentinel.trigger("click");
    expect(sentinel.classes()).toContain("selected");
    expect(
      panel.findAll('[data-testid="filter-option"]').some((chip) => chip.text() === "应届生" && chip.classes().includes("selected")),
    ).toBe(false);
  });

  it("reset clears the draft and closes the panel; closing without 确定 keeps the applied filter", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无" } });
    // 先应用一个筛选（15K以上）
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    let panel = wrapper.get('[data-testid="result-filter-panel"]');
    await panel.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "15K以上")!.trigger("click");
    await wrapper.get('[data-testid="filter-apply"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(1);

    // 重新打开面板：选中「本科」但不点确定，直接关面板 → 应用状态不变
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    panel = wrapper.get('[data-testid="result-filter-panel"]');
    await panel.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "本科")!.trigger("click");
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(1);
    expect(wrapper.get('[data-testid="result-filter-label"]').text()).toBe("筛选 1");

    // 点「重置」→ 草稿清空、面板关闭、列表恢复全量、徽标消失
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.get('[data-testid="filter-reset"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="result-filter-panel"]').exists()).toBe(false);
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(3);
    expect(wrapper.find('[data-testid="result-filter-label"]').exists()).toBe(false);
  });

  it("filtered-out result shows a dedicated empty state with a clear-filter button", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "没有明确匹配的岗位" } });
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    const panel = wrapper.get('[data-testid="result-filter-panel"]');
    await panel.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "硕士")!.trigger("click");
    await wrapper.get('[data-testid="filter-apply"]').trigger("click");
    await wrapper.vm.$nextTick();

    const empty = wrapper.get('[data-testid="job-workspace-empty"]');
    expect(empty.text()).toContain("没有符合条件的岗位");
    // 「清除筛选」按钮恢复全量
    await wrapper.get('[data-testid="clear-filter"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(3);
    expect(wrapper.find('[data-testid="result-filter-label"]').exists()).toBe(false);
  });

  it("original empty list keeps the existing empty state without a clear button", () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: [], emptyMessage: "没有明确匹配的岗位" } });
    expect(wrapper.get('[data-testid="job-workspace-empty"]').text()).toContain("没有明确匹配的岗位");
    expect(wrapper.find('[data-testid="clear-filter"]').exists()).toBe(false);
  });

  it("sort menu reorders by salary desc, keeps 面议 last and disables unpublished sorts", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无" } });
    await wrapper.get('[data-testid="result-sort-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    const menu = wrapper.get('[data-testid="result-sort-menu"]');
    const options = menu.findAll('[data-testid="sort-option"]');
    expect(options.map((option) => option.text().trim())).toEqual([
      "综合排序", "薪资最高", "薪资最低", "最新发布", "匹配度最高",
    ]);
    // 最新发布 / 匹配度最高：置灰 disabled + 提示，点击无效果
    expect(options[3].attributes("disabled")).toBeDefined();
    expect(options[4].attributes("disabled")).toBeDefined();
    expect(options[3].attributes("title")).toBe("数据补齐后开放");
    await options[3].trigger("click");
    await wrapper.vm.$nextTick();
    // 置灰项点击无效：菜单仍打开、列表顺序不变（综合排序）
    expect(wrapper.find('[data-testid="result-sort-menu"]').exists()).toBe(true);
    expect(wrapper.findAll('[data-testid="job-row"]')[0].text()).toContain("资深后端");

    await options[1].trigger("click");
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll('[data-testid="job-row"]');
    expect(rows).toHaveLength(3);
    // 25-35K → 6-8K → 200-300元/天（无法解析，恒沉底）
    expect(rows[0].text()).toContain("资深后端");
    expect(rows[1].text()).toContain("初级前端");
    expect(rows[2].text()).toContain("测试实习生");
    expect(wrapper.get('[data-testid="result-sort-label"]').text()).toBe("薪资最高");
  });

  it("selecting sort keeps the first visible job in sync with the detail pane", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无" } });
    await wrapper.get('[data-testid="result-sort-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    // 薪资最低（index 2）
    await wrapper.findAll('[data-testid="sort-option"]')[2].trigger("click");
    await wrapper.vm.$nextTick();
    // 薪资最低：6-8K 排第一 → 详情同步为「初级前端」
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("初级前端");
    // 第一行选中态
    expect(wrapper.findAll('[data-testid="job-row"]')[0].classes()).toContain("selected");
  });

  it("opening the sort menu closes the filter panel and vice versa", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无" } });
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="result-filter-panel"]').exists()).toBe(true);

    await wrapper.get('[data-testid="result-sort-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="result-filter-panel"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="result-sort-menu"]').exists()).toBe(true);
  });

  it("resultEpoch change resets filter and sort state (D3: jobs content change keeps state)", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无", resultEpoch: 1 } });
    // 应用筛选 + 排序
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    const panel = wrapper.get('[data-testid="result-filter-panel"]');
    await panel.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "15K以上")!.trigger("click");
    await wrapper.get('[data-testid="filter-apply"]').trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.get('[data-testid="result-sort-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.findAll('[data-testid="sort-option"]')[1].trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(1);
    expect(wrapper.get('[data-testid="result-sort-label"]').text()).toBe("薪资最高");

    // 新结果加载（resultEpoch 递增）→ 筛选/排序全部回到默认
    await wrapper.setProps({ resultEpoch: 2 });
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(3);
    expect(wrapper.find('[data-testid="result-filter-label"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="result-sort-label"]').exists()).toBe(false);
  });

  it("D3: switching category/platform (jobs prop change) keeps filter and sort state", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无", resultEpoch: 1 } });
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    const panel = wrapper.get('[data-testid="result-filter-panel"]');
    await panel.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "15K以上")!.trigger("click");
    await wrapper.get('[data-testid="filter-apply"]').trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.get('[data-testid="result-sort-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.findAll('[data-testid="sort-option"]')[1].trigger("click");
    await wrapper.vm.$nextTick();

    // 切分类/切平台：jobs 换成另一组岗位（结果集合变化，但 resultEpoch 不变）
    const otherJobs: JobItem[] = [
      { job_id: "g1", title: "另一组资深", company: "D", salary: "30-40K", experience: "5-10年", degree: "硕士", platform: "boss" },
      { job_id: "g2", title: "另一组初级", company: "E", salary: "4-6K", experience: "1-3年", degree: "本科", platform: "boss" },
      { job_id: "g3", title: "智联岗位", company: "F", salary: "面议", experience: "", degree: "", platform: "zhilian" },
    ];
    await wrapper.setProps({ jobs: otherJobs });
    await wrapper.vm.$nextTick();
    // 状态保留：徽标还在、排序标签还在，新列表按原条件收窄（30-40K 落 15up）
    expect(wrapper.get('[data-testid="result-filter-label"]').text()).toBe("筛选 1");
    expect(wrapper.get('[data-testid="result-sort-label"]').text()).toBe("薪资最高");
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(1);
    expect(wrapper.findAll('[data-testid="job-row"]')[0].text()).toContain("另一组资深");

    // 只有 resultEpoch 递增才重置
    await wrapper.setProps({ resultEpoch: 2 });
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="result-filter-label"]').exists()).toBe(false);
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(3);
  });

  it("filters by welfare from extra.welfare_list at the component level", async () => {
    const jobs: JobItem[] = [
      { job_id: "w1", title: "带双休", company: "A", salary: "10-15K", experience: "3-5年", degree: "本科", platform: "boss", extra: { welfare_list: ["五险一金", "双休"] } },
      { job_id: "w2", title: "仅五险一金", company: "B", salary: "8-10K", experience: "1-3年", degree: "大专", platform: "boss", extra: { welfare_list: ["五险一金"] } },
      { job_id: "w3", title: "智联无福利", company: "C", salary: "12-15K", experience: "", degree: "", platform: "zhilian", extra: {} },
    ];
    const wrapper = mount(JobWorkspace, { props: { jobs, emptyMessage: "暂无" } });
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    const panel = wrapper.get('[data-testid="result-filter-panel"]');
    // 福利组提示始终显示
    expect(panel.text()).toContain("智联岗位暂不支持福利筛选");
    await panel.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "双休")!.trigger("click");
    await wrapper.get('[data-testid="filter-apply"]').trigger("click");
    await wrapper.vm.$nextTick();
    const rows = wrapper.findAll('[data-testid="job-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain("带双休");
    // 智联岗位（无 welfare_list）被过滤属预期行为
  });

  it("keeps a manually selected job when it survives filtering; falls back to the first row when filtered out", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无" } });
    // 手动选中「初级前端」（列表第二项）
    await wrapper.findAll('[data-testid="job-row"]')[1].trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("初级前端");

    // 应用筛选：6-8K → 5-10 档，初级前端仍在 → 保持原选中
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    const panel = wrapper.get('[data-testid="result-filter-panel"]');
    await panel.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "5-10K")!.trigger("click");
    await wrapper.get('[data-testid="filter-apply"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(1);
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("初级前端");

    // 换条件：15K以上 → 初级前端被过滤 → 回退到过滤后第一项（资深后端）
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    const panel2 = wrapper.get('[data-testid="result-filter-panel"]');
    await panel2.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "5-10K")!.trigger("click");
    await panel2.findAll('[data-testid="filter-option"]').find((chip) => chip.text() === "15K以上")!.trigger("click");
    await wrapper.get('[data-testid="filter-apply"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(1);
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("资深后端");
  });

  it("a fresh result with no manual selection follows the first row when a filter is applied", async () => {
    const wrapper = mount(JobWorkspace, { props: { jobs: filterableJobs, emptyMessage: "暂无", resultEpoch: 1 } });
    // 新结果加载（resultEpoch 递增）→ 未手动选择
    await wrapper.setProps({ resultEpoch: 2 });
    await wrapper.vm.$nextTick();
    // 应用筛选（薪资最低 6-8K → 初级前端排第一）→ 选中跟随第一项
    await wrapper.get('[data-testid="result-sort-toggle"]').trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.findAll('[data-testid="sort-option"]')[2].trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("初级前端");
    expect(wrapper.findAll('[data-testid="job-row"]')[0].classes()).toContain("selected");
  });

  it("B033: high flag marks detail box red and shows ⚠ before reason", () => {
    const flagged = [{
      job_id: "job-h",
      title: "高薪岗位",
      company: "某公司",
      salary: "20-30K",
      location: "上海",
      jd: "岗位描述",
      verdict: "not_match",
      verdict_reason: "疑似骗局：要求先交培训费",
      flags: [{ code: "C1", level: "high", reason: "要求先交培训费" }],
    }] as JobItem[];
    const wrapper = mount(JobWorkspace, { props: { jobs: flagged, emptyMessage: "暂无" } });
    const box = wrapper.get(".verdict-reason");
    expect(box.classes()).toContain("flag-danger");
    expect(box.text()).toContain("⚠");
    expect(box.text()).toContain("要求先交培训费");
    expect(wrapper.findAll('[data-testid^="flag-high-"]')).toHaveLength(1);
    // 岗位条标题前 ⚠（红色）
    expect(wrapper.get(".job-row-flag").classes()).toContain("high");
  });

  it("B033: medium flags render in soft-requirements box with yellow ⚠ and no row mark conflict", () => {
    const flagged = [{
      job_id: "job-m",
      title: "销售岗",
      company: "某公司",
      salary: "8-12K",
      location: "上海",
      jd: "岗位描述",
      verdict: "match",
      verdict_reason: "合适",
      caveats: ["优先英语六级"],
      flags: [
        { code: "B1", level: "medium", reason: "标题含无责底薪" },
        { code: "F3", level: "medium", reason: "试用期未写明" },
      ],
    }] as JobItem[];
    const wrapper = mount(JobWorkspace, { props: { jobs: flagged, emptyMessage: "暂无" } });
    const box = wrapper.get(".caveats-list");
    expect(box.classes()).toContain("flag-unsure");
    expect(box.text()).toContain("标题含无责底薪");
    expect(wrapper.findAll('[data-testid^="flag-medium-"]')).toHaveLength(2);
    // 岗位条 ⚠ 为黄色（medium）
    expect(wrapper.get(".job-row-flag").classes()).toContain("medium");
    // AI 判断说明盒子不高危标红
    expect(wrapper.get(".verdict-reason").classes()).not.toContain("flag-danger");
  });

  it("B033: jobs without flags render no ⚠ mark anywhere", () => {
    const plainJobs = [{ ...jobs[0], verdict_reason: "技能契合" }] as JobItem[];
    const wrapper = mount(JobWorkspace, { props: { jobs: plainJobs, emptyMessage: "暂无" } });
    expect(wrapper.find(".job-row-flag").exists()).toBe(false);
    expect(wrapper.find(".flag-reason").exists()).toBe(false);
    expect(wrapper.get(".verdict-reason").classes()).not.toContain("flag-danger");
  });
});
